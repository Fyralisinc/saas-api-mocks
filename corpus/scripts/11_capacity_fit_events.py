#!/usr/bin/env python3
"""Build a Fyralis-capacity-fit derivative of corpus/build/events.jsonl.

The source corpus (x) remains full fidelity. This script writes a reversible
derivative (y) that summarizes only items that overflow current Fyralis per-item
capacity. Summaries are cached by source-content hash so repeated prepares are
cheap and stable.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import textwrap
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT / "build" / "events.jsonl"
DEFAULT_OUTPUT = ROOT / "build" / "events.capacity_fit.jsonl"
DEFAULT_CACHE = ROOT / "cache" / "capacity_fit"
DEFAULT_REPORT = ROOT / "build" / "capacity_fit_report.json"

PROFILE_NAME = "fyralis-ollama-think-2026-06-16"

# Pinned from latest Fyralis main plus local Ollama probe:
# - Ollama /api/embeddings with nomic-embed-text fails at 6139 chars for a
#   repeated test string on the local model; use 5500 as a conservative budget.
# - Think prompt has _PER_ITEM_CHAR_LIMIT = 1500.
# - Notion block handler exposes text[:200] in content_text, so summary blocks
#   stay below that to preserve their whole meaning in the visible item.
EMBED_TEXT_CHAR_BUDGET = 5500
THINK_ITEM_CHAR_BUDGET = 1500
TEXT_SUMMARY_TARGET = 1400
NOTION_SUMMARY_TARGET = 4600
NOTION_BLOCK_CHAR_BUDGET = 180
NOTION_MAX_BLOCKS = 24

OVERSIZE_TEXT_KEYS = {
    ("drive", "file.create", "body"),
    ("drive", "file.update", "body"),
    ("gmail", "message", "body"),
    ("slack", "message", "text"),
    ("discord", "message", "text"),
    ("discord", "message", "content"),
    ("github", "commit", "message"),
    ("github", "review.submit", "body"),
    ("jira", "comment", "body"),
}


@dataclass(frozen=True)
class Section:
    level: int
    title: str
    body: str
    order: int


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _norm_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _clip(text: str, limit: int) -> str:
    text = _norm_space(text)
    if len(text) <= limit:
        return text
    cut = max(
        text.rfind(". ", 0, limit - 3),
        text.rfind("; ", 0, limit - 3),
        text.rfind(", ", 0, limit - 3),
        text.rfind(" ", 0, limit - 3),
    )
    if cut < max(60, limit // 3):
        cut = limit - 3
    return text[:cut].rstrip(" ,;:.") + "..."


def _sentences(text: str) -> list[str]:
    compact = _norm_space(re.sub(r"```.*?```", " ", text, flags=re.S))
    if not compact:
        return []
    out = re.split(r"(?<=[.!?])\s+", compact)
    return [s.strip() for s in out if s.strip()]


def _bullets(text: str) -> list[str]:
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^([-*+]|\d+[.)])\s+", stripped):
            out.append(re.sub(r"^([-*+]|\d+[.)])\s+", "", stripped).strip())
    return out


def _refs(text: str) -> list[str]:
    found: list[str] = []
    patterns = [
        r"`([^`]{2,80})`",
        r"\b(?:repo|product|person|team|project|issue|customer):[A-Za-z0-9_.:/-]+",
    ]
    seen: set[str] = set()
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            ref = match.group(1) if match.lastindex else match.group(0)
            if ref not in seen:
                seen.add(ref)
                found.append(ref)
            if len(found) >= 18:
                return found
    return found


def _sections(markdown: str) -> list[Section]:
    lines = markdown.replace("\r\n", "\n").splitlines()
    sections: list[Section] = []
    current_level = 0
    current_title = "Overview"
    current_lines: list[str] = []
    order = 0

    def flush() -> None:
        nonlocal order, current_lines
        body = "\n".join(current_lines).strip()
        if current_title != "Overview" or body:
            sections.append(Section(current_level, current_title, body, order))
            order += 1
        current_lines = []

    for line in lines:
        match = re.match(r"^(#{1,4})\s+(.+?)\s*$", line)
        if match:
            flush()
            current_level = len(match.group(1))
            current_title = match.group(2).strip()
            continue
        current_lines.append(line)
    flush()
    return sections


def _section_summary(section: Section, *, line_limit: int) -> str:
    title = _norm_space(section.title)
    body = section.body.strip()
    if not body:
        return ""
    bullets = _bullets(body)
    sentences = _sentences(body)
    lower = title.lower()

    if lower in {"summary", "overview", "executive summary"}:
        summary = " ".join(sentences[:3]) if sentences else body
        return f"{title}: {_clip(summary, line_limit)}"

    if any(word in lower for word in (
        "open question", "decision", "action", "next step", "recommendation",
        "root cause", "impact", "resolution", "motivation", "risk",
        "drawback", "alternative",
    )):
        if bullets:
            summary = "; ".join(_clip(b, 95) for b in bullets[:4])
        else:
            summary = " ".join(sentences[:2]) if sentences else body
        return f"{title}: {_clip(summary, line_limit)}"

    if bullets:
        summary = "; ".join(_clip(b, 90) for b in bullets[:3])
    else:
        summary = " ".join(sentences[:2]) if sentences else body
    return f"{title}: {_clip(summary, line_limit)}"


def summarize_markdown(
    text: str,
    *,
    fallback_title: str,
    target_chars: int,
) -> str:
    sections = _sections(text)
    doc_title = fallback_title.strip() or "Untitled"
    for section in sections:
        if section.level == 1 and section.title.strip():
            doc_title = section.title.strip()
            break

    refs = _refs(text)
    section_titles = [
        s.title.strip() for s in sections
        if s.level > 1 and s.title.strip()
    ]

    for line_limit in (300, 230, 170, 130):
        lines = [f"# {doc_title}", "", "Capacity-fit summary preserving the original item meaning."]
        for section in sections:
            if section.level == 1:
                continue
            rendered = _section_summary(section, line_limit=line_limit)
            if rendered:
                lines.append(f"- {rendered}")
        if refs:
            lines.append("- Key refs: " + ", ".join(f"`{r}`" for r in refs[:14]))
        covered = ", ".join(section_titles[:20])
        if covered:
            lines.append("- Sections covered: " + _clip(covered, 420))
        summary = "\n".join(lines).strip() + "\n"
        if len(summary) <= target_chars:
            return summary

    # Last resort: keep all section names plus the lead summary.
    lead = ""
    for section in sections:
        if section.title.lower() in {"summary", "overview"} and section.body.strip():
            lead = _clip(" ".join(_sentences(section.body)[:3]), 900)
            break
    if not lead:
        lead = _clip(text, 900)
    headings = ", ".join(section_titles)
    summary = "\n".join([
        f"# {doc_title}",
        "",
        "- Summary: " + lead,
        "- Sections covered: " + _clip(headings, max(200, target_chars - len(lead) - 160)),
    ]).strip() + "\n"
    return summary[:target_chars].rstrip() + "\n"


def summarize_plain(text: str, *, label: str, target_chars: int) -> str:
    bullets = _bullets(text)
    sentences = _sentences(text)
    parts: list[str] = []
    if bullets:
        parts.extend(_clip(b, 160) for b in bullets[:8])
    else:
        parts.extend(_clip(s, 220) for s in sentences[:8])
    if not parts:
        parts = [_clip(text, target_chars - len(label) - 20)]
    out = f"{label} capacity-fit summary:\n" + "\n".join(
        f"- {part}" for part in parts if part
    )
    return _clip(out, target_chars)


def _split_block(line: str, limit: int) -> list[str]:
    line = _norm_space(line)
    if not line:
        return []
    chunks: list[str] = []
    rest = line
    while len(rest) > limit:
        cut = max(
            rest.rfind(". ", 0, limit),
            rest.rfind("; ", 0, limit),
            rest.rfind(", ", 0, limit),
            rest.rfind(" ", 0, limit),
        )
        if cut < max(40, limit // 3):
            cut = limit
        chunks.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        chunks.append(rest)
    return chunks


def summary_blocks(summary: str, *, limit: int, max_blocks: int) -> list[str]:
    blocks: list[str] = []
    for raw in summary.splitlines():
        line = raw.strip()
        if not line:
            continue
        line = re.sub(r"^#{1,4}\s+", "", line)
        line = re.sub(r"^[-*+]\s+", "", line)
        line = re.sub(r"\s+", " ", line).strip()
        if not line:
            continue
        blocks.extend(_split_block(line, limit))
    if len(blocks) <= max_blocks:
        return blocks
    kept = blocks[: max_blocks - 1]
    tail = "; ".join(blocks[max_blocks - 1 :])
    kept.append(_clip("Additional covered points: " + tail, limit))
    return kept


def exact_blocks(text: str, *, limit: int) -> list[str]:
    """Split source text into visible-size blocks without summarizing it."""
    blocks: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text.strip()):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        for line in paragraph.splitlines():
            line = _norm_space(line)
            if line:
                blocks.extend(_split_block(line, limit))
    return blocks


def body_preview_from_blocks(blocks: list[str], *, target_chars: int) -> str:
    lines: list[str] = []
    used = 0
    for block in blocks:
        line = f"- {block}"
        add = len(line) + (1 if lines else 0)
        if lines and used + add > target_chars:
            break
        lines.append(line)
        used += add
    return "\n".join(lines).strip() + ("\n" if lines else "")


def _cache_path(cache_dir: Path, *, kind: str, text: str) -> Path:
    return cache_dir / f"{kind}-{_sha(text)}.json"


def _cached_summary(
    cache_dir: Path,
    *,
    kind: str,
    text: str,
    title: str,
    target_chars: int,
    refresh: bool,
) -> str:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(cache_dir, kind=kind, text=text)
    if path.exists() and not refresh:
        cached = json.loads(path.read_text())
        summary = cached.get("summary")
        if isinstance(summary, str) and summary.strip():
            return summary
    if kind == "notion":
        summary = summarize_markdown(text, fallback_title=title, target_chars=target_chars)
    else:
        summary = summarize_plain(text, label=title or kind, target_chars=target_chars)
    path.write_text(
        json.dumps(
            {
                "profile": PROFILE_NAME,
                "kind": kind,
                "source_sha256": _sha(text),
                "source_chars": len(text),
                "summary_chars": len(summary),
                "summary": summary,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    return summary


def _event_label(event: dict[str, Any], line_no: int) -> str:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    return ":".join(
        str(x)
        for x in (
            line_no,
            event.get("provider", "?"),
            event.get("kind", "?"),
            payload.get("id") or payload.get("key") or payload.get("title") or "",
        )
    )


def fit_event(
    event: dict[str, Any],
    *,
    line_no: int,
    cache_dir: Path,
    refresh: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    out = copy.deepcopy(event)
    payload = out.get("payload")
    if not isinstance(payload, dict):
        return out, []

    provider = str(out.get("provider") or "")
    kind = str(out.get("kind") or "")
    changes: list[dict[str, Any]] = []

    if provider == "notion" and kind == "page.create":
        body = payload.get("body_md")
        if isinstance(body, str) and len(body) > THINK_ITEM_CHAR_BUDGET:
            title = str(payload.get("title") or payload.get("id") or "Notion page")
            full_summary = _cached_summary(
                cache_dir,
                kind="notion",
                text=body,
                title=title,
                target_chars=NOTION_SUMMARY_TARGET,
                refresh=refresh,
            )
            blocks = summary_blocks(
                full_summary,
                limit=NOTION_BLOCK_CHAR_BUDGET,
                max_blocks=NOTION_MAX_BLOCKS,
            )
            body_md = body_preview_from_blocks(
                blocks,
                target_chars=TEXT_SUMMARY_TARGET,
            )
            payload["body_md"] = body_md
            payload["body_blocks"] = blocks
            changes.append(
                {
                    "field": "payload.body_md",
                    "reason": "notion_body_over_think_item_budget",
                    "original_chars": len(body),
                    "summary_chars": len(full_summary),
                    "body_md_chars": len(body_md),
                    "blocks": len(blocks),
                    "max_block_chars": max((len(b) for b in blocks), default=0),
                    "source_sha256": _sha(body),
                }
            )
        elif isinstance(body, str) and len(body) > NOTION_BLOCK_CHAR_BUDGET:
            blocks = exact_blocks(body, limit=NOTION_BLOCK_CHAR_BUDGET)
            payload["body_blocks"] = blocks
            changes.append(
                {
                    "field": "payload.body_blocks",
                    "reason": "notion_body_split_for_block_visibility",
                    "original_chars": len(body),
                    "blocks": len(blocks),
                    "max_block_chars": max((len(b) for b in blocks), default=0),
                    "source_sha256": _sha(body),
                }
            )

    for p, k, key in OVERSIZE_TEXT_KEYS:
        if provider != p or kind != k:
            continue
        value = payload.get(key)
        if isinstance(value, str) and len(value) > THINK_ITEM_CHAR_BUDGET:
            title = f"{provider}.{kind}.{key}"
            summary = _cached_summary(
                cache_dir,
                kind=f"{provider}-{kind}-{key}".replace(".", "-"),
                text=value,
                title=title,
                target_chars=TEXT_SUMMARY_TARGET,
                refresh=refresh,
            )
            payload[key] = summary
            changes.append(
                {
                    "field": f"payload.{key}",
                    "reason": "text_over_think_item_budget",
                    "original_chars": len(value),
                    "summary_chars": len(summary),
                    "source_sha256": _sha(value),
                }
            )

    if changes:
        payload["_capacity_fit"] = {
            "profile": PROFILE_NAME,
            "line_label": _event_label(event, line_no),
            "budgets": {
                "embedding_text_chars": EMBED_TEXT_CHAR_BUDGET,
                "think_item_chars": THINK_ITEM_CHAR_BUDGET,
                "notion_block_chars": NOTION_BLOCK_CHAR_BUDGET,
            },
            "changes": changes,
        }
    return out, changes


def validate_fit(path: Path) -> dict[str, Any]:
    errors: list[str] = []
    max_body_md = 0
    max_block = 0
    max_text_field = 0
    touched = 0
    by_field: Counter[str] = Counter()

    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            event = json.loads(line)
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            meta = payload.get("_capacity_fit") if isinstance(payload, dict) else None
            if isinstance(meta, dict):
                touched += 1
                for change in meta.get("changes") or []:
                    if isinstance(change, dict):
                        by_field[str(change.get("field"))] += 1

            if event.get("provider") == "notion" and event.get("kind") == "page.create":
                body = payload.get("body_md")
                if isinstance(body, str):
                    max_body_md = max(max_body_md, len(body))
                    if len(body) > THINK_ITEM_CHAR_BUDGET:
                        errors.append(f"line {line_no}: notion body_md exceeds think budget")
                blocks = payload.get("body_blocks")
                if isinstance(blocks, list):
                    for i, block in enumerate(blocks):
                        if not isinstance(block, str):
                            errors.append(f"line {line_no}: body_blocks[{i}] is not a string")
                            continue
                        max_block = max(max_block, len(block))
                        if len(block) > NOTION_BLOCK_CHAR_BUDGET:
                            errors.append(f"line {line_no}: body_blocks[{i}] exceeds block budget")
                elif isinstance(body, str) and len(body) > NOTION_BLOCK_CHAR_BUDGET:
                    errors.append(
                        f"line {line_no}: notion body_md exceeds block budget "
                        "without body_blocks"
                    )

            provider = str(event.get("provider") or "")
            kind = str(event.get("kind") or "")
            for p, k, key in OVERSIZE_TEXT_KEYS:
                if provider == p and kind == k:
                    value = payload.get(key)
                    if isinstance(value, str):
                        max_text_field = max(max_text_field, len(value))
                        if len(value) > EMBED_TEXT_CHAR_BUDGET:
                            errors.append(
                                f"line {line_no}: {provider}.{kind}.{key} "
                                "exceeds embedding budget"
                            )

    return {
        "ok": not errors,
        "errors": errors,
        "touched_events": touched,
        "changed_fields": dict(sorted(by_field.items())),
        "max_notion_body_md_chars": max_body_md,
        "max_notion_body_block_chars": max_block,
        "max_text_field_chars": max_text_field,
    }


def build_capacity_fit(
    input_path: Path,
    output_path: Path,
    *,
    cache_dir: Path,
    report_path: Path | None,
    refresh: bool,
) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    changed_events = 0
    by_reason: Counter[str] = Counter()
    by_field: Counter[str] = Counter()
    max_source_chars = 0
    max_summary_chars = 0

    with input_path.open(encoding="utf-8") as src, output_path.open("w", encoding="utf-8") as dst:
        for line_no, line in enumerate(src, 1):
            total += 1
            event = json.loads(line)
            fitted, changes = fit_event(
                event,
                line_no=line_no,
                cache_dir=cache_dir,
                refresh=refresh,
            )
            if changes:
                changed_events += 1
                for change in changes:
                    by_reason[str(change.get("reason"))] += 1
                    by_field[str(change.get("field"))] += 1
                    max_source_chars = max(max_source_chars, int(change.get("original_chars") or 0))
                    max_summary_chars = max(max_summary_chars, int(change.get("summary_chars") or 0))
            dst.write(json.dumps(fitted, ensure_ascii=False, separators=(",", ":")) + "\n")

    validation = validate_fit(output_path)
    report = {
        "profile": PROFILE_NAME,
        "input": str(input_path),
        "output": str(output_path),
        "cache": str(cache_dir),
        "events": total,
        "changed_events": changed_events,
        "changed_reasons": dict(sorted(by_reason.items())),
        "changed_fields": dict(sorted(by_field.items())),
        "max_source_chars": max_source_chars,
        "max_summary_chars": max_summary_chars,
        "validation": validation,
    }
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if not validation["ok"]:
        raise SystemExit(
            "capacity-fit validation failed:\n"
            + "\n".join(textwrap.indent(e, "  ") for e in validation["errors"][:20])
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create corpus/build/events.capacity_fit.jsonl from full events.jsonl."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument("--refresh-cache", action="store_true")
    args = parser.parse_args()

    report = build_capacity_fit(
        args.input,
        args.output,
        cache_dir=args.cache_dir,
        report_path=None if args.no_report else args.report,
        refresh=args.refresh_cache,
    )
    print(
        "capacity-fit wrote "
        f"{report['output']} from {report['input']} "
        f"({report['changed_events']}/{report['events']} events changed)"
    )
    print("changed fields:", json.dumps(report["changed_fields"], sort_keys=True))
    print("validation:", json.dumps(report["validation"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "corpus" / "scripts" / "11_capacity_fit_events.py"


def _load_capacity_fit_module():
    spec = importlib.util.spec_from_file_location("capacity_fit_events", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_capacity_fit_only_summarizes_oversize_notion(tmp_path: Path) -> None:
    mod = _load_capacity_fit_module()
    source = tmp_path / "events.jsonl"
    output = tmp_path / "events.capacity_fit.jsonl"
    report = tmp_path / "report.json"
    cache = tmp_path / "cache"

    long_body = (
        "# Capacity Test\n\n"
        "## Summary\n"
        + "This section preserves a core claim about Strata launch messaging. " * 45
        + "\n\n## Rollout Plan\n"
        + "- Owner review remains required.\n"
        + "- Claims map to repo:alpen and product:strata.\n"
    )
    rows = [
        {
            "t": "2025-01-01T00:00:00Z",
            "provider": "notion",
            "kind": "page.create",
            "actor": "person:alice",
            "payload": {
                "id": "notion:test",
                "title": "Capacity Test",
                "kind": "rfc",
                "body_md": long_body,
            },
        },
        {
            "t": "2025-01-01T00:00:00Z",
            "provider": "notion",
            "kind": "page.create",
            "actor": "person:alice",
            "payload": {
                "id": "notion:short",
                "title": "Short Visibility Test",
                "kind": "decision_record",
                "body_md": "A short page that should not be summarized but should be "
                "split into Notion-visible chunks. " * 5,
            },
        },
        {
            "t": "2025-01-01T00:00:01Z",
            "provider": "slack",
            "kind": "message",
            "actor": "person:alice",
            "payload": {"channel": "chan:test", "text": "short message"},
        },
    ]
    source.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

    result = mod.build_capacity_fit(
        source,
        output,
        cache_dir=cache,
        report_path=report,
        refresh=False,
    )

    assert result["changed_events"] == 2
    assert result["validation"]["ok"] is True
    fitted = [json.loads(line) for line in output.read_text().splitlines()]
    notion = fitted[0]["payload"]
    short_notion = fitted[1]["payload"]
    slack = fitted[2]["payload"]
    assert "_capacity_fit" in notion
    assert "_capacity_fit" in short_notion
    assert "_capacity_fit" not in slack
    assert len(notion["body_md"]) <= mod.THINK_ITEM_CHAR_BUDGET
    assert notion["body_blocks"]
    assert max(len(block) for block in notion["body_blocks"]) <= mod.NOTION_BLOCK_CHAR_BUDGET
    assert short_notion["body_md"].startswith("A short page")
    assert max(len(block) for block in short_notion["body_blocks"]) <= mod.NOTION_BLOCK_CHAR_BUDGET
    assert "repo:alpen" in "\n".join(notion["body_blocks"])
    assert json.loads(report.read_text())["changed_events"] == 2

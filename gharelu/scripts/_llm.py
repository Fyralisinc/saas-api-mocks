"""Shared LLM helper used by 03/04. Two transports:

  - ``deepseek``  — OpenAI-compatible HTTP API call (default; bills $)
  - ``codex``     — shells out to the local Codex CLI subprocess; auth is
                    handled by ~/.codex/auth.json; bills the ChatGPT subscription

Pick via ``LLM_PROVIDER`` env var. Both share the same on-disk cache so a
half-finished run with one transport can be resumed with the other.

Budget tracking:
  - deepseek: precise per-token cost from the API's usage block
  - codex:    we don't see the wire cost, so we count calls + assume a fixed
              compute-credit equivalent ($0 actual $, but useful for quota math)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import openai

DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"

# Pick the transport once at import time so all callers agree.
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "deepseek").lower()


def _codex_call(system: str, user: str, *, model: str | None,
                timeout_s: float = 300.0) -> str:
    """Shell out to ``codex exec`` and return the assistant's last message.

    Mirrors Fyralis's CLI transport (lib/llm/provider.py, CodexProvider._raw_call_cli):
    runs the agent fully sandboxed/read-only, captures the final message via
    ``--output-last-message``. ChatGPT-subscription billed via auth.json.
    """
    prompt = (
        "You are serving as a structured-content LLM provider. "
        "Follow the system instructions exactly. Answer the user request only. "
        "Do not mention this wrapper. Do not explore the filesystem.\n\n"
        f"<system>\n{system}\n</system>\n\n"
        f"<user>\n{user}\n</user>\n"
    )
    with tempfile.TemporaryDirectory(prefix="alpen-codex-") as tmp:
        out_path = Path(tmp) / "last.txt"
        args = [
            "codex", "--ask-for-approval", "never",
            "exec",
            "--skip-git-repo-check",
            "--ignore-user-config",
            "--sandbox", "read-only",
            "--output-last-message", str(out_path),
        ]
        if model:
            args += ["--model", model]
        args.append("-")
        try:
            proc = subprocess.run(
                args, input=prompt, text=True, capture_output=True,
                timeout=timeout_s, check=False,
            )
        except FileNotFoundError as e:
            raise SystemExit("`codex` CLI not on PATH; `npm i -g @openai/codex` first") from e
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"codex exec timed out after {timeout_s:.0f}s") from e
        if proc.returncode != 0:
            tail = (proc.stderr + proc.stdout)[-1500:]
            raise RuntimeError(f"codex exec exited {proc.returncode}:\n{tail}")
        if out_path.exists():
            content = out_path.read_text(encoding="utf-8").strip()
            if content:
                return content
        # Fallback: parse the final "codex" block from stdout.
        for marker in ("\ncodex\n", "\ncodex "):
            idx = proc.stdout.rfind(marker)
            if idx != -1:
                tail = proc.stdout[idx + len(marker):].strip()
                # Strip the "tokens used …" footer if present.
                cut = tail.find("\ntokens used")
                return (tail[:cut] if cut != -1 else tail).strip()
        raise RuntimeError("codex exec produced no parseable output")

# DeepSeek pricing per million tokens (check current at platform.deepseek.com).
PRICING = {
    "deepseek-chat":     {"input": 0.27, "cached_input": 0.07, "output": 1.10},
    "deepseek-reasoner": {"input": 0.55, "cached_input": 0.14, "output": 2.19},
}


@dataclass
class Budget:
    cap_usd: float
    spent_usd: float = 0.0
    in_tokens: int = 0
    cached_tokens: int = 0
    out_tokens: int = 0
    calls: int = 0
    cache_hits: int = 0
    log: list[dict] = field(default_factory=list)

    def add(self, model: str, prompt_tokens: int, cached_tokens: int,
            completion_tokens: int) -> None:
        p = PRICING[model]
        # DeepSeek reports prompt_tokens as the TOTAL (cache_hit + cache_miss).
        miss = max(0, prompt_tokens - cached_tokens)
        cost = (miss * p["input"] + cached_tokens * p["cached_input"]
                + completion_tokens * p["output"]) / 1_000_000
        self.spent_usd += cost
        self.in_tokens += prompt_tokens
        self.cached_tokens += cached_tokens
        self.out_tokens += completion_tokens
        self.calls += 1

    def check(self) -> None:
        if self.spent_usd >= self.cap_usd:
            raise RuntimeError(
                f"budget exceeded: ${self.spent_usd:.4f} >= cap ${self.cap_usd:.2f}"
            )

    def summary(self) -> str:
        return (f"calls={self.calls} cache_hits={self.cache_hits} "
                f"in={self.in_tokens} cached={self.cached_tokens} "
                f"out={self.out_tokens} spend=${self.spent_usd:.4f} "
                f"(cap ${self.cap_usd:.2f})")


def _cache_key(model: str, messages: list[dict],
               temperature: float, response_format: dict | None) -> str:
    payload = json.dumps({
        "model": model, "messages": messages,
        "temperature": temperature,
        "response_format": response_format,
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def chat(
    messages: list[dict],
    *,
    budget: Budget,
    cache_dir: Path,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.7,
    response_format: dict | None = None,
    dry_run: bool = False,
    max_tokens: int | None = None,
) -> str:
    """Send a chat completion; return content string.

    Caches by SHA-256 of the full request payload so re-runs are free.
    Honors --dry-run (prints prompt, returns placeholder).
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = _cache_key(model, messages, temperature, response_format)
    cached = cache_dir / f"{key}.json"
    if cached.exists():
        budget.cache_hits += 1
        return json.loads(cached.read_text())["content"]

    if dry_run:
        # Estimate: 1 token ≈ 4 chars. Just print and bail.
        prompt_chars = sum(len(m["content"]) for m in messages)
        est_in = prompt_chars // 4
        est_out = max_tokens or 2000
        if LLM_PROVIDER == "codex":
            print(f"[dry-run/codex] est_in≈{est_in} est_out≈{est_out} "
                  f"transport=cli subscription-billed", file=sys.stderr)
        else:
            p = PRICING[model]
            est_cost = (est_in * p["input"] + est_out * p["output"]) / 1_000_000
            print(f"[dry-run/deepseek] model={model} est_in≈{est_in} "
                  f"est_out≈{est_out} est_cost≈${est_cost:.4f}", file=sys.stderr)
        return "{}"

    if LLM_PROVIDER == "codex":
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        user_text = "\n\n".join(m["content"] for m in messages if m["role"] == "user")
        codex_model = os.environ.get("CODEX_MODEL")  # None → CLI uses config.toml default
        content = _codex_call(system, user_text, model=codex_model)
        # No per-token cost on the wire — call-count budget only.
        budget.calls += 1
        cached.write_text(json.dumps({
            "content": content, "usage": {"transport": "codex"},
            "model": codex_model or "config-default",
        }))
        return content

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise SystemExit("DEEPSEEK_API_KEY not set; `set -a; . ./.env; set +a` first")
    client = openai.OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)

    for attempt in range(4):
        try:
            r = client.chat.completions.create(
                model=model, messages=messages,
                temperature=temperature,
                response_format=response_format,
                max_tokens=max_tokens,
            )
            break
        except openai.RateLimitError as e:
            wait = 5 * (attempt + 1)
            print(f"  [rate-limit] sleeping {wait}s ({e})", file=sys.stderr)
            time.sleep(wait)
        except openai.APIStatusError as e:
            if e.status_code == 402:
                raise SystemExit("DeepSeek balance insufficient — top up at "
                                 "https://platform.deepseek.com")
            raise
    else:
        raise RuntimeError("LLM call failed after retries")

    content = r.choices[0].message.content or ""
    u = r.usage
    cached_tok = getattr(u, "prompt_cache_hit_tokens", None) or 0
    budget.add(model, u.prompt_tokens, cached_tok, u.completion_tokens)
    budget.check()

    cached.write_text(json.dumps({
        "content": content,
        "usage": {"in": u.prompt_tokens, "cached": cached_tok,
                  "out": u.completion_tokens},
        "model": model,
    }))
    budget.log.append({
        "key": key[:16], "in": u.prompt_tokens, "cached": cached_tok,
        "out": u.completion_tokens,
    })
    return content

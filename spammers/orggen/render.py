"""Jinja-based content rendering.

Loads templates from spammers/orggen/templates/<provider>/<name>.j2.
Deterministic: every input variable is derived from the RunRandom.
"""
from __future__ import annotations

import pathlib
from functools import lru_cache
from typing import Any

try:
    from jinja2 import Environment, FileSystemLoader, StrictUndefined
except ImportError:  # pragma: no cover
    Environment = None  # type: ignore


_TEMPLATE_DIR = pathlib.Path(__file__).resolve().parent / "templates"


@lru_cache(maxsize=1)
def _env():
    if Environment is None:
        raise RuntimeError(
            "jinja2 not installed — add it to pyproject.toml or pip install jinja2"
        )
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        undefined=StrictUndefined,
        keep_trailing_newline=False,
        autoescape=False,
    )


def render(template_rel_path: str, **kwargs: Any) -> str:
    """``template_rel_path`` like ``slack/standup.j2``."""
    tpl = _env().get_template(template_rel_path)
    return tpl.render(**kwargs).strip()

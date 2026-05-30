"""The single company the spammer simulates.

Historically the Studio offered nine profile-driven synthetic companies. The
spammer is now a corpus replayer for one specific company — Gharelu-Alpen,
a homemade high-fidelity simulation of Alpen Labs — so this collapses to a
single entry. The shape is kept (key, name, stage, tagline) so the rest of
the Studio's API + UI doesn't have to change.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Company:
    key: str
    name: str
    stage: str
    tagline: str
    span: str
    headcount: str

    def as_dict(self) -> dict:
        d = asdict(self)
        d["est"] = "~2 min"
        return d


GHARELU_ALPEN = Company(
    key="gharelu-alpen",
    name="Gharelu-Alpen",
    stage="Series-A protocol startup",
    tagline="A homemade high-fidelity simulation of Alpen Labs — "
            "BitVM / Strata, real public GitHub history, ~36 people.",
    span="~4 years of history",
    headcount="~36 people",
)


COMPANIES: list[Company] = [GHARELU_ALPEN]
_BY_KEY = {c.key: c for c in COMPANIES}


def get(key: str) -> Company | None:
    return _BY_KEY.get(key)


def all_companies() -> list[dict]:
    return [c.as_dict() for c in COMPANIES]

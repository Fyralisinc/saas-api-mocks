"""The selectable 'companies' — friendly names over the 9 OrgGen profiles.

Each company is just a (size, runtime) profile with a memorable name, a
stage label, and a one-line tagline. Seeds are fixed per company so a given
company always regenerates identically.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class Company:
    key: str
    name: str
    size: str        # small | medium | large
    runtime: str     # few_months | one_year | few_years
    seed: int
    stage: str       # human stage label
    tagline: str

    def as_dict(self) -> dict:
        d = asdict(self)
        d["headcount"], d["span"], d["est"] = _PROFILE_HINT[(self.size, self.runtime)]
        return d


# (size, runtime) -> (approx headcount, human span, est. init time) — display only.
# Init time grows with daily_events × duration (millions of rows for the big ones).
_PROFILE_HINT = {
    ("small", "few_months"): ("~8 people", "~3 months of history", "~3s"),
    ("small", "one_year"): ("~12 people", "~1 year of history", "~4s"),
    ("small", "few_years"): ("~20 people", "~3 years of history", "~6s"),
    ("medium", "few_months"): ("~60 people", "~3 months of history", "~10s"),
    ("medium", "one_year"): ("~100 people", "~1 year of history", "~35s"),
    ("medium", "few_years"): ("~150 people", "~3 years of history", "~3 min"),
    ("large", "few_months"): ("~600 people", "~3 months of history", "~50s"),
    ("large", "one_year"): ("~1,200 people", "~1 year of history", "~7 min"),
    ("large", "few_years"): ("~2,000 people", "~3 years of history", "~30 min"),
}


COMPANIES: list[Company] = [
    Company("seedling", "Seedling Labs", "small", "few_months", 42,
            "Seed-stage startup", "A tiny team three months past their first commit."),
    Company("tinker", "Tinker Foundry", "small", "one_year", 43,
            "Early startup", "A small crew with a year of scrappy iteration behind them."),
    Company("garage", "Garage Collective", "small", "few_years", 44,
            "Bootstrapped small co.", "Small, profitable, and several years deep into the product."),
    Company("vertex", "Vertex Dynamics", "medium", "few_months", 45,
            "Series-A scaleup", "Just raised, hiring fast, three months of rapid growth."),
    Company("quanta", "Quanta Systems", "medium", "one_year", 46,
            "Growth-stage company", "A hundred people and a year of scaling pains."),
    Company("meridian", "Meridian Works", "medium", "few_years", 47,
            "Established mid-size", "A settled mid-size org with years of product history."),
    Company("hyperion", "Hyperion Industries", "large", "few_months", 48,
            "Enterprise (post-merger)", "Six hundred people, three months into a big reorg."),
    Company("olympus", "Olympus Networks", "large", "one_year", 49,
            "Large enterprise", "A thousand-plus headcount with a year of org sprawl."),
    Company("atlas", "Atlas Conglomerate", "large", "few_years", 50,
            "Global megacorp", "Two thousand people across years of accumulated history."),
]

_BY_KEY = {c.key: c for c in COMPANIES}


def get(key: str) -> Company | None:
    return _BY_KEY.get(key)


def all_companies() -> list[dict]:
    return [c.as_dict() for c in COMPANIES]

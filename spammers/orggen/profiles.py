"""Organization profile dial.

Maps ``(size, runtime)`` to concrete headcount / project / event-rate parameters.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Literal


Size = Literal["small", "medium", "large"]
Runtime = Literal["few_months", "one_year", "few_years"]


@dataclass(frozen=True)
class ProfileSpec:
    size: Size
    runtime: Runtime

    people: int
    teams: int
    repos: int
    slack_channels: int
    discord_channels: int

    daily_events: int                  # total events/day across all four apps

    # Per-app share of daily events. These are independent rate multipliers
    # (each provider generates ``int(daily_events * its_share)`` events/day), not
    # a partition — adding a provider raises total volume rather than reslicing.
    slack_share: float = 0.50
    github_share: float = 0.15
    gmail_share: float = 0.25
    discord_share: float = 0.10
    calendar_share: float = 0.12
    notion_share: float = 0.15

    @property
    def duration(self) -> timedelta:
        return RUNTIME_DURATION[self.runtime]


RUNTIME_DURATION: dict[Runtime, timedelta] = {
    "few_months": timedelta(days=90),
    "one_year": timedelta(days=365),
    "few_years": timedelta(days=3 * 365),
}


PROFILES: dict[tuple[Size, Runtime], ProfileSpec] = {
    ("small",  "few_months"): ProfileSpec("small",  "few_months",   8,  2,   3,    6,   4,      60),
    ("small",  "one_year"):   ProfileSpec("small",  "one_year",    12,  3,   5,    8,   5,      80),
    ("small",  "few_years"):  ProfileSpec("small",  "few_years",   20,  4,   8,   12,   7,     100),

    ("medium", "few_months"): ProfileSpec("medium", "few_months",  60,  6,  12,   20,  12,     600),
    ("medium", "one_year"):   ProfileSpec("medium", "one_year",   100,  8,  20,   30,  18,     900),
    ("medium", "few_years"):  ProfileSpec("medium", "few_years",  150, 10,  35,   45,  25,    1200),

    ("large",  "few_months"): ProfileSpec("large",  "few_months", 600, 30,  80,  120,  60,    6000),
    ("large",  "one_year"):   ProfileSpec("large",  "one_year", 1200, 50, 150,  200, 100,   10000),
    ("large",  "few_years"):  ProfileSpec("large",  "few_years",2000, 80, 350,  350, 160,   15000),
}


def resolve(size: Size, runtime: Runtime) -> ProfileSpec:
    key = (size, runtime)
    if key not in PROFILES:
        raise ValueError(f"no profile for ({size}, {runtime})")
    return PROFILES[key]

"""Persona archetypes + generator.

Each generated ``Person`` represents a single human in the synthetic org.
The set scales to the profile's ``people`` count; role mix scales with team mix.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Sequence
from uuid import UUID, uuid4

from spammers.orggen.profiles import ProfileSpec
from spammers.orggen.seed import RunRandom


@dataclass(frozen=True)
class RoleArchetype:
    role: str
    levels: Sequence[str]      # one per "instance" of the role
    team_label: str            # which team they belong to


@dataclass
class Person:
    id: UUID
    handle: str
    full_name: str
    email: str
    role: str
    level: str
    team_name: str
    timezone: str
    started_at: datetime
    ended_at: datetime | None = None
    voice_signature: dict = field(default_factory=dict)


# ---- name pools (deterministic; mixed Western/Indian/Latin/East-Asian) ----

_FIRST_NAMES = [
    "Alice", "Marcus", "Monica", "Priya", "David", "Nora", "Jakob", "Sara",
    "Tomas", "Evelyn", "Ben", "Rachin", "Hana", "Diego", "Mei", "Omar",
    "Sofia", "Liam", "Aditi", "Yusuf", "Zoe", "Niko", "Ines", "Kenji",
    "Lara", "Mateo", "Aisha", "Idris", "Eva", "Hugo", "Jin", "Maya",
    "Noah", "Olga", "Pavel", "Quinn", "Rita", "Sven", "Tara", "Umar",
    "Vera", "Wei", "Xander", "Yara", "Zane", "Amani", "Bilal", "Carmen",
    "Dara", "Esha", "Fiona", "Gus", "Hadi", "Iris", "Jonas", "Kira",
    "Leon", "Mira", "Nadia", "Otto", "Pia", "Reza", "Sami", "Tess",
]

_LAST_NAMES = [
    "Park", "Mendes", "Kapoor", "Schmidt", "Okafor", "Reyes", "Larsen",
    "Hassan", "Petrov", "Yamamoto", "Silva", "Chen", "Nair", "Brown",
    "Rao", "Khan", "Singh", "Garcia", "Martin", "Wang", "Sato", "Hughes",
    "Iyer", "Lopez", "Cohen", "Nakamura", "Patel", "Olsen", "Carvalho",
    "Becker", "Adler", "Walsh", "Vargas", "Khanna", "Becker", "Nakagawa",
    "Bauer", "Mensah", "Nasser", "Costa", "Romero", "Andersen", "Berg",
]

_TIMEZONES = [
    "America/Los_Angeles", "America/Denver", "America/Chicago", "America/New_York",
    "America/Sao_Paulo", "Europe/London", "Europe/Berlin", "Europe/Helsinki",
    "Asia/Kolkata", "Asia/Singapore", "Asia/Tokyo", "Australia/Sydney",
]


# Team & role composition by size. For each team we give role-counts that
# get scaled to the profile's total ``people`` count.
TEAM_MIX_SMALL: dict[str, dict[str, int]] = {
    "Engineering": {"ic": 4, "senior": 2, "manager": 1},
    "Sales":       {"ae": 1, "head": 1},
}

TEAM_MIX_MEDIUM: dict[str, dict[str, int]] = {
    "Engineering": {"ic": 14, "senior": 6, "staff": 2, "manager": 3, "head": 1},
    "Product":     {"pm": 4, "head": 1},
    "Design":      {"designer": 3, "senior": 1, "head": 1},
    "Sales":       {"ae": 6, "ae_senior": 3, "head": 1},
    "Marketing":   {"marketer": 3, "head": 1},
    "CustomerSuccess": {"cs": 4, "head": 1},
    "Finance":     {"analyst": 1, "head": 1},
    "Ops":         {"opsmgr": 1, "head": 1},
}

TEAM_MIX_LARGE: dict[str, dict[str, int]] = {
    "EngineeringPlatform":   {"ic": 60, "senior": 30, "staff": 10, "manager": 8, "head": 2},
    "EngineeringProduct":    {"ic": 80, "senior": 40, "staff": 12, "manager": 10, "head": 2},
    "EngineeringInfra":      {"ic": 40, "senior": 20, "staff": 6, "manager": 6, "head": 1},
    "EngineeringSecurity":   {"ic": 12, "senior": 6, "staff": 2, "manager": 2, "head": 1},
    "Product":               {"pm": 20, "senior_pm": 8, "head": 2},
    "Design":                {"designer": 18, "senior": 8, "head": 2},
    "Sales":                 {"ae": 60, "ae_senior": 25, "manager": 6, "head": 2, "vp": 1},
    "Marketing":             {"marketer": 30, "head": 2},
    "CustomerSuccess":       {"cs": 40, "manager": 5, "head": 1},
    "Finance":               {"analyst": 6, "head": 1, "cfo": 1},
    "Legal":                 {"counsel": 4, "head": 1},
    "Ops":                   {"opsmgr": 8, "head": 1},
    "Exec":                  {"ceo": 1, "cto": 1, "coo": 1},
}


def _select_team_mix(spec: ProfileSpec) -> dict[str, dict[str, int]]:
    if spec.size == "small":
        return TEAM_MIX_SMALL
    if spec.size == "medium":
        return TEAM_MIX_MEDIUM
    return TEAM_MIX_LARGE


def _scale_mix(mix: dict[str, dict[str, int]], target_people: int) -> dict[str, dict[str, int]]:
    """Scale role-counts proportionally so the total matches ``target_people``."""
    total = sum(sum(roles.values()) for roles in mix.values())
    if total == 0:
        return mix
    scale = target_people / total
    scaled: dict[str, dict[str, int]] = {}
    running = 0
    keys = list(mix.keys())
    for i, team in enumerate(keys):
        scaled[team] = {}
        roles = mix[team]
        for role, count in roles.items():
            new_count = max(1, round(count * scale))
            scaled[team][role] = new_count
            running += new_count
    # adjust last team to hit target exactly
    diff = target_people - running
    if diff != 0 and keys:
        last = keys[-1]
        # bump the largest role in the last team
        if scaled[last]:
            biggest = max(scaled[last], key=lambda r: scaled[last][r])
            scaled[last][biggest] = max(1, scaled[last][biggest] + diff)
    return scaled


def _voice_signature(rng: RunRandom, role: str, level: str) -> dict:
    return {
        "verbosity": rng.choice(["terse", "moderate", "verbose"]),
        "formality": rng.choice(["casual", "neutral", "formal"]),
        "punctuation": rng.choice(["light", "heavy"]),
        "emoji_rate": round(rng.uniform(0.0, 0.4 if role in ("designer", "marketer", "cs") else 0.15), 2),
    }


def generate_people(
    spec: ProfileSpec,
    rng: RunRandom,
    *,
    virtual_now: datetime,
) -> tuple[list[Person], list[str]]:
    """Returns (people, team_names_in_order)."""
    rng_p = rng.sub("people")
    mix = _scale_mix(_select_team_mix(spec), spec.people)
    teams = list(mix.keys())

    used_handles: set[str] = set()
    used_emails: set[str] = set()
    people: list[Person] = []

    earliest_hire = virtual_now - spec.duration

    for team in teams:
        roles = mix[team]
        for role, count in roles.items():
            for _ in range(count):
                first = rng_p.choice(_FIRST_NAMES)
                last = rng_p.choice(_LAST_NAMES)
                base_handle = (first[0] + last).lower()
                handle = base_handle
                i = 0
                while handle in used_handles:
                    i += 1
                    handle = f"{base_handle}{i}"
                used_handles.add(handle)
                email = f"{handle}@spammer-org.test"
                if email in used_emails:
                    email = f"{handle}.{rng_p.randint(10, 99)}@spammer-org.test"
                used_emails.add(email)

                # hire date — earlier for senior/head/vp
                seniority_bias = {
                    "ic": 0.3, "designer": 0.3, "ae": 0.3, "cs": 0.3, "marketer": 0.3,
                    "senior": 0.6, "senior_pm": 0.6, "ae_senior": 0.6, "analyst": 0.5,
                    "staff": 0.8, "manager": 0.85, "opsmgr": 0.8, "pm": 0.6,
                    "head": 0.95, "vp": 0.97, "counsel": 0.7,
                    "ceo": 1.0, "cto": 1.0, "coo": 0.95, "cfo": 0.97,
                }.get(role, 0.5)
                # bias = high → hired earlier in the runtime
                u = rng_p.uniform(0.0, 1.0)
                # mix uniform with a left-tail per seniority_bias
                pos = u * (1 - seniority_bias) + (u ** 3) * seniority_bias
                hire = earliest_hire + (virtual_now - earliest_hire) * pos
                # truncate microseconds for stability
                hire = hire.replace(microsecond=0)

                person = Person(
                    id=uuid4(),
                    handle=handle,
                    full_name=f"{first} {last}",
                    email=email,
                    role=role,
                    level=role if role in {"head", "vp", "ceo", "cto", "cfo", "coo"} else _level_for(role),
                    team_name=team,
                    timezone=rng_p.choice(_TIMEZONES),
                    started_at=hire,
                    voice_signature=_voice_signature(rng_p, role, role),
                )
                people.append(person)

    return people, teams


def _level_for(role: str) -> str:
    if role in {"intern"}: return "intern"
    if role.endswith("_senior") or role == "senior": return "senior"
    if role == "staff": return "staff"
    if role in {"manager", "opsmgr"}: return "manager"
    if role in {"head"}: return "head"
    if role in {"vp"}: return "vp"
    if role in {"ceo", "cto", "cfo", "coo"}: return "exec"
    return "ic"

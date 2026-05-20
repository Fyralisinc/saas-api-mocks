"""Deterministic organization timeline generator.

Entry point: ``spammers.orggen.compile.compile_run(pool, run_id)``.
Reads ``org.runs`` row, populates org.people / teams / projects,
generates timeline.events, and projects per-app state.
"""

from spammers.orggen.profiles import PROFILES, ProfileSpec
from spammers.orggen.seed import RunRandom

__all__ = ["PROFILES", "ProfileSpec", "RunRandom"]

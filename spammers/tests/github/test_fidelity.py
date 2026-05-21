"""GitHub format/signing fidelity (offline, no DB)."""
from __future__ import annotations

import hashlib
import hmac
import re

from spammers.common.ids import (
    github_app_id,
    github_installation_id,
    github_installation_token,
    github_repo_id,
    github_sha,
)
from spammers.common.signing import github_sign, github_verify


def test_app_and_installation_ids_are_ints():
    assert isinstance(github_app_id(), int)
    assert isinstance(github_installation_id(), int)
    assert isinstance(github_repo_id(), int)


def test_installation_token_format():
    assert re.fullmatch(r"ghs_[A-Za-z0-9]{36}", github_installation_token())


def test_sha_format():
    assert re.fullmatch(r"[0-9a-f]{40}", github_sha())


def test_github_signature_matches_independent_computation():
    secret, body = "whsec", b'{"action":"opened"}'
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert github_sign(secret, body) == expected


def test_github_verify_roundtrip():
    secret, body = "whsec", b"payload"
    assert github_verify(secret, github_sign(secret, body), body) is True
    assert github_verify(secret, github_sign(secret, body), b"tampered") is False

"""Spammer Studio — an isolated control/observation UI for the mocks.

This package is deliberately decoupled from the mock runtime: the
slack/discord/github mocks never import anything from here, so if the
Studio breaks the mocks keep working. The Studio only:

  - shells out to the Director CLI (reset / prepare) and spawns the mock
    server processes (process supervision), and
  - reads the shared DB (read-only) + calls the public ``inject_*``
    helpers in ``spammers.orggen.live``.

Run it with ``python -m spammers.studio`` (or ``./dev.sh studio``).
"""

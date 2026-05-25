"""Gmail mock (:7004).

Wire-compatible with the Gmail API + Admin Directory surface the consumer uses,
plus the full Pub/Sub push path:

  - DWD ``POST /token`` (shared with the Calendar mock via common.google_token)
  - ``GET /gmail/v1/users/{userId}/messages`` (list) + ``/messages/{id}`` (get)
  - ``GET /gmail/v1/users/{userId}/threads/{id}``
  - ``GET /gmail/v1/users/{userId}/history`` (incremental drain)
  - ``POST /gmail/v1/users/{userId}/watch`` + ``/stop``; ``/profile``
  - Admin Directory ``users``/``groups``/``orgunits`` (mailbox enumeration)
  - ``GET /jwks`` + an OIDC-signed Pub/Sub push emitter (see push.py)

Serves the latest run resolved at startup; nothing here is imported by the
other mocks.
"""

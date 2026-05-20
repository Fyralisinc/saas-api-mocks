"""Slack mock — wire-compatible replica.

Entry point: ``spammers.slack.app.create_app()`` (FastAPI).

Surfaces:
  • OAuth: /oauth/v2/authorize, /api/oauth.v2.access
  • Web API: chat.postMessage, users.{info,list}, conversations.{info,list,history,
    replies,members}, team.info, auth.test
  • Outbound Events API: signed POST to Fyralis on each new message
"""

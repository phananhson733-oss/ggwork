"""Trends radar: the Google Trends and Search Console observation channels (design 2026-09-25, plan TR-01).

Two Railway cron services run `python -m ggwork_pick.observe.trends` and `python -m ggwork_pick.observe.gsc` from the
gateway image; the gateway itself imports only the read side, lazily. This package module imports nothing, so a cron
process pays for exactly the submodules it uses (plan D7): no gateway runtime, app config, fastapi or alembic.
"""

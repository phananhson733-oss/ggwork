"""Trends radar: the Google Trends and Search Console observation channels (design 2026-09-25, plan TR-01).

The design has two Railway cron services, both run from the gateway image. One entry point exists today:
`python -m ggwork_pick.observe.trends` (trends/__main__.py; plan TR-14, TR-15). The Search Console one,
`python -m ggwork_pick.observe.gsc`, is planned and not built: gsc/__main__.py and gsc/run.py come with plan TR-21, and
until then the gsc package has no __main__. The gateway itself imports only the read side, lazily. This package module
imports nothing, so a cron process pays for exactly the submodules it uses (plan D7): no gateway runtime, app config,
fastapi or alembic.
"""

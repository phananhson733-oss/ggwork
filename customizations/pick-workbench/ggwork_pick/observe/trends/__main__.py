"""`python -m ggwork_pick.observe.trends [run|status|preflight|canary-report] [--selfcheck-only]`: the pick-obs-trends cron (plan
TR-14, TR-15).

The command line, `status`, `--selfcheck-only` and the exit statuses are observe.cron's, shared with the gsc cron. What
is the trends channel's own:

- `run` (the default): one trigger of the nightly session (run.py). The cron fires every 30 minutes from 17:00 to 01:30
  UTC; a trigger before the mode's start or after the 01:45 deadline does nothing and exits 0. Every request is paced
  at the preset PICK_OBS_TRENDS_PACE names (pacing.PRESETS, user unless set), and a canary's task list must pass the
  payload gate (admission.py) before its first request.
- `canary-report`: seven UTC calendar dates of stored aggregate facts, using only the read-only status reader.
  Missing dates remain unknown; no collector configuration, HTTP requests or promotion decision.
- `preflight`: tonight's task list in figures, read-only, no request (preflight.py): 0 when the night would run as a
  valid canary night, 2 when it would be refused. S6a reads it. On Railway it is the second step of the self-check
  config's start command (deploy/pick-obs/trends/selfcheck/railway.toml), run only when --selfcheck-only exited 0, in
  the service's own container with its own variables.
- the configuration, checked before anything is read or sent by a run and by `--selfcheck-only` alike, a bad one
  exiting 2: the mode, the pace (and that the mode fits its window at it, capacity.py) and the other settings
  (settings.py), the canary's control list (canary.py; the default is the package's trends/canary_controls.json, with
  a market series for every geo the canary queries), the state key (crypto.load_cipher) and the egress echo URL
  (egress.py, off unless set). The stable mode's task source is TR-18's WatchTaskSource: until it is registered here,
  stable is refused.
"""

import asyncio
import json
import logging
import random
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

import httpx

from ggwork_pick.observe.clock import Clock, SystemClock, random_source
from ggwork_pick.observe.cron import SELFCHECK_VARIABLES, CronEntry, cron_main
from ggwork_pick.observe.crypto import KEY_FILE_VARIABLE, KEY_VARIABLE, StateCipher, load_cipher
from ggwork_pick.observe.errors import Refused
from ggwork_pick.observe.trends import admission as gate
from ggwork_pick.observe.trends import canary_report, pacing, preflight
from ggwork_pick.observe.trends.canary import CanaryTaskSource, load_controls
from ggwork_pick.observe.trends.egress import ECHO_ENV, egress_from_env
from ggwork_pick.observe.trends.run import TRENDS, TaskSource, Wiring, run_trends
from ggwork_pick.observe.trends.settings import VARIABLES, Settings, settings_from

PROG = "python -m ggwork_pick.observe.trends"
DESCRIPTION = "选剧观测雷达的 Trends 采集（pick-obs-trends cron）"
TRENDS_VARIABLES = frozenset({*SELFCHECK_VARIABLES, *VARIABLES, KEY_VARIABLE, KEY_FILE_VARIABLE, ECHO_ENV})


def source_for(settings: Settings, controls_path: Path | None) -> TaskSource:
    """The mode's task source: the canary's control list and fresh titles; stable waits for TR-18."""
    if settings.canary:
        return CanaryTaskSource(load_controls(controls_path), granularities=settings.granularities, related=settings.related)
    raise Refused("stable 模式的任务来源是 TR-18 的 WatchTaskSource，尚未接入：金丝雀结束、TR-18 部署之后再切 stable（计划第 9 节）")


@dataclass(frozen=True)
class Configured:
    settings: Settings
    source: TaskSource
    cipher: StateCipher


def configured(environ: Mapping[str, str], controls_path: Path | None) -> Configured:
    """The session's configuration, each part checked; Refused (exit 2) without echoing a value."""
    settings = settings_from(environ)
    return Configured(settings, source_for(settings, controls_path), load_cipher(environ))


@dataclass(frozen=True)
class TrendsCron:
    """The trends entry's parts, injectable (tests pass a ManualClock, a MockTransport, a no-op pacer, a looser payload
    gate). In production there is no pacer here, so run.run_session paces at the settings' preset (the one place that
    default is made), and the gate is admission.STRICT."""

    clock: Clock
    rng: random.Random
    transport: httpx.AsyncBaseTransport | None
    pacer: pacing.Pacer | None
    controls_path: Path | None
    admission: gate.Admission = gate.STRICT

    async def check(self, environ: Mapping[str, str]) -> None:
        configured(environ, self.controls_path)
        async with egress_from_env(environ, clock=self.clock):  # the URL is checked; nothing is sent
            pass

    async def run(self, environ: Mapping[str, str]) -> int:
        config = configured(environ, self.controls_path)
        async with egress_from_env(environ, clock=self.clock) as egress:
            probe = egress if egress.enabled else None
            wiring = Wiring(clock=self.clock, rng=self.rng, transport=self.transport, pacer=self.pacer, egress=probe, admission=self.admission)
            return int(await run_trends(config.settings, config.source, cipher=config.cipher, environ=environ, wiring=wiring))

    async def preflight(self, environ: Mapping[str, str], out: TextIO) -> int:
        config = configured(environ, self.controls_path)
        line = await preflight.tonight(config.settings, config.source, now=self.clock.now(), environ=environ, admission=self.admission)
        print(json.dumps({"preflight": line}, ensure_ascii=False, default=str), file=out)
        return int(preflight.exit_code(line))

    async def report(self, environ: Mapping[str, str], out: TextIO) -> int:
        return await canary_report.print_report(environ, out, now=self.clock.now())

    def entry(self) -> CronEntry:
        return CronEntry(TRENDS, PROG, DESCRIPTION, TRENDS_VARIABLES, self.check, self.run, {"preflight": self.preflight, "canary-report": self.report})


async def amain(
    argv: Sequence[str],
    *,
    environ: Mapping[str, str] | None = None,
    clock: Clock | None = None,
    rng: random.Random | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    pacer: pacing.Pacer | None = None,
    controls_path: Path | None = None,
    admission: gate.Admission = gate.STRICT,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """The entry point with its parts injectable."""
    cron = TrendsCron(clock or SystemClock(), rng or random_source(), transport, pacer, controls_path, admission)
    return await cron_main(cron.entry(), argv, environ=environ, out=out, err=err)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s", stream=sys.stderr)
    return asyncio.run(amain(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())

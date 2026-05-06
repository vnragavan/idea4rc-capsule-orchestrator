"""Adaptive drain pollers for the three post-ingest stages.

Each drain polls a monotonic counter, declares "drained" once the value
has been stable for a configurable number of polls (and >= ``min_rows``,
and elapsed >= ``min_wait_seconds``). It also short-circuits with
``fatal()`` on either:

* a hard wall-clock timeout (``timeout``), or
* an adaptive stall (``max_stall_seconds`` of no growth, after at least
  ``min_wait_seconds`` have elapsed).

The bash counterparts in ``fresh_ingest.sh`` are
``wait_for_aerospike_drained`` / ``wait_for_staging_drained`` /
``wait_for_omop_drained``. This module preserves those semantics
exactly; only the implementation language changes.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional

from idea4rc_capsule.config import (
    AerospikeDrainConfig,
    Config,
    DrainConfig,
    OMOPDrainConfig,
    StagingDrainConfig,
)
from idea4rc_capsule.kube import Kube
from idea4rc_capsule.logging import fatal, log


CountFn = Callable[[], Optional[int]]
"""A drain probe: returns the current monotonic counter value, or None
when the value cannot be read this poll (transient errors are tolerated;
None is treated as 0)."""


@dataclass
class _DrainState:
    label: str
    elapsed: int = 0
    last_growth_elapsed: int = 0
    last_count: int = -1


def _drain_loop(
    *,
    label: str,
    knobs: DrainConfig,
    probe: CountFn,
    on_count_log: Callable[[int, int, int], str],
) -> None:
    """Shared polling loop. ``on_count_log(count, delta, elapsed)`` formats
    the per-poll log line so each drain can phrase the unit (records,
    inserts, rows). ``delta`` is non-negative (0 means no growth)."""
    history: deque[int] = deque(maxlen=knobs.stable_polls)
    state = _DrainState(label=label)
    log(f"Waiting for {label} to drain")
    log(f"  hard_timeout={knobs.timeout}s  max_stall={knobs.max_stall_seconds}s  "
        f"interval={knobs.poll_interval}s")
    log(f"  stable_polls={knobs.stable_polls}  min_rows={knobs.min_rows}  "
        f"min_wait={knobs.min_wait_seconds}s")

    while True:
        raw = probe()
        if raw is None or raw < 0:
            log(f"{label}: count query returned non-numeric value; treating as 0.")
            count = 0
        else:
            count = int(raw)

        if count > state.last_count:
            delta = count - max(state.last_count, 0)
            log(on_count_log(count, delta, state.elapsed))
            state.last_growth_elapsed = state.elapsed
        else:
            stall = state.elapsed - state.last_growth_elapsed
            log(f"{label}: {count}  (no growth for {stall}s, elapsed={state.elapsed}s)")
        state.last_count = count

        history.append(count)

        # Stable verdict path.
        if (len(history) >= knobs.stable_polls
                and count >= knobs.min_rows
                and len(set(history)) == 1):
            if state.elapsed < knobs.min_wait_seconds:
                log(f"{label} looks stable at {count} but min_wait="
                    f"{knobs.min_wait_seconds}s not reached "
                    f"(elapsed={state.elapsed}s); continuing.")
            else:
                log(f"{label} drained: stable at {count} over "
                    f"{knobs.stable_polls} polls (elapsed={state.elapsed}s).")
                return

        stall = state.elapsed - state.last_growth_elapsed
        if (knobs.max_stall_seconds > 0
                and stall >= knobs.max_stall_seconds
                and state.elapsed >= knobs.min_wait_seconds):
            fatal(
                f"{label}: counter has not grown for {stall}s "
                f"(>= max_stall={knobs.max_stall_seconds}s); last={count}. "
                f"Treating as stuck."
            )

        if state.elapsed >= knobs.timeout:
            fatal(
                f"{label}: hard timeout ({knobs.timeout}s) reached "
                f"without stable verdict (last={count}). "
                f"This is the absolute wall-clock cap; the adaptive stall "
                f"detector ('max_stall') is the preferred trigger."
            )

        time.sleep(knobs.poll_interval)
        state.elapsed += knobs.poll_interval


# --------------------------------------------------------------------- probes
def _aerospike_probe(kube: Kube, knobs: AerospikeDrainConfig) -> CountFn:
    """asinfo -v sets/<ns>/<set> emits colon-separated key=value fragments;
    we extract the ``objects=`` field."""
    def _probe() -> Optional[int]:
        try:
            txt = kube.exec_capture(
                f"deploy/{knobs.deployment}",
                "sh", "-lc",
                f"asinfo -v 'sets/{knobs.as_namespace}/{knobs.as_set}' 2>/dev/null",
                check=False,
            )
        except SystemExit:
            return None
        for part in txt.split(":"):
            if part.startswith("objects="):
                tail = part.split("=", 1)[1].strip()
                if tail.isdigit():
                    return int(tail)
                return None
        return 0  # set not yet created -> empty/no objects
    return _probe


def _staging_probe(kube: Kube, knobs: StagingDrainConfig) -> CountFn:
    """SUM(n_tup_ins) over pg_stat_user_tables in the staging schema, with
    optional table excludes. Identical SQL semantics to the bash version."""
    if knobs.exclude_tables:
        quoted = ",".join("'" + t.replace("'", "''") + "'" for t in knobs.exclude_tables)
        where = (f"schemaname='{knobs.schema}' AND relname NOT IN ({quoted})")
    else:
        where = f"schemaname='{knobs.schema}'"
    sql = (f"SELECT COALESCE(SUM(n_tup_ins),0) "
           f"FROM pg_stat_user_tables WHERE {where};")
    inner = (f"psql -U \"${knobs.user_env}\" -d \"${knobs.name_env}\" "
             f"-At -c \"{sql}\"")

    def _probe() -> Optional[int]:
        try:
            txt = kube.exec_capture(
                f"deploy/{knobs.deployment}",
                "bash", "-c", inner,
                check=False,
            )
        except SystemExit:
            return None
        s = "".join(txt.split())
        if s.isdigit():
            return int(s)
        return None
    return _probe


def _omop_probe(kube: Kube, cfg: Config, knobs: OMOPDrainConfig,
                pod: str) -> CountFn:
    sql = f"SELECT COUNT(*) FROM {cfg.omop.schema}.{knobs.table};"

    def _probe() -> Optional[int]:
        try:
            txt = kube.exec_capture(
                pod,
                "psql", "-U", cfg.omop.db_admin_user,
                "-d", cfg.omop.db_name,
                "-At", "-c", sql,
                container=cfg.omop.db_container or None,
                check=False,
            )
        except SystemExit:
            return None
        s = "".join(txt.split())
        if s.isdigit():
            return int(s)
        return None
    return _probe


# ----------------------------------------------------------------------- API
def wait_aerospike(cfg: Config, kube: Kube, *, dry_run: bool = False) -> None:
    if dry_run:
        log("[dry-run] Skip Aerospike upload-buffer drain wait.")
        return
    knobs = cfg.aerospike_drain
    label = f"Aerospike upload buffer (deploy/{knobs.deployment}, set={knobs.as_set})"
    probe = _aerospike_probe(kube, knobs)

    def _line(count: int, delta: int, elapsed: int) -> str:
        return (f"Aerospike {knobs.as_set}: {count} records  "
                f"(+{delta} since last poll, elapsed={elapsed}s)")

    _drain_loop(label=label, knobs=knobs, probe=probe, on_count_log=_line)


def wait_staging(cfg: Config, kube: Kube, *, dry_run: bool = False) -> None:
    if dry_run:
        log("[dry-run] Skip staging drain wait.")
        return
    knobs = cfg.staging_drain
    label = f"staging Postgres (deploy/{knobs.deployment})"

    def _line(count: int, delta: int, elapsed: int) -> str:
        return (f"Staging inserts: {count}  "
                f"(+{delta} since last poll, elapsed={elapsed}s)")

    probe = _staging_probe(kube, knobs)
    _drain_loop(label=label, knobs=knobs, probe=probe, on_count_log=_line)


def wait_omop(cfg: Config, kube: Kube, pod: str, *, dry_run: bool = False) -> None:
    if dry_run:
        log("[dry-run] Skip OMOP drain wait.")
        return
    knobs = cfg.omop_drain
    label = f"OMOP CDM {cfg.omop.schema}.{knobs.table}"

    def _line(count: int, delta: int, elapsed: int) -> str:
        return (f"OMOP {cfg.omop.schema}.{knobs.table}: {count}  "
                f"(+{delta} since last poll, elapsed={elapsed}s)")

    probe = _omop_probe(kube, cfg, knobs, pod)
    _drain_loop(label=label, knobs=knobs, probe=probe, on_count_log=_line)

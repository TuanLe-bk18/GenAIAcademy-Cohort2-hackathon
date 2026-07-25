#!/usr/bin/env python3
"""Phase 5R replay evidence evaluator and provider-run safety contract.

The provider driver writes one JSON evidence document containing correlated
Cloud Run attempts, BigQuery byte observations, replay manifests, and cleanup
targets.  This module deliberately keeps all acceptance logic pure so it can be
proved locally before a billed 96+1 replay is allowed.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
from math import ceil
from pathlib import Path
import re
import subprocess
from statistics import median
import time
from typing import Any, Callable, Iterable, Mapping, Sequence


TAG_RE = re.compile(r"^[0-9]{14}$")
DATASET_RE = re.compile(r"^heatsafe_phase5r_probe_([0-9]{14})$")
JOB_RE = re.compile(r"^heatsafe-phase5r-([0-9]{14})$")
BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*-heatsafe-phase5r-([0-9]{14})$")
RUN_ID_RE = re.compile(r"^[a-f0-9]{32}$")


class EvidenceError(RuntimeError):
    """Evidence is incomplete, unsafe, or fails an acceptance gate."""


@dataclass
class CumulativeByteBudget:
    """Reserve bounded query bytes before dispatch, then settle actual billing."""

    maximum: int
    observed: int = 0
    reserved: int = 0

    def reserve(self, upper_bound: int) -> None:
        if upper_bound <= 0:
            raise ValueError("query byte upper bound must be positive")
        if self.observed + self.reserved + upper_bound > self.maximum:
            raise EvidenceError("cumulative byte budget exhausted before dispatch")
        self.reserved += upper_bound

    def settle(self, upper_bound: int, billed: int) -> None:
        if upper_bound <= 0 or billed < 0 or billed > upper_bound:
            raise ValueError("invalid query byte settlement")
        if upper_bound > self.reserved:
            raise ValueError("query was not reserved before dispatch")
        self.reserved -= upper_bound
        self.observed += billed


def nearest_rank(values: Iterable[float], percentile: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise EvidenceError("cannot aggregate an empty sample")
    if not 0 < percentile <= 100:
        raise ValueError("percentile must be in (0, 100]")
    return ordered[ceil(percentile / 100 * len(ordered)) - 1]


def timing_summary(values: Iterable[float]) -> dict[str, float]:
    sample = list(values)
    return {
        "count": len(sample),
        "p50_seconds": nearest_rank(sample, 50),
        "p95_seconds": nearest_rank(sample, 95),
        "max_seconds": max(sample),
    }


def _instant(value: object) -> datetime:
    if not isinstance(value, str):
        raise EvidenceError("attempt timestamp is missing")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceError("attempt timestamp is invalid") from exc


def correlate_attempts(
    attempts: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Correlate attempts by execution and reject overlap/duplicate success."""

    executions: dict[str, Mapping[str, object]] = {}
    successful_ticks: dict[int, str] = {}
    intervals: list[tuple[datetime, datetime, str]] = []
    for attempt in attempts:
        execution = str(attempt.get("cloud_run_execution") or "")
        if not execution:
            raise EvidenceError("attempt lacks cloud_run_execution")
        prior = executions.get(execution)
        if prior is not None and prior != attempt:
            raise EvidenceError("one Cloud Run execution maps to multiple records")
        executions[execution] = attempt
        start = _instant(attempt.get("dispatch_at"))
        end = _instant(attempt.get("terminal_at"))
        if end < start:
            raise EvidenceError("attempt terminal precedes dispatch")
        intervals.append((start, end, execution))
        if attempt.get("outcome") == "SUCCEEDED":
            tick_index = int(attempt["tick_index"])
            prior_execution = successful_ticks.get(tick_index)
            if prior_execution and prior_execution != execution:
                raise EvidenceError("duplicate successful logical tick")
            successful_ticks[tick_index] = execution

    intervals.sort()
    overlaps: list[tuple[str, str]] = []
    for previous, current in zip(intervals, intervals[1:]):
        if current[0] < previous[1]:
            overlaps.append((previous[2], current[2]))
    if overlaps:
        raise EvidenceError(f"overlapping Cloud Run attempts: {overlaps}")
    return {
        "execution_count": len(executions),
        "successful_tick_count": len(successful_ticks),
        "zero_overlap": True,
        "zero_duplicate_success": True,
    }


def full_tick_summary(
    attempts: Sequence[Mapping[str, object]], required_ticks: Iterable[int]
) -> dict[str, float]:
    required = set(required_ticks)
    full = {
        int(item["tick_index"]): item
        for item in attempts
        if item.get("execution_mode") == "FULL"
        and item.get("outcome") == "SUCCEEDED"
        and int(item["tick_index"]) in required
    }
    missing = sorted(required - set(full))
    if missing:
        raise EvidenceError(f"required persisted FULL ticks are missing: {missing}")
    durations = [
        (_instant(item["terminal_at"]) - _instant(item["dispatch_at"])).total_seconds()
        for item in full.values()
    ]
    return timing_summary(durations)


def checkpoint_timing_summary(
    values: Sequence[float], *, maximum_p95_seconds: float
) -> dict[str, float]:
    if len(values) < 20:
        raise EvidenceError("checkpoint timing requires at least 20 samples")
    summary = timing_summary(values)
    if summary["p95_seconds"] > maximum_p95_seconds:
        raise EvidenceError("checkpoint restore/upload/verify p95 exceeds gate")
    return summary


def verify_terminal_noop(
    before: Mapping[str, object],
    after: Mapping[str, object],
    invocation: Mapping[str, object],
) -> None:
    if before != after:
        raise EvidenceError("invocation 97 mutated the completed replay manifest")
    if invocation.get("outcome") != "NO_OP_TERMINAL":
        raise EvidenceError("invocation 97 is not a terminal no-op")
    if invocation.get("terminal_signal") is not True:
        raise EvidenceError("invocation 97 lacks terminal signal")


def exact_disposable_tag(
    *, dataset: str, bucket: str, job_prefix: str
) -> str:
    matches = (
        DATASET_RE.fullmatch(dataset),
        BUCKET_RE.fullmatch(bucket),
        JOB_RE.fullmatch(job_prefix),
    )
    if any(match is None for match in matches):
        raise EvidenceError("resources are not exact disposable Phase 5R targets")
    tags = {match.group(1) for match in matches if match is not None}
    if len(tags) != 1:
        raise EvidenceError("disposable resource tags do not match")
    return tags.pop()


def validate_cleanup_targets(targets: Sequence[str], *, tag: str) -> None:
    if not TAG_RE.fullmatch(tag):
        raise EvidenceError("cleanup tag is invalid")
    if not targets:
        raise EvidenceError("cleanup has no exact targets")
    if any(tag not in target for target in targets):
        raise EvidenceError("cleanup target is outside the disposable run tag")
    if len(set(targets)) != len(targets):
        raise EvidenceError("cleanup targets contain duplicates")


def live_resource_names(tag: str) -> dict[str, str]:
    """Resolve only the exact run-tagged resources created by the deploy script."""

    if not TAG_RE.fullmatch(tag):
        raise EvidenceError("live provider tag is invalid")
    return {
        "tick_job": f"heatsafe-simulation-tick-{tag}",
        "scheduler_job": f"heatsafe-simulation-replay-2m-{tag}",
    }


def require_remaining_budget(
    *,
    observed: int,
    last_completed_tick: int,
    per_tick_upper_bound: int,
    maximum: int,
) -> dict[str, int]:
    """Reserve the worst case for every remaining logical tick before resume."""

    if observed < 0 or per_tick_upper_bound <= 0 or maximum <= 0:
        raise ValueError("provider byte limits must be positive")
    remaining_ticks = max(0, 95 - last_completed_tick)
    reserved = remaining_ticks * per_tick_upper_bound
    if observed + reserved > maximum:
        raise EvidenceError(
            "remaining replay cannot be dispatched within cumulative byte budget"
        )
    return {
        "observed": observed,
        "remaining_ticks": remaining_ticks,
        "reserved": reserved,
        "maximum": maximum,
    }


def parallel_decision(
    serial_seconds: Sequence[float],
    parallel_seconds: Sequence[float],
    *,
    trigger_seconds: float,
    minimum_improvement_pct: float,
    equivalent_results: bool,
    anomaly_count: int,
) -> dict[str, object]:
    serial = timing_summary(serial_seconds)
    triggered = serial["p95_seconds"] > trigger_seconds
    result: dict[str, object] = {
        "triggered": triggered,
        "selected_mode": "serial",
        "serial": serial,
    }
    if not triggered:
        return result
    if len(serial_seconds) < 10 or len(parallel_seconds) < 10:
        result["reason"] = "minimum ten paired complete attempts per mode"
        return result
    parallel = timing_summary(parallel_seconds)
    p50_gain = 100 * (
        serial["p50_seconds"] - parallel["p50_seconds"]
    ) / serial["p50_seconds"]
    p95_gain = 100 * (
        serial["p95_seconds"] - parallel["p95_seconds"]
    ) / serial["p95_seconds"]
    accepted = (
        p50_gain >= minimum_improvement_pct
        and p95_gain >= minimum_improvement_pct
        and equivalent_results
        and anomaly_count == 0
    )
    result.update(
        {
            "parallel": parallel,
            "p50_improvement_pct": p50_gain,
            "p95_improvement_pct": p95_gain,
            "equivalent_results": equivalent_results,
            "anomaly_count": anomaly_count,
            "selected_mode": "parallel" if accepted else "serial",
        }
    )
    return result


def evaluate_evidence(args: argparse.Namespace, evidence: Mapping[str, object]) -> dict[str, object]:
    attempts = evidence.get("attempts")
    if not isinstance(attempts, list):
        raise EvidenceError("evidence attempts must be a list")
    correlation = correlate_attempts(attempts)
    full = full_tick_summary(attempts, args.full_ticks)
    if full["p95_seconds"] > args.max_full_p95_seconds:
        raise EvidenceError("corrected FULL tick p95 exceeds replay gate")
    if full["max_seconds"] >= args.max_tick_seconds:
        raise EvidenceError("dispatch-to-terminal maximum reaches cadence")
    before = evidence.get("manifest_before_97")
    after = evidence.get("manifest_after_97")
    invocation = evidence.get("invocation_97")
    if args.invoke_terminal_noop:
        if not all(isinstance(value, Mapping) for value in (before, after, invocation)):
            raise EvidenceError("96+1 manifest evidence is incomplete")
        verify_terminal_noop(before, after, invocation)  # type: ignore[arg-type]
    billed = int(evidence.get("total_bytes_billed") or 0)
    if billed > args.maximum_replay_bytes_billed:
        raise EvidenceError("cumulative replay bytes exceed ceiling")
    checkpoint_values = evidence.get("checkpoint_seconds")
    if not isinstance(checkpoint_values, list):
        raise EvidenceError("checkpoint timing evidence is missing")
    checkpoint = checkpoint_timing_summary(
        [float(value) for value in checkpoint_values],
        maximum_p95_seconds=args.max_checkpoint_p95_seconds,
    )
    return {
        "outcome": "PASS",
        "full": full,
        "checkpoint": checkpoint,
        "correlation": correlation,
        "total_bytes_billed": billed,
        "scheduler_cadence": args.scheduler_cadence,
    }


class ProviderDriver:
    """Operate one exact disposable Scheduler and persist its raw evidence."""

    def __init__(
        self,
        args: argparse.Namespace,
        *,
        run_command: Callable[[Sequence[str]], str] | None = None,
    ) -> None:
        self.args = args
        self.tag = exact_disposable_tag(
            dataset=args.dataset,
            bucket=args.bucket,
            job_prefix=args.disposable_job_prefix,
        )
        self.resources = live_resource_names(self.tag)
        self._run_command = run_command or self._subprocess

    @staticmethod
    def _subprocess(command: Sequence[str]) -> str:
        result = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            detail = result.stderr.strip() or result.stdout.strip()
            raise EvidenceError(
                f"provider command failed ({command[0]}): {detail}"
            )
        return result.stdout

    def _json(self, command: Sequence[str]) -> Any:
        output = self._run_command(command)
        try:
            return json.loads(output or "null")
        except json.JSONDecodeError as exc:
            raise EvidenceError(
                f"provider command returned invalid JSON: {command[0]}"
            ) from exc

    def scheduler(self) -> Mapping[str, object]:
        payload = self._json(
            [
                "gcloud",
                "scheduler",
                "jobs",
                "describe",
                self.resources["scheduler_job"],
                f"--project={self.args.project}",
                f"--location={self.args.region}",
                "--format=json",
            ]
        )
        if not isinstance(payload, Mapping):
            raise EvidenceError("Scheduler describe payload is invalid")
        return payload

    def pause(self) -> None:
        self._run_command(
            [
                "gcloud",
                "scheduler",
                "jobs",
                "pause",
                self.resources["scheduler_job"],
                f"--project={self.args.project}",
                f"--location={self.args.region}",
                "--quiet",
            ]
        )

    def resume(self) -> None:
        self._run_command(
            [
                "gcloud",
                "scheduler",
                "jobs",
                "resume",
                self.resources["scheduler_job"],
                f"--project={self.args.project}",
                f"--location={self.args.region}",
                "--quiet",
            ]
        )

    def _bq_rows(self, sql: str) -> list[Mapping[str, object]]:
        payload = self._json(
            [
                "bq",
                "query",
                f"--project_id={self.args.project}",
                f"--location={self.args.region}",
                "--use_legacy_sql=false",
                "--format=json",
                "--maximum_bytes_billed=50000000",
                sql,
            ]
        )
        if not isinstance(payload, list) or any(
            not isinstance(item, Mapping) for item in payload
        ):
            raise EvidenceError("BigQuery evidence payload is invalid")
        return payload

    def manifest(self, run_id: str | None = None) -> dict[str, object]:
        where = ""
        if run_id:
            if not RUN_ID_RE.fullmatch(run_id):
                raise EvidenceError("simulation run id is invalid")
            where = f" WHERE simulation_run_id = '{run_id}'"
        rows = self._bq_rows(
            "SELECT TO_JSON_STRING(t) AS manifest_json "
            f"FROM `{self.args.project}.{self.args.dataset}.simulation_runs` AS t"
            f"{where}"
        )
        if len(rows) != 1 or not isinstance(rows[0].get("manifest_json"), str):
            raise EvidenceError("disposable dataset must contain exactly one run")
        manifest = json.loads(str(rows[0]["manifest_json"]))
        if not isinstance(manifest, dict):
            raise EvidenceError("simulation run manifest is invalid")
        return manifest

    def provider_total_bytes_billed(self) -> int:
        created = datetime.strptime(self.tag, "%Y%m%d%H%M%S").isoformat()
        rows = self._bq_rows(
            "SELECT COALESCE(SUM(total_bytes_billed), 0) AS billed "
            f"FROM `{self.args.project}.region-{self.args.region}."
            "INFORMATION_SCHEMA.JOBS_BY_PROJECT` "
            f"WHERE user_email = '{self.args.runtime_service_account}' "
            "AND parent_job_id IS NULL "
            f"AND creation_time >= TIMESTAMP('{created}+00:00')"
        )
        if len(rows) != 1:
            raise EvidenceError("provider byte aggregate is invalid")
        return int(rows[0].get("billed") or 0)

    def telemetry(self) -> list[Mapping[str, object]]:
        payload = self._json(
            [
                "gcloud",
                "logging",
                "read",
                (
                    'jsonPayload.cloud_run_job="'
                    f'{self.resources["tick_job"]}"'
                ),
                f"--project={self.args.project}",
                "--freshness=24h",
                "--limit=5000",
                "--format=json",
            ]
        )
        if not isinstance(payload, list):
            raise EvidenceError("Cloud Logging evidence payload is invalid")
        return [item for item in payload if isinstance(item, Mapping)]

    def executions(self) -> list[Mapping[str, object]]:
        payload = self._json(
            [
                "gcloud",
                "run",
                "jobs",
                "executions",
                "list",
                f'--job={self.resources["tick_job"]}',
                f"--project={self.args.project}",
                f"--region={self.args.region}",
                "--limit=200",
                "--format=json",
            ]
        )
        if not isinstance(payload, list):
            raise EvidenceError("Cloud Run execution payload is invalid")
        return [item for item in payload if isinstance(item, Mapping)]

    @staticmethod
    def _payload(entry: Mapping[str, object]) -> Mapping[str, object]:
        payload = entry.get("jsonPayload")
        return payload if isinstance(payload, Mapping) else {}

    def progress(
        self, telemetry: Sequence[Mapping[str, object]], run_id: str
    ) -> dict[str, int]:
        completed: set[int] = set()
        billed_jobs: dict[str, int] = {}
        for entry in telemetry:
            payload = self._payload(entry)
            if payload.get("simulation_run_id") != run_id:
                continue
            if (
                payload.get("event") == "simulation_tick_component"
                and payload.get("component") == "tick_total"
                and payload.get("outcome") == "SUCCEEDED"
            ):
                completed.add(int(payload["tick_index"]))
            job_id = payload.get("bigquery_job_id")
            billed = payload.get("total_bytes_billed")
            if job_id and billed is not None:
                billed_jobs[str(job_id)] = int(billed)
        return {
            "last_completed_tick": max(completed, default=-1),
            "successful_tick_count": len(completed),
            "total_bytes_billed": sum(billed_jobs.values()),
        }

    @staticmethod
    def active_execution_count(
        executions: Sequence[Mapping[str, object]],
    ) -> int:
        active = 0
        for execution in executions:
            status = execution.get("status")
            if not isinstance(status, Mapping):
                continue
            if not status.get("completionTime") and not status.get("failedCount"):
                active += 1
        return active

    def _attempts(
        self,
        telemetry: Sequence[Mapping[str, object]],
        executions: Sequence[Mapping[str, object]],
        run_id: str,
    ) -> list[dict[str, object]]:
        ticks: dict[str, Mapping[str, object]] = {}
        for entry in telemetry:
            payload = self._payload(entry)
            if (
                payload.get("simulation_run_id") == run_id
                and payload.get("event") == "simulation_tick_component"
                and payload.get("component") == "tick_total"
            ):
                execution_name = str(
                    payload.get("cloud_run_execution") or ""
                )
                prior = ticks.get(execution_name)
                if (
                    prior is None
                    or payload.get("outcome") == "SUCCEEDED"
                    or prior.get("outcome") != "SUCCEEDED"
                ):
                    ticks[execution_name] = payload
        attempts: list[dict[str, object]] = []
        for execution in executions:
            metadata = execution.get("metadata")
            status = execution.get("status")
            if not isinstance(metadata, Mapping) or not isinstance(status, Mapping):
                continue
            name = str(metadata.get("name") or "")
            tick = ticks.get(name)
            if tick is None or not status.get("completionTime"):
                continue
            attempts.append(
                {
                    "tick_index": int(tick["tick_index"]),
                    "execution_mode": tick.get("execution_mode"),
                    "cloud_run_execution": name,
                    "dispatch_at": metadata.get("creationTimestamp"),
                    "terminal_at": status.get("completionTime"),
                    "outcome": tick.get("outcome"),
                }
            )
        return attempts

    def _checkpoint_seconds(
        self,
        telemetry: Sequence[Mapping[str, object]],
        run_id: str,
    ) -> list[float]:
        components: dict[str, dict[str, int]] = {}
        for entry in telemetry:
            payload = self._payload(entry)
            if (
                payload.get("simulation_run_id") != run_id
                or payload.get("outcome") not in {"SUCCEEDED", "NO_OP"}
            ):
                continue
            component = str(payload.get("component") or "")
            if component not in {
                "checkpoint_restore",
                "checkpoint_upload",
                "checkpoint_readback",
            }:
                continue
            execution = str(payload.get("cloud_run_execution") or "")
            components.setdefault(execution, {})[component] = int(
                payload.get("elapsed_ms") or 0
            )
        return [
            sum(sample.values()) / 1_000
            for sample in components.values()
            if "checkpoint_restore" in sample
            and "checkpoint_upload" in sample
            and "checkpoint_readback" in sample
        ]

    def _invoke_terminal_noop(self) -> str:
        before = {
            str(
                (
                    item.get("metadata")
                    if isinstance(item.get("metadata"), Mapping)
                    else {}
                ).get("name")
            )
            for item in self.executions()
        }
        self._run_command(
            [
                "gcloud",
                "run",
                "jobs",
                "execute",
                self.resources["tick_job"],
                f"--project={self.args.project}",
                f"--region={self.args.region}",
                "--wait",
                "--quiet",
            ]
        )
        after = self.executions()
        names = [
            str(item.get("metadata", {}).get("name"))
            for item in after
            if isinstance(item.get("metadata"), Mapping)
            and str(item.get("metadata", {}).get("name")) not in before
        ]
        if len(names) != 1:
            raise EvidenceError("could not identify exact invocation 97 execution")
        return names[0]

    def run(self) -> dict[str, object]:
        scheduler = self.scheduler()
        if scheduler.get("schedule") != self.args.scheduler_cadence:
            raise EvidenceError("disposable Scheduler cadence does not match proof")
        if scheduler.get("state") != "PAUSED":
            raise EvidenceError("provider driver requires Scheduler PAUSED at entry")

        manifest_before = self.manifest()
        run_id = str(manifest_before.get("simulation_run_id") or "")
        if not RUN_ID_RE.fullmatch(run_id):
            raise EvidenceError("disposable manifest has invalid run id")
        initial = self.progress(self.telemetry(), run_id)
        initial["total_bytes_billed"] = self.provider_total_bytes_billed()
        require_remaining_budget(
            observed=initial["total_bytes_billed"],
            last_completed_tick=initial["last_completed_tick"],
            per_tick_upper_bound=self.args.per_tick_query_upper_bound,
            maximum=self.args.maximum_replay_bytes_billed,
        )

        deadline = time.monotonic() + self.args.provider_timeout_seconds
        last_reported_tick = initial["last_completed_tick"]
        print(
            json.dumps(
                {"event": "provider_resume", "run_id": run_id, **initial},
                sort_keys=True,
            ),
            flush=True,
        )
        self.resume()
        try:
            while True:
                telemetry = self.telemetry()
                executions = self.executions()
                if self.active_execution_count(executions) > 1:
                    raise EvidenceError("more than one Cloud Run execution is active")
                progress = self.progress(telemetry, run_id)
                if progress["last_completed_tick"] != last_reported_tick:
                    progress["total_bytes_billed"] = (
                        self.provider_total_bytes_billed()
                    )
                    require_remaining_budget(
                        observed=progress["total_bytes_billed"],
                        last_completed_tick=progress["last_completed_tick"],
                        per_tick_upper_bound=(
                            self.args.per_tick_query_upper_bound
                        ),
                        maximum=self.args.maximum_replay_bytes_billed,
                    )
                    print(
                        json.dumps(
                            {"event": "provider_progress", **progress},
                            sort_keys=True,
                        ),
                        flush=True,
                    )
                    last_reported_tick = progress["last_completed_tick"]
                if progress["last_completed_tick"] >= 95:
                    break
                if (
                    progress["total_bytes_billed"]
                    + self.args.per_tick_query_upper_bound
                    > self.args.maximum_replay_bytes_billed
                ):
                    raise EvidenceError(
                        "cumulative byte budget exhausted before next Scheduler dispatch"
                    )
                if time.monotonic() >= deadline:
                    raise EvidenceError("provider replay timed out")
                time.sleep(self.args.provider_poll_seconds)
        finally:
            self.pause()
            print(
                json.dumps(
                    {
                        "event": "provider_scheduler_paused",
                        "scheduler_job": self.resources["scheduler_job"],
                    },
                    sort_keys=True,
                ),
                flush=True,
            )

        manifest_before_97 = self.manifest(run_id)
        invocation_execution = self._invoke_terminal_noop()
        manifest_after_97 = self.manifest(run_id)
        telemetry = self.telemetry()
        executions = self.executions()
        terminal = next(
            (
                self._payload(item)
                for item in telemetry
                if self._payload(item).get("event")
                in {"simulation_tick_completed", "simulation_tick_terminal"}
                and self._payload(item).get("cloud_run_execution")
                == invocation_execution
            ),
            None,
        )
        if terminal is None:
            raise EvidenceError("invocation 97 completion telemetry is missing")
        progress = self.progress(telemetry, run_id)
        progress["total_bytes_billed"] = self.provider_total_bytes_billed()
        evidence = {
            "attempts": self._attempts(telemetry, executions, run_id),
            "checkpoint_seconds": self._checkpoint_seconds(
                telemetry, run_id
            ),
            "manifest_before_97": manifest_before_97,
            "manifest_after_97": manifest_after_97,
            "invocation_97": {
                "outcome": (
                    "NO_OP_TERMINAL"
                    if terminal.get("terminal_signal") is True
                    else terminal.get("status")
                ),
                "terminal_signal": terminal.get("terminal_signal"),
                "cloud_run_execution": invocation_execution,
            },
            "total_bytes_billed": progress["total_bytes_billed"],
            "resource_tag": self.tag,
            "resources": self.resources,
            "scheduler_profile": "accelerated-replay",
            "scheduler_cadence": self.args.scheduler_cadence,
            "simulation_run_id": run_id,
            "image": self.args.image,
        }
        return evidence


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--disposable-job-prefix", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--full-ticks", required=True)
    parser.add_argument("--oracle-sentinels", required=True)
    parser.add_argument("--scheduler-cadence", required=True)
    parser.add_argument("--max-full-p95-seconds", type=float, required=True)
    parser.add_argument("--max-tick-seconds", type=float, required=True)
    parser.add_argument("--max-checkpoint-p95-seconds", type=float, default=3.0)
    parser.add_argument("--parallel-scoring-policy", choices=("conditional",), required=True)
    parser.add_argument("--parallel-trigger-seconds", type=float, required=True)
    parser.add_argument("--min-parallel-improvement-pct", type=float, required=True)
    parser.add_argument("--invoke-terminal-noop", action="store_true")
    parser.add_argument("--maximum-replay-bytes-billed", type=int, required=True)
    parser.add_argument("--enforce-cumulative-budget", action="store_true")
    parser.add_argument("--evidence-json", type=Path)
    parser.add_argument("--write-evidence-json", type=Path)
    parser.add_argument("--runtime-service-account")
    parser.add_argument(
        "--per-tick-query-upper-bound", type=int, default=500_000_000
    )
    parser.add_argument("--provider-poll-seconds", type=float, default=15.0)
    parser.add_argument("--provider-timeout-seconds", type=float, default=12_000.0)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if not args.image.startswith("sha256:") and "@sha256:" not in args.image:
        parser.error("--image must be an immutable digest")
    try:
        args.full_ticks = tuple(int(item) for item in args.full_ticks.split(","))
        args.oracle_sentinels = tuple(
            int(item) for item in args.oracle_sentinels.split(",")
        )
    except ValueError as exc:
        parser.error(f"tick lists must contain integers: {exc}")
    if args.oracle_sentinels != (0, 24, 48, 95):
        parser.error("--oracle-sentinels must be exactly 0,24,48,95")
    if args.scheduler_cadence != "*/2 * * * *":
        parser.error("--scheduler-cadence must be the accelerated two-minute profile")
    exact_disposable_tag(
        dataset=args.dataset,
        bucket=args.bucket,
        job_prefix=args.disposable_job_prefix,
    )
    if not args.execute or not args.enforce_cumulative_budget:
        parser.error("--execute and --enforce-cumulative-budget are required")
    if args.provider_poll_seconds <= 0 or args.provider_timeout_seconds <= 0:
        parser.error("provider polling and timeout values must be positive")
    if args.runtime_service_account is None:
        args.runtime_service_account = (
            f"heatsafe-sim-runtime@{args.project}.iam.gserviceaccount.com"
        )
    return args


def main() -> None:
    args = parse_args()
    if args.evidence_json is not None:
        evidence = json.loads(args.evidence_json.read_text(encoding="utf-8"))
    else:
        evidence = ProviderDriver(args).run()
        if args.write_evidence_json is not None:
            args.write_evidence_json.write_text(
                json.dumps(evidence, indent=2, sort_keys=True),
                encoding="utf-8",
            )
    result = evaluate_evidence(args, evidence)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()

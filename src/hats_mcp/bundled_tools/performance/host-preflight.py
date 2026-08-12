#!/usr/bin/env python3
# ---
# id: performance.host-preflight
# name: Host Performance Preflight
# description: Check whether a Linux host is quiet enough for representative performance measurements without changing workloads.
# domain: performance
# interpreter: python3
# requires: [linux]
# mutating: false
# idempotent: true
# timeout_seconds: 240
# arguments:
#   - name: samples
#     type: integer
#     required: false
#     minimum: 1
#     maximum: 20
#   - name: interval_ms
#     type: integer
#     required: false
#     minimum: 100
#     maximum: 10000
#   - name: max_cpu_percent
#     type: integer
#     required: false
#     minimum: 0
#     maximum: 100
#   - name: max_load_per_cpu_percent
#     type: integer
#     required: false
#     minimum: 0
#     maximum: 10000
#   - name: max_container_cpu_percent
#     type: integer
#     required: false
#     minimum: 0
#     maximum: 10000
#   - name: docker
#     type: string
#     required: false
#     enum: [auto, required, "off"]
#   - name: top_containers
#     type: integer
#     required: false
#     minimum: 1
#     maximum: 20
# ---
"""Read-only host-load admission check for representative performance measurements."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Iterable

SCHEMA_VERSION = "1"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_proc_stat(line: str) -> tuple[int, int]:
    parts = line.split()
    if len(parts) < 5 or parts[0] != "cpu":
        raise ValueError("invalid aggregate /proc/stat CPU line")
    values = [int(value) for value in parts[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values), idle


def read_cpu_snapshot() -> tuple[int, int]:
    with open("/proc/stat", "r", encoding="utf-8") as handle:
        return parse_proc_stat(handle.readline())


def cpu_busy_percent(before: tuple[int, int], after: tuple[int, int]) -> float:
    total_delta = after[0] - before[0]
    idle_delta = after[1] - before[1]
    if total_delta <= 0 or idle_delta < 0:
        raise ValueError("non-increasing CPU counters")
    return max(0.0, min(100.0, (total_delta - idle_delta) * 100.0 / total_delta))


def sample_cpu(sample_count: int, interval_seconds: float) -> list[float]:
    samples: list[float] = []
    previous = read_cpu_snapshot()
    for _ in range(sample_count):
        time.sleep(interval_seconds)
        current = read_cpu_snapshot()
        samples.append(round(cpu_busy_percent(previous, current), 2))
        previous = current
    return samples


def read_load1() -> float:
    with open("/proc/loadavg", "r", encoding="utf-8") as handle:
        return float(handle.read().split(maxsplit=1)[0])


def parse_percent(value: str) -> float:
    return float(value.strip().rstrip("%").replace(",", "."))


def docker_consumers(mode: str, limit: int) -> dict[str, object]:
    docker = shutil.which("docker")
    if mode == "off":
        return {"available": False, "mode": "off", "top": []}
    if docker is None:
        if mode == "required":
            raise RuntimeError("docker command is required but not available")
        return {"available": False, "mode": "auto", "reason": "docker-command-unavailable", "top": []}

    try:
        completed = subprocess.run(
            [docker, "stats", "--no-stream", "--format", "{{json .}}"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        if mode == "required":
            raise RuntimeError(f"docker stats failed: {type(exc).__name__}") from exc
        return {"available": False, "mode": "auto", "reason": "docker-stats-unavailable", "top": []}

    rows: list[dict[str, object]] = []
    for raw_line in completed.stdout.splitlines():
        if not raw_line.strip():
            continue
        try:
            item = json.loads(raw_line)
            name = str(item.get("Name") or item.get("Container") or "unknown")[:128]
            cpu = parse_percent(str(item.get("CPUPerc", "0")))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        rows.append({"name": name, "cpu_percent": round(cpu, 2)})

    rows.sort(key=lambda row: float(row["cpu_percent"]), reverse=True)
    return {"available": True, "mode": mode, "top": rows[:limit]}


def evaluate(
    *,
    cpu_samples: Iterable[float],
    load_per_cpu: float,
    docker: dict[str, object],
    max_cpu_percent: float,
    max_load_per_cpu: float,
    max_container_cpu_percent: float,
) -> list[str]:
    reasons: list[str] = []
    samples = list(cpu_samples)
    if samples and max(samples) > max_cpu_percent:
        reasons.append("host-cpu-busy")
    if load_per_cpu > max_load_per_cpu:
        reasons.append("host-load-busy")
    if docker.get("available"):
        top = docker.get("top") or []
        if any(float(row["cpu_percent"]) > max_container_cpu_percent for row in top):
            reasons.append("container-cpu-busy")
    return reasons


def run(args: argparse.Namespace) -> int:
    if not Path("/proc/stat").is_file() or not Path("/proc/loadavg").is_file():
        print(json.dumps({"status": "error", "error": "Linux /proc CPU/load metrics are required"}, sort_keys=True))
        return 2

    cpu_count = os.cpu_count() or 1
    try:
        cpu_samples = sample_cpu(args.samples, args.interval_ms / 1000.0)
        load1 = read_load1()
        docker = docker_consumers(args.docker, args.top_containers)
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
        return 2

    load_per_cpu = round(load1 / cpu_count, 3)
    max_load_per_cpu = args.max_load_per_cpu_percent / 100.0
    reasons = evaluate(
        cpu_samples=cpu_samples,
        load_per_cpu=load_per_cpu,
        docker=docker,
        max_cpu_percent=args.max_cpu_percent,
        max_load_per_cpu=max_load_per_cpu,
        max_container_cpu_percent=args.max_container_cpu_percent,
    )
    eligible = not reasons
    payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "checked_at": utc_now(),
        "eligible": eligible,
        "status": "ready" if eligible else "measurement-window-busy",
        "reasons": reasons,
        "thresholds": {
            "max_cpu_percent": args.max_cpu_percent,
            "max_load_per_cpu": max_load_per_cpu,
            "max_container_cpu_percent": args.max_container_cpu_percent,
        },
        "host": {
            "cpu_count": cpu_count,
            "cpu_samples_percent": cpu_samples,
            "cpu_max_percent": max(cpu_samples) if cpu_samples else 0.0,
            "cpu_average_percent": round(sum(cpu_samples) / len(cpu_samples), 2) if cpu_samples else 0.0,
            "load1": round(load1, 3),
            "load1_per_cpu": load_per_cpu,
        },
        "docker": docker,
    }
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0 if eligible else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--interval-ms", type=int, default=500)
    parser.add_argument("--max-cpu-percent", type=int, default=50)
    parser.add_argument("--max-load-per-cpu-percent", type=int, default=75)
    parser.add_argument("--max-container-cpu-percent", type=int, default=25)
    parser.add_argument("--docker", choices=("auto", "required", "off"), default="auto")
    parser.add_argument("--top-containers", type=int, default=5)
    return parser.parse_args()


def main() -> int:
    return run(parse_args())


if __name__ == "__main__":
    sys.exit(main())

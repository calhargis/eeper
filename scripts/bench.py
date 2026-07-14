#!/usr/bin/env python3
"""Reference-profile benchmark harness (M5.2).

Measures a running eeper stack against the release gate from MASTER_PLAN §8/§9:

* **steady-state CPU** — mean total container CPU as a fraction of host capacity,
  sampled over the run (budget: < 60 % on the reference Pi 5 profile);
* **HTTP latency** — the API/page-load proxy (budget: < 3 s cold page load);
* **stability** — no container restart and no OOM-kill over the run (the 72 h soak on
  the bench runner asserts this over a long window).

It is the SAME harness in two modes: a short `--smoke` run validates the machinery in CI
(on a non-Pi runner, so CPU/latency budgets are relaxed), and a full run on the
self-hosted bench runner enforces the real reference-profile thresholds over 72 h.

Pure stdlib + the `docker` CLI; no third-party deps so it runs anywhere the stack does.
Emits a JSON report to stdout and exits non-zero on any breached budget.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from dataclasses import asdict, dataclass, field


@dataclass
class Sample:
    ts: float
    cpu_pct: float  # summed container CPU% / (cores*100) * 100 → % of host capacity


@dataclass
class Report:
    duration_s: float
    cores: int
    cpu_budget_pct: float
    latency_budget_ms: float
    mean_cpu_pct: float = 0.0
    peak_cpu_pct: float = 0.0
    median_latency_ms: float = 0.0
    restarts: int = 0
    oom_killed: int = 0
    samples: int = 0
    breaches: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.breaches


def _docker(*args: str) -> str:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _project_containers(project: str) -> list[str]:
    out = _docker(
        "ps",
        "--filter",
        f"label=com.docker.compose.project={project}",
        "--format",
        "{{.Names}}",
    )
    return [line for line in out.splitlines() if line]


def _cpu_percent_now(names: list[str]) -> float:
    """Summed container CPU% (docker stats is per-core, so this can exceed 100)."""
    if not names:
        return 0.0
    out = _docker("stats", "--no-stream", "--format", "{{.CPUPerc}}", *names)
    total = 0.0
    for line in out.splitlines():
        total += float(line.strip().rstrip("%") or 0.0)
    return total


def _restart_and_oom(names: list[str]) -> tuple[int, int]:
    restarts = oom = 0
    for name in names:
        raw = _docker("inspect", "-f", "{{.RestartCount}} {{.State.OOMKilled}}", name)
        rc, _, ok = raw.partition(" ")
        restarts += int(rc or 0)
        oom += 1 if ok.strip() == "true" else 0
    return restarts, oom


def _http_latency_ms(url: str, attempts: int = 5) -> float:
    lat: list[float] = []
    ctx = _insecure_ctx()
    for _ in range(attempts):
        start = time.monotonic()
        try:
            urllib.request.urlopen(url, timeout=10, context=ctx).read()
            lat.append((time.monotonic() - start) * 1000.0)
        except Exception:  # noqa: BLE001 — a failed probe is a max-latency data point
            lat.append(float("inf"))
        time.sleep(0.2)
    lat.sort()
    return lat[len(lat) // 2]


def _insecure_ctx() -> object:
    import ssl

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # the local CA isn't trusted on the bench host
    return ctx


def run(args: argparse.Namespace) -> Report:
    cores = os.cpu_count() or 1
    names = _project_containers(args.project)
    if not names:
        print(
            f"no running containers for compose project '{args.project}'",
            file=sys.stderr,
        )
        sys.exit(2)

    base_restarts, base_oom = _restart_and_oom(names)
    report = Report(
        duration_s=args.duration,
        cores=cores,
        cpu_budget_pct=args.cpu_budget,
        latency_budget_ms=args.latency_budget_ms,
    )

    samples: list[Sample] = []
    latencies: list[float] = []
    deadline = time.monotonic() + args.duration
    while time.monotonic() < deadline:
        cpu = _cpu_percent_now(names) / (cores * 100.0) * 100.0
        samples.append(Sample(ts=time.time(), cpu_pct=cpu))
        latencies.append(_http_latency_ms(args.url))
        time.sleep(args.interval)

    end_restarts, end_oom = _restart_and_oom(names)
    report.samples = len(samples)
    if samples:
        report.mean_cpu_pct = round(sum(s.cpu_pct for s in samples) / len(samples), 2)
        report.peak_cpu_pct = round(max(s.cpu_pct for s in samples), 2)
    if latencies:
        latencies.sort()
        report.median_latency_ms = round(latencies[len(latencies) // 2], 1)
    report.restarts = end_restarts - base_restarts
    report.oom_killed = end_oom - base_oom

    if report.mean_cpu_pct > args.cpu_budget:
        report.breaches.append(
            f"mean CPU {report.mean_cpu_pct}% > budget {args.cpu_budget}%"
        )
    if report.median_latency_ms > args.latency_budget_ms:
        report.breaches.append(
            f"median latency {report.median_latency_ms}ms > budget {args.latency_budget_ms}ms"
        )
    if report.restarts > 0:
        report.breaches.append(f"{report.restarts} container restart(s) during the run")
    if report.oom_killed > 0:
        report.breaches.append(
            f"{report.oom_killed} container(s) OOM-killed during the run"
        )
    return report


def main() -> int:
    p = argparse.ArgumentParser(description="eeper reference-profile bench harness")
    p.add_argument("--project", default="eeper", help="compose project name")
    p.add_argument(
        "--url",
        default="https://localhost/api/v1/system/status",
        help="latency probe URL",
    )
    p.add_argument(
        "--duration", type=float, default=60.0, help="sampling window, seconds"
    )
    p.add_argument(
        "--interval", type=float, default=5.0, help="seconds between samples"
    )
    p.add_argument(
        "--cpu-budget", type=float, default=60.0, help="max mean CPU %% of host"
    )
    p.add_argument(
        "--latency-budget-ms", type=float, default=3000.0, help="max median latency"
    )
    p.add_argument(
        "--smoke",
        action="store_true",
        help="CI mode on a non-Pi runner: relax the CPU/latency budgets (validates the "
        "harness, not the reference-profile gate)",
    )
    args = p.parse_args()
    if args.smoke:
        # A shared CI runner is not the reference Pi; only prove the harness measures +
        # reports and the stack is stable. The real budgets run on the bench runner.
        args.cpu_budget = max(args.cpu_budget, 95.0)
        args.latency_budget_ms = max(args.latency_budget_ms, 5000.0)

    report = run(args)
    print(json.dumps({**asdict(report), "passed": report.passed}, indent=2))
    if not report.passed:
        print("BENCH FAILED:", "; ".join(report.breaches), file=sys.stderr)
        return 1
    print("BENCH OK", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# Performance & the reference-profile bench gate

eeper commits to running comfortably on modest hardware. The release gate (MASTER_PLAN
[§8](./MASTER_PLAN.md#8-security-architecture) / [§9](./MASTER_PLAN.md#9-performance--optimization))
is a **reference profile** that must be met before a v1.0 release.

## Budgets

| Metric                      | Budget                                      |
| --------------------------- | ------------------------------------------- |
| Steady-state CPU            | **< 60 %** of host capacity                 |
| WebRTC glass-to-glass (LAN) | **< 500 ms**                                |
| State event → UI            | **< 2 s**                                   |
| Cold page load (mid phone)  | **< 3 s**                                   |
| Soak                        | **72 h** with no OOM, crash, or stream loss |

Reference profile: **Raspberry Pi 5 (4 GB) + 1080p camera + mic + 2 sensor nodes**,
full stack. See [hardware.md](./hardware.md) for the bill of materials.

## The harness

[`scripts/bench.py`](../scripts/bench.py) is a pure-stdlib harness (only the `docker` CLI)
that samples a running stack and emits a JSON report, exiting non-zero on any breached
budget. It measures:

- **steady-state CPU** — summed container CPU as a fraction of host capacity, averaged
  over the run;
- **HTTP latency** — the API/page-load proxy (median of repeated probes);
- **stability** — container restart count and OOM-kills over the window.

```bash
# Short smoke run (any host) — validates the machinery, relaxed budgets:
python3 scripts/bench.py --smoke --duration 45

# Full reference-profile run on the bench (strict budgets, 72 h):
python3 scripts/bench.py --duration 259200 --interval 30 --cpu-budget 60 --latency-budget-ms 3000
```

## How it runs in CI vs on the bench

A GitHub-hosted runner is **not** a Pi, so it cannot hold the reference budgets. The
automation is therefore split:

- **`bench-smoke`** (in `stack.yml`, every push) brings up the core stack and runs the
  harness with `--smoke` — proving the harness measures, reports, and sees a stable stack.
- **[`bench.yml`](../.github/workflows/bench.yml)** runs the _real_ gate on a self-hosted
  runner labelled `[self-hosted, bench, pi5]`. It is `workflow_dispatch`-only (never runs
  or bills in ordinary CI) and brings up the full reference stack, then runs the harness
  with the strict budgets over the requested soak duration.

### Registering the bench runner

On the reference Pi 5 (camera + mic + sensor nodes attached), install the GitHub Actions
runner and register it with the labels `bench` and `pi5`. Then trigger **Actions → bench →
Run workflow** with `duration_hours: 72`. The run uploads `bench-report.json`.

## Results log

Record each release-candidate bench run here (attach or paste the report summary).

| Date      | Commit | Duration | Mean CPU | Median latency | Restarts / OOM | Verdict |
| --------- | ------ | -------- | -------- | -------------- | -------------- | ------- |
| _pending_ |        |          |          |                |                |         |

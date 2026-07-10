# Contributing to eeper

Thanks for wanting to help. eeper is a baby-monitoring system, which makes two things unusual about contributing here: a non-negotiable safety boundary, and a testing bar that is the definition of done. Read this once before your first PR and everything else is ordinary open source.

## The safety boundary (non-negotiable)

eeper is a sleep-insight tool, **not a medical device** (see docs/MASTER_PLAN.md §2). PRs will be declined — regardless of technical quality — if they:

- add or imply medical, diagnostic, or vital-sign claims (apnea/SIDS detection, oxygen alarms, temperature-as-vital-sign, "keeps your baby safe" language);
- add clinical alarm framing to notifications or UI copy (notification templates are lint-checked in CI against a clinical-terms denylist — this is enforced, not aspirational);
- weaken the pulse-ox gating (profile + acknowledged disclaimer), quality-gating, or insights-only presentation;
- remove or soften safety disclaimers.

Features that _improve_ insight quality, add inputs under the same stance, or strengthen the honesty of what we show users are very welcome. If you're unsure which side of the line an idea falls on, open an issue first — that conversation is cheap.

## Definition of done: tests

Every milestone in docs/IMPLEMENTATION_PLAN.md ships with testing criteria labeled [AUTO] or [MANUAL]. Work is done when its criteria pass, not when the code compiles. For PRs this means:

- New behavior comes with automated tests at the appropriate tier (unit / integration via the stack harness / Playwright).
- Quality-gate thresholds must trace to a stated product requirement, with the derivation recorded next to the number (see the implementation plan's testing-infrastructure preamble for why we learned this the hard way).
- If your PR completes checklist items in docs/PROGRESS.md, update docs/PROGRESS.md in the same PR.
- Model, preprocessing, or fusion changes trigger the model quality gates and replay suite; regressions below recorded ratchet baselines fail CI by design.

## Developer Certificate of Origin (DCO)

We use the [Developer Certificate of Origin v1.1](https://developercertificate.org/) rather than a CLA. You keep your copyright; you certify you have the right to contribute the code. Sign off every commit:

```
git commit -s -m "feat(insight): add radar presence extractor"
```

which appends `Signed-off-by: Your Name <you@example.com>`. Please sign off every commit (CI enforcement of this is planned, not yet in place). By signing off, your contribution is accepted under the license of the directory it touches (see LICENSING.md): AGPL-3.0-only for the core, MIT for `/web/src/lib` and `/firmware`. Do not copy GPL/AGPL code into the MIT directories; when adapting compatibly-licensed prior art into the core, preserve notices and note the provenance in the PR.

## Conventions

- **Commits:** [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `test:`, `chore:`), enforced in CI.
- **Python:** typed, `mypy --strict` clean; **TypeScript:** strict mode.
- **Formatting/linting:** run the pre-commit hooks (`pre-commit install`); CI runs the same.
- **Licensing headers:** every new source file starts with the correct `SPDX-License-Identifier` header for its directory; CI checks this.
- **Architecture seams:** clients speak only the versioned API; the server consumes only normalized streams/topics; sensors publish only the MQTT contract. PRs that reach across these seams need a very good reason, stated in the description.
- **Multi-arch:** code must run on amd64 and arm64; CI builds both.

## What to work on

- docs/PROGRESS.md shows the current milestone and its open criteria — the roadmap is the backlog.
- Issues labeled `good-first-issue` are scoped for newcomers.
- New input types, integrations (e.g., Home Assistant), and reference builds are welcome as post-v1 tracks — open an issue to align on the contract before building.
- docs/prior-art.md additions are always welcome.

## Manual-test contributions

Some criteria are [MANUAL] (physical devices, acoustics, overnight runs). If you have the hardware, executing a documented manual procedure from `/docs/testing/` and reporting results (date + initials in docs/PROGRESS.md format) is a first-class contribution.

## Conduct

Be kind, assume good faith, and remember the user on the other end is a sleep-deprived parent. Disagreements about engineering are settled with measurements where possible — this project has a track record of letting honest numbers overrule optimistic plans, and we'd like to keep it.

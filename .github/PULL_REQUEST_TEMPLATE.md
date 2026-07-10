<!-- .github/PULL_REQUEST_TEMPLATE.md -->

## What & why

<!-- What does this PR do, and what problem/milestone does it serve?
     Reference milestones (e.g., M2.3) and issues where applicable. -->

## Checklist

- [ ] **Safety boundary:** this PR adds no medical/diagnostic/vital-sign claims, no clinical alarm language, and does not weaken pulse-ox gating or disclaimers (see CONTRIBUTING.md). Copy changes pass the clinical-terms lint.
- [ ] **Tests:** new behavior is covered by [AUTO] tests at the right tier; quality-gate thresholds (if any) trace to a stated product requirement.
- [ ] **docs/PROGRESS.md:** updated in this PR if any milestone criteria are completed (or N/A).
- [ ] **DCO:** all commits are signed off (`git commit -s`).
- [ ] **SPDX headers:** new files carry the correct `SPDX-License-Identifier` for their directory; no GPL/AGPL code introduced into MIT directories.
- [ ] **Seams respected:** no client→internal-service, server→hardware, or contract-bypassing shortcuts (or the exception is justified below).
- [ ] **Multi-arch:** change is exercised by CI on amd64 and arm64.

## Model/fusion changes only

- [ ] Quality gates and replay suite run; no regression below recorded ratchet baselines (or the re-baselining is justified and recorded).
- [ ] Fixture library version referenced: `fixtures-v___`

## Manual testing performed

<!-- If this PR touches [MANUAL]-verified behavior (physical devices, acoustics,
     push notifications, overnight runs), describe what you ran and on what hardware. -->

## Notes for reviewers

<!-- Anything unusual: seam exceptions, provenance of adapted code, follow-ups deferred. -->

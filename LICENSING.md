# eeper Licensing

eeper uses a two-license structure. This document is the authoritative statement of which license applies where, and why.

## The structure

| Scope                                                                                                                                                                                         | License           | File                   |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------- | ---------------------- |
| Everything not listed below — the server services (`/server`), insight engine, recorder, deployment tooling (`/deploy`), adapters (`/adapters`), documentation, and the repository as a whole | **AGPL-3.0-only** | `/LICENSE`             |
| Client library code intended for embedding by third parties: the API client library and reusable PWA components (`/web/src/lib`)                                                              | **MIT**           | `/web/src/lib/LICENSE` |
| Sensor-node firmware and ESPHome configurations (`/firmware`)                                                                                                                                 | **MIT**           | `/firmware/LICENSE`    |

Source files should carry an `SPDX-License-Identifier` header (`AGPL-3.0-only` or `MIT`) so the applicable license is machine-checkable per file. Adding those headers across the tree and a CI check that enforces their presence and directory-correctness is planned work, not yet in place.

## Why this split

**AGPL-3.0 for the core** matches the project's founding premise: user ownership. The one commercially tempting misuse of this codebase is repackaging it as a closed, subscription, cloud-hosted baby monitor — the product category eeper defines itself against. Ordinary GPL does not reach that case (hosting is not distribution); AGPL §13 does, requiring anyone who offers a modified eeper over a network to publish their modifications. Anyone is free to use, modify, self-host, and even sell eeper — they just can't close it.

**MIT for the edges** removes friction exactly where we want maximum uptake: third-party apps embedding our API client, Home Assistant users reusing our sensor firmware, integrators building on the device configs. Nothing anti-enclosure lives in those directories.

## Practical consequences

- **Self-hosting families:** no obligations of any kind. Run it, modify it, never share anything.
- **Contributors:** contributions are accepted under the license of the directory they touch, certified via the Developer Certificate of Origin (see CONTRIBUTING.md). We use a DCO, not a CLA: copyright stays with contributors, which means the core license is effectively permanent once community contributions land. That permanence is intentional.
- **Companies/integrators:** you may build products and services with eeper. If you modify the AGPL core and offer it to users over a network, you must offer those users the modified source under AGPL-3.0. The MIT-licensed directories carry no such obligation. If AGPL is incompatible with your policies, the API and MQTT contracts are the intended integration boundary — build against them without incorporating core code.
- **Prior art:** GPL-3.0 code (e.g., OpenBabyMonitor) may be combined into the AGPL core per GPLv3 §13 / AGPLv3 §13, with attribution and license notices preserved. GPL/AGPL code must never be introduced into the MIT-licensed directories.

## What the licenses do not cover

The **"eeper" name and logo** are not licensed by the above. See TRADEMARKS.md — the realistic abuse scenario is not code theft but a stripped-down or unsafe fork shipping under our name.

## SPDX summary

`AGPL-3.0-only AND MIT` (per-directory, per the table above).

---

_This document describes the project's licensing intent in plain language; the license texts themselves are controlling. Not legal advice._

"""Insight engine entrypoint: ``python -m eeper.insight``.

Ensures the schema (incl. the state_history/events hypertables) exists — the
insight engine WRITES state_history, so it can't assume the api created it first —
then runs the supervisor: one ffmpeg video child per enabled camera feeding the
motion scorer, plus an audio child (16 kHz PCM) for cameras that carry audio.
"""

from __future__ import annotations

import asyncio
import logging

from eeper.api.config import get_settings
from eeper.api.db import create_schema_and_hypertables, get_sessionmaker
from eeper.insight.supervisor import InsightSupervisor


async def _main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    await create_schema_and_hypertables()
    supervisor = InsightSupervisor(get_sessionmaker(), settings)
    await supervisor.run()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()

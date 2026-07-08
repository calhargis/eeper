"""Recorder entrypoint: ``python -m eeper.recorder``.

Runs the supervisor (one ffmpeg child per enabled camera) and the retention
daemon concurrently in one process. Note the on-disk writer is each ffmpeg
subprocess, not this process — retention's safety comes from never deleting the
newest-per-camera (active) segment, not from same-process serialization
(see retention.py).
"""

from __future__ import annotations

import asyncio
import logging

from eeper.api.config import get_settings
from eeper.api.db import get_sessionmaker
from eeper.recorder.retention import retention_loop
from eeper.recorder.supervisor import RecorderSupervisor


async def _main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    supervisor = RecorderSupervisor(get_sessionmaker(), settings)
    await asyncio.gather(supervisor.run(), retention_loop(settings))


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()

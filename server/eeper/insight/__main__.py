"""Insight engine entrypoint: ``python -m eeper.insight``.

M2.1: run the audio supervisor (one ffmpeg audio-decode child per enabled camera,
producing 16 kHz mono PCM windows). M2.2 adds the frame sampler + feature registry
+ MQTT/state write alongside it.
"""

from __future__ import annotations

import asyncio
import logging

from eeper.api.config import get_settings
from eeper.api.db import get_sessionmaker
from eeper.insight.supervisor import AudioSupervisor


async def _main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    supervisor = AudioSupervisor(get_sessionmaker(), settings)
    await supervisor.run()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()

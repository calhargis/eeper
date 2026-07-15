"""Run the thermal capture node: ``python -m eeper.thermal``.

Needs the ``thermal`` extra (the MLX90640 driver) and a Pi with the sensor on I²C.
Configure via the environment — see docs/thermal-node.md.
"""

from eeper.thermal.node import main

if __name__ == "__main__":
    main()

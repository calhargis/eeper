"""Segment recorder (M1.4): records each enabled camera to a ring buffer of
MPEG-TS segments and evicts oldest segments over a byte quota. Runs as its own
container (``python -m eeper.recorder``) for crash isolation from the api."""

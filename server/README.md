# server — eeper Python services

Home of the server-side services: `api` (FastAPI), `insight-engine`
(PyTorch/ONNX), and `recorder` (ffmpeg). Phase 0 ships only tooling and a small
typed `eeper` package so lint/type-check/test run against real code; the
services land in later phases as subpackages.

## Develop

```bash
python3 -m pip install -e ".[dev]"
ruff check .     # lint
ruff format .    # format
mypy .           # type-check (strict)
pytest           # unit tests
```

Requires Python ≥ 3.12 (server images will pin `python:3.12-slim`).

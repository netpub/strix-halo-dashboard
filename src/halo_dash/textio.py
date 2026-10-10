from __future__ import annotations

import re
from pathlib import Path


_PROM_LINE = re.compile(
    r"^(?P<name>[a-zA-Z_:][\w:]*)(?:\{[^}]*\})?\s+(?P<value>[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
)

_RATIO_OR_RATE = {
    "llamacpp:kv_cache_usage_ratio",
    "llamacpp:prompt_tokens_seconds",
    "llamacpp:predicted_tokens_seconds",
}


def parse_prometheus(payload: str) -> dict[str, float]:
    grouped: dict[str, list[float]] = {}
    for line in payload.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _PROM_LINE.match(stripped)
        if not match:
            continue
        grouped.setdefault(match.group("name"), []).append(float(match.group("value")))
    out: dict[str, float] = {}
    for name, values in grouped.items():
        if name in _RATIO_OR_RATE:
            out[name] = max(values)
        else:
            out[name] = sum(values)
    return out


def read_int_file(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def read_float_file(path: Path) -> float | None:
    try:
        return float(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def read_text_file(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None

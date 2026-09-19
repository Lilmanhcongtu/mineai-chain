"""Structured (JSON-lines) logging. Never pass secrets (keys, passwords) to these helpers."""
from __future__ import annotations

import json
import logging
import sys
import time

_FORBIDDEN = {"password", "private_key", "secret", "mnemonic", "seed"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + "Z",
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        entry.update(getattr(record, "fields", {}))
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str, separators=(",", ":"))


def configure(level: str = "INFO") -> None:
    root = logging.getLogger("mineai")
    if any(getattr(h, "_mineai", False) for h in root.handlers):
        root.setLevel(level)
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    handler._mineai = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False


def event(logger: logging.Logger, level: int, name: str, **fields) -> None:
    bad = _FORBIDDEN.intersection(k.lower() for k in fields)
    if bad:
        raise ValueError(f"refusing to log sensitive field(s): {sorted(bad)}")
    logger.log(level, name, extra={"fields": fields})

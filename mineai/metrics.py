"""Operational metrics: thread-safe counters plus Prometheus-format rendering.

Every label value comes from a closed set defined in our own code (validation error codes, message types from the
protocol allow-list, fixed rejection buckets). Nothing an untrusted peer can choose ever becomes a label, so the number
of time series is bounded no matter how hostile the traffic is.
"""
from __future__ import annotations

import re
import threading
import time

_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")
_LABEL_VALUE_ESCAPES = str.maketrans({"\\": r"\\", '"': r"\"", "\n": r"\n"})

# Fixed buckets for handshake / protocol rejection reasons (the raw reason strings may contain peer-chosen numbers).
REJECT_BUCKETS = (
    ("different network", "network"), ("different genesis", "genesis"), ("no common protocol", "version"),
    ("connected to self", "self"), ("banned", "banned"), ("duplicate connection", "duplicate"),
    ("peer table full", "table_full"), ("frame length", "bad_frame"), ("malformed JSON", "malformed"),
    ("bad envelope", "malformed"), ("unknown message type", "unknown_type"), ("first message must be hello", "no_hello"),
    ("unexpected hello", "no_hello"), ("bad protocol version", "version"),
)


def reject_bucket(reason: str) -> str:
    for needle, bucket in REJECT_BUCKETS:
        if needle in reason:
            return bucket
    return "other"


class Metrics:
    def __init__(self):
        self.started = time.time()
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple], float] = {}

    def inc(self, name: str, n: float = 1, **labels: str) -> None:
        if not _NAME.match(name):
            raise ValueError(f"bad metric name {name!r}")
        key = (name, tuple(sorted(labels.items())))
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + n

    def get(self, name: str, **labels: str) -> float:
        with self._lock:
            return self._counters.get((name, tuple(sorted(labels.items()))), 0)

    def total(self, name: str) -> float:
        with self._lock:
            return sum(v for (n, _), v in self._counters.items() if n == name)

    def snapshot(self) -> dict[str, dict[str, float]]:
        """{metric: {"": value} or {"label=value": value}} — JSON friendly."""
        out: dict[str, dict[str, float]] = {}
        with self._lock:
            for (name, labels), value in sorted(self._counters.items()):
                out.setdefault(name, {})[",".join(f"{k}={v}" for k, v in labels)] = value
        return out

    def uptime(self) -> float:
        return time.time() - self.started


def _line(name: str, labels: tuple, value: float) -> str:
    if labels:
        body = ",".join(f'{k}="{str(v).translate(_LABEL_VALUE_ESCAPES)}"' for k, v in labels)
        name = f"{name}{{{body}}}"
    return f"{name} {value:.10g}"


def render_prometheus(counters: Metrics, gauges: dict[str, float | None], info: dict[str, str] | None = None) -> str:
    """Prometheus text exposition format. `gauges` are point-in-time values supplied by the caller."""
    lines: list[str] = []
    if info:
        lines += ["# HELP mineai_build_info Software and network identity.", "# TYPE mineai_build_info gauge",
                  _line("mineai_build_info", tuple(sorted(info.items())), 1)]
    for name, value in sorted(gauges.items()):
        if value is None:
            continue
        lines += [f"# TYPE mineai_{name} gauge", _line(f"mineai_{name}", (), float(value))]
    lines += ["# TYPE mineai_uptime_seconds gauge", _line("mineai_uptime_seconds", (), counters.uptime())]
    seen = set()
    with counters._lock:
        items = sorted(counters._counters.items())
    for (name, labels), value in items:
        metric = f"mineai_{name}_total"
        if metric not in seen:
            seen.add(metric)
            lines.append(f"# TYPE {metric} counter")
        lines.append(_line(metric, labels, value))
    return "\n".join(lines) + "\n"

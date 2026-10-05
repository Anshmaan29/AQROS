"""Prometheus-format metrics with no third-party dependency.

The architecture review flagged that no service exposes ``/metrics`` today.
Prometheus (``prometheus_client``) is the obvious library, but pulling it into
``aqros-core`` would make every service depend on it, and the metrics needed
across the platform are few and specific: request counts and latencies.

This module implements exactly that in the Prometheus text exposition format
with no dependencies. Latency is a bounded-window summary rather than a
histogram: the platform needs p50/p99 for alerting, not bucket-level
aggregation, and a summary keeps memory bounded without label cardinality.

Labels are deliberately limited to low-cardinality dimensions (status class,
method). Path is *not* a label: instrument IDs and order IDs would be
unbounded and would blow up the metrics store.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from threading import Lock


def _escape(value: str) -> str:
    """Escape a label value per the Prometheus text exposition format."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


@dataclass
class Summary:
    """Streaming count/sum plus quantiles over a bounded observation window."""

    name: str
    help: str
    quantiles: tuple[float, ...] = (0.5, 0.9, 0.99)
    max_observations: int = 10_000
    _observations: list[float] = field(default_factory=list, repr=False)
    _count: float = 0.0
    _sum: float = 0.0

    def observe(self, value: float) -> None:
        self._count += 1
        self._sum += value
        self._observations.append(value)
        if len(self._observations) > self.max_observations:
            # Keep the most recent window; dropping the older half keeps the
            # amortised cost O(1) per observation.
            del self._observations[: len(self._observations) // 2]

    def _quantile(self, q: float) -> float:
        if not self._observations:
            return 0.0
        ordered = sorted(self._observations)
        index = min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))
        return ordered[index]

    def render(self) -> Iterator[str]:
        yield f"# HELP {self.name} {_escape(self.help)}"
        yield f"# TYPE {self.name} summary"
        for q in self.quantiles:
            yield f'{self.name}{{quantile="{q}"}} {self._quantile(q)}'
        yield f"{self.name}_sum {self._sum}"
        yield f"{self.name}_count {self._count}"


@dataclass
class Counter:
    """Monotonically increasing counter. Rejects decreases rather than lying."""

    name: str
    help: str
    labels: dict[str, str] = field(default_factory=dict)
    _value: float = 0.0

    def inc(self, amount: float = 1.0) -> None:
        if amount < 0:
            raise ValueError("counters may only increase")
        self._value += amount

    def render(self) -> Iterator[str]:
        yield f"# HELP {self.name} {_escape(self.help)}"
        yield f"# TYPE {self.name} counter"
        if self.labels:
            rendered = ",".join(f'{k}="{_escape(v)}"' for k, v in sorted(self.labels.items()))
            yield f"{self.name}{{{rendered}}} {self._value}"
        else:
            yield f"{self.name} {self._value}"


@dataclass
class Gauge:
    """A value that may go up and down (queue depth, in-flight requests)."""

    name: str
    help: str
    labels: dict[str, str] = field(default_factory=dict)
    _value: float = 0.0

    def set(self, value: float) -> None:
        self._value = value

    def inc(self, amount: float = 1.0) -> None:
        self._value += amount

    def dec(self, amount: float = 1.0) -> None:
        self._value -= amount

    def render(self) -> Iterator[str]:
        yield f"# HELP {self.name} {_escape(self.help)}"
        yield f"# TYPE {self.name} gauge"
        if self.labels:
            rendered = ",".join(f'{k}="{_escape(v)}"' for k, v in sorted(self.labels.items()))
            yield f"{self.name}{{{rendered}}} {self._value}"
        else:
            yield f"{self.name} {self._value}"


class MetricsRegistry:
    """Collection of metrics rendered as Prometheus text.

    Metric handles are cached by name, so two callers asking for
    ``registry.counter("x", ...)`` get the same counter and thus accumulate
    into one series rather than silently forking the metric.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._counters: dict[str, Counter] = {}
        self._gauges: dict[str, Gauge] = {}
        self._summaries: dict[str, Summary] = {}

    def counter(self, name: str, help: str, **labels: str) -> Counter:
        key = f"{name}{sorted(labels.items())}"
        with self._lock:
            existing = self._counters.get(key)
            if existing is None:
                existing = Counter(name=name, help=help, labels=dict(labels))
                self._counters[key] = existing
            return existing

    def gauge(self, name: str, help: str, **labels: str) -> Gauge:
        key = f"{name}{sorted(labels.items())}"
        with self._lock:
            existing = self._gauges.get(key)
            if existing is None:
                existing = Gauge(name=name, help=help, labels=dict(labels))
                self._gauges[key] = existing
            return existing

    def summary(self, name: str, help: str) -> Summary:
        with self._lock:
            existing = self._summaries.get(name)
            if existing is None:
                existing = Summary(name=name, help=help)
                self._summaries[name] = existing
            return existing

    def render(self) -> str:
        """Render the whole registry in the Prometheus text exposition format."""
        metrics: list[Counter | Gauge | Summary] = [
            *self._counters.values(),
            *self._gauges.values(),
            *self._summaries.values(),
        ]
        with self._lock:
            lines = [line for metric in metrics for line in metric.render()]
        return "\n".join(lines) + "\n"


# Process-wide default, mirroring the stdlib logging convention. Tests pass
# their own registry to stay isolated.
REGISTRY = MetricsRegistry()

REQUESTS_TOTAL = "aqros_http_requests_total"
REQUEST_DURATION = "aqros_http_request_duration_seconds"
REQUESTS_IN_FLIGHT = "aqros_http_requests_in_flight"
RESPONSES_TOTAL = "aqros_http_responses_total"


def observe_request(
    method: str,
    status_code: int,
    duration_seconds: float,
    registry: MetricsRegistry | None = None,
) -> None:
    """Record one completed HTTP request.

    Only low-cardinality dimensions become labels (method, status class). The
    path is deliberately excluded: instrument and order IDs in the path would
    create unbounded series and are the classic way to take down a metrics
    backend.
    """
    reg = registry if registry is not None else REGISTRY
    reg.counter(REQUESTS_TOTAL, "Total HTTP requests served.", method=method).inc()
    reg.counter(
        RESPONSES_TOTAL,
        "HTTP responses by status class.",
        method=method,
        status=_status_class(status_code),
    ).inc()
    reg.summary(REQUEST_DURATION, "HTTP request duration in seconds.").observe(duration_seconds)


def _status_class(status_code: int) -> str:
    """Bucket a status code into 2xx/3xx/4xx/5xx to bound label cardinality."""
    if 100 <= status_code < 200:
        return "1xx"
    if 200 <= status_code < 300:
        return "2xx"
    if 300 <= status_code < 400:
        return "3xx"
    if 400 <= status_code < 500:
        return "4xx"
    return "5xx"


def render_all(registry: MetricsRegistry | None = None) -> str:
    """Render a registry (default: the process-wide one) to Prometheus text."""
    return (registry if registry is not None else REGISTRY).render()


def iter_lines(text: str) -> Iterable[str]:
    """Yield non-empty lines of rendered metrics (helper for tests)."""
    return (line for line in text.splitlines() if line)

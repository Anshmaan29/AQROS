"""Tests for the Prometheus text-format metrics registry."""

from __future__ import annotations

from aqros_core.metrics import (
    Counter,
    Gauge,
    MetricsRegistry,
    Summary,
    _status_class,
    observe_request,
)


def _value_of(rendered: str, series: str) -> float:
    for line in rendered.splitlines():
        if line.startswith(series + " ") or line.startswith(series + "{"):
            return float(line.rsplit(" ", 1)[1])
    raise AssertionError(f"{series} not found in:\n{rendered}")


class TestCounter:
    def test_increments(self) -> None:
        c = Counter(name="x_total", help="test")
        c.inc()
        c.inc(4)
        assert _value_of("\n".join(c.render()), "x_total") == 5.0

    def test_rejects_decrease(self) -> None:
        """A counter that can go down is a bug magnet; make it loud."""
        c = Counter(name="x_total", help="test")
        try:
            c.inc(-1)
        except ValueError:
            return
        raise AssertionError("counter should reject a negative increment")

    def test_renders_help_and_type(self) -> None:
        lines = list(Counter(name="x_total", help="A test counter.").render())
        assert lines[0] == "# HELP x_total A test counter."
        assert lines[1] == "# TYPE x_total counter"

    def test_renders_labels(self) -> None:
        c = Counter(name="x_total", help="t", labels={"method": "GET"})
        assert 'x_total{method="GET"}' in "\n".join(c.render())


class TestGauge:
    def test_increases_and_decreases(self) -> None:
        g = Gauge(name="g", help="t")
        g.set(5)
        assert g._value == 5
        g.dec(2)
        assert g._value == 3
        g.inc(1)
        assert g._value == 4

    def test_renders_type(self) -> None:
        assert "# TYPE g gauge" in "\n".join(Gauge(name="g", help="t").render())


class TestSummary:
    def test_count_and_sum(self) -> None:
        s = Summary(name="dur_seconds", help="t")
        for v in (1.0, 2.0, 3.0):
            s.observe(v)
        rendered = "\n".join(s.render())
        assert _value_of(rendered, "dur_seconds_count") == 3.0
        assert _value_of(rendered, "dur_seconds_sum") == 6.0

    def test_quantiles_are_ordered(self) -> None:
        s = Summary(name="d", help="t")
        for v in range(1, 101):
            s.observe(float(v))
        p50 = s._quantile(0.5)
        p99 = s._quantile(0.99)
        assert p50 <= p99
        assert 40 <= p50 <= 60

    def test_empty_summary_is_zero_not_a_crash(self) -> None:
        """An empty quantile must render 0.0, not raise or emit NaN."""
        s = Summary(name="d", help="t")
        assert s._quantile(0.99) == 0.0

    def test_window_is_bounded(self) -> None:
        """Memory must not grow without bound under sustained traffic."""
        s = Summary(name="d", help="t", max_observations=100)
        for i in range(1000):
            s.observe(float(i))
        assert len(s._observations) <= 100
        # The count/sum still reflect every observation, even after trimming.
        assert s._count == 1000


class TestRegistry:
    def test_same_name_returns_same_metric(self) -> None:
        """Two callers must not silently fork the series."""
        r = MetricsRegistry()
        assert r.counter("c", "t") is r.counter("c", "t")

    def test_labels_are_part_of_identity(self) -> None:
        r = MetricsRegistry()
        a = r.counter("c", "t", method="GET")
        b = r.counter("c", "t", method="POST")
        assert a is not b

    def test_render_includes_all_types(self) -> None:
        r = MetricsRegistry()
        r.counter("c_total", "t").inc()
        r.gauge("g", "t").set(2)
        r.summary("s_seconds", "t").observe(1.0)
        rendered = r.render()
        assert "c_total" in rendered
        assert "# TYPE g gauge" in rendered
        assert "s_seconds_count" in rendered

    def test_render_is_parseable_line_format(self) -> None:
        r = MetricsRegistry()
        r.counter("c_total", "t").inc()
        for line in r.render().splitlines():
            assert line.startswith("#") or line.split(" ")[0]

    def test_help_escapes_quotes_and_newlines(self) -> None:
        r = MetricsRegistry()
        r.counter("c_total", 'has "quote" and\nnewline').inc()
        rendered = r.render()
        # The HELP line must stay on one line and must not emit a raw quote.
        help_line = next(ln for ln in rendered.splitlines() if ln.startswith("# HELP"))
        assert '\\"quote\\"' in help_line
        assert "\\n" in help_line
        assert 'has \\"quote\\" and\\nnewline' in rendered


class TestStatusClass:
    def test_buckets(self) -> None:
        assert _status_class(200) == "2xx"
        assert _status_class(404) == "4xx"
        assert _status_class(503) == "5xx"
        assert _status_class(301) == "3xx"


class TestObserveRequest:
    def test_records_into_registry(self) -> None:
        r = MetricsRegistry()
        observe_request("GET", 200, 0.01, r)
        observe_request("GET", 500, 0.02, r)
        rendered = r.render()
        assert _value_of(rendered, 'aqros_http_requests_total{method="GET"}') == 2.0
        assert _value_of(rendered, 'aqros_http_responses_total{method="GET",status="5xx"}') == 1.0

    def test_excludes_path_from_labels(self) -> None:
        """Unbounded label values (order IDs) would blow up a metrics backend."""
        r = MetricsRegistry()
        observe_request("GET", 200, 0.01, r)
        assert "order_id" not in r.render()
        assert "/v1/orders" not in r.render()

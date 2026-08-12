"""A8: Prometheus metrics for monitoring and alerting."""

from __future__ import annotations

try:
    from prometheus_client import Counter, Histogram

    # LLM call latency by operation and status
    llm_call_duration = Histogram(
        "llm_call_duration_seconds",
        "Duration of LLM call in seconds",
        labelnames=["operation", "status"],
        buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
    )

    # Token counts
    llm_tokens = Counter(
        "llm_tokens_total",
        "Total tokens processed by LLM",
        labelnames=["operation"],  # 'input' or 'output'
    )

    # Diagram generation success/failure
    diagram_generations = Counter(
        "diagram_generations_total",
        "Number of diagram generations",
        labelnames=["status"],  # 'success', 'error', 'timeout'
    )

    # HTTP request latency
    http_request_duration = Histogram(
        "http_request_duration_seconds",
        "HTTP request latency in seconds",
        labelnames=["method", "path", "status"],
        buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
    )

    # IR compilation time
    ir_compile_duration = Histogram(
        "ir_compile_duration_seconds",
        "IR diagram compilation time in seconds",
        labelnames=["view_type"],  # 'logical', 'security', 'data_flow'
        buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0),
    )

except ImportError:
    # Prometheus not installed; provide no-op stubs
    class _NoOpHistogram:
        def labels(self, **kwargs):
            return self
        def observe(self, value):
            pass
        def time(self):
            pass

    class _NoOpCounter:
        def labels(self, **kwargs):
            return self
        def inc(self, amount=1):
            pass

    llm_call_duration = _NoOpHistogram()  # type: ignore
    llm_tokens = _NoOpCounter()  # type: ignore
    diagram_generations = _NoOpCounter()  # type: ignore
    http_request_duration = _NoOpHistogram()  # type: ignore
    ir_compile_duration = _NoOpHistogram()  # type: ignore

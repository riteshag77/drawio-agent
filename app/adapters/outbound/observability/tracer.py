"""A8: Observability layer — structured tracing, Prometheus metrics, persistent traces.

Replaces request-local in-memory logging with persistent, queryable traces
and Prometheus metrics for monitoring and debugging.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger("drawio-agent.traces")


@dataclass
class SpanEvent:
    """Single operation within a diagram generation request."""

    timestamp: str
    operation: str  # "llm.call", "ir.compile", "cost.estimate", etc.
    duration_ms: float
    status: str  # "success", "error", "timeout", "retry"
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    cost_usd: Optional[float] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    error_message: Optional[str] = None
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class DiagramGenerationTracer:
    """Traces all operations in a diagram generation request.
    
    Records:
    - Timing for each step (LLM calls, compilation, cost estimation, etc.)
    - Token usage and estimated cost
    - Errors and retries
    - Status (success, timeout, error)
    
    Can be exported as JSON for persistent storage and querying.
    """

    def __init__(self, request_id: str, parent_span_id: Optional[str] = None):
        self.request_id = request_id
        self.parent_span_id = parent_span_id
        self.start_time = time.time()
        self.events: list[SpanEvent] = []
        self.total_tokens_in = 0
        self.total_tokens_out = 0
        self.total_cost_usd = 0.0

    def span(self, operation: str, **kwargs) -> _Span:
        """Context manager for a single operation."""
        return _Span(self, operation, **kwargs)

    def add_event(self, event: SpanEvent) -> None:
        """Record an event and update totals."""
        self.events.append(event)

        # Update totals
        if event.tokens_in:
            self.total_tokens_in += event.tokens_in
        if event.tokens_out:
            self.total_tokens_out += event.tokens_out
        if event.cost_usd:
            self.total_cost_usd += event.cost_usd

        # Log immediately to structured logger
        logger.info(
            f"[{self.request_id}] {event.operation}",
            extra={
                "request_id": self.request_id,
                "operation": event.operation,
                "duration_ms": event.duration_ms,
                "status": event.status,
                "tokens_in": event.tokens_in,
                "tokens_out": event.tokens_out,
                "cost_usd": event.cost_usd,
                "model": event.model,
                "provider": event.provider,
            },
        )

        # Prometheus metrics (if available)
        try:
            from app.adapters.outbound.observability.metrics import (
                llm_call_duration,
                llm_tokens,
            )

            if event.operation.startswith("llm."):
                llm_call_duration.labels(
                    operation=event.operation, status=event.status
                ).observe(event.duration_ms / 1000)

                if event.tokens_in:
                    llm_tokens.labels(operation="input").inc(event.tokens_in)
                if event.tokens_out:
                    llm_tokens.labels(operation="output").inc(event.tokens_out)
        except ImportError:
            pass  # Prometheus not available

    def export_json(self) -> dict:
        """Export trace as JSON for persistence."""
        total_duration_ms = round((time.time() - self.start_time) * 1000)

        return {
            "request_id": self.request_id,
            "parent_span_id": self.parent_span_id,
            "start_time": datetime.fromtimestamp(self.start_time).isoformat(),
            "total_duration_ms": total_duration_ms,
            "total_tokens_in": self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "total_cost_usd": self.total_cost_usd,
            "events": [event.to_dict() for event in self.events],
        }

    def export_json_string(self) -> str:
        """Export trace as JSON string."""
        return json.dumps(self.export_json(), default=str)


class _Span:
    """Context manager for a single traced operation."""

    def __init__(self, tracer: DiagramGenerationTracer, operation: str, **kwargs):
        self.tracer = tracer
        self.operation = operation
        self.kwargs = kwargs
        self.start_time: Optional[float] = None

    def __enter__(self):
        self.start_time = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration_ms = round((time.time() - self.start_time) * 1000)
        status = "error" if exc_type else "success"

        event = SpanEvent(
            timestamp=datetime.utcnow().isoformat(),
            operation=self.operation,
            duration_ms=duration_ms,
            status=status,
            error_message=str(exc_val) if exc_val else None,
            **self.kwargs,
        )
        self.tracer.add_event(event)

        # Return False to propagate exceptions
        return False

    async def __aenter__(self):
        self.start_time = time.time()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        duration_ms = round((time.time() - self.start_time) * 1000)
        status = "error" if exc_type else "success"

        event = SpanEvent(
            timestamp=datetime.utcnow().isoformat(),
            operation=self.operation,
            duration_ms=duration_ms,
            status=status,
            error_message=str(exc_val) if exc_val else None,
            **self.kwargs,
        )
        self.tracer.add_event(event)

        return False


class TraceStore:
    """Persistent storage for traces (for later analysis and debugging).
    
    Stores traces in SQLite so they can be queried by request_id, date range,
    operation type, etc.
    """

    def __init__(self, db_connection: Any):
        """Initialize trace store with database connection."""
        self.db = db_connection
        self._init_schema()

    def _init_schema(self) -> None:
        """Create traces table if it doesn't exist."""
        cursor = self.db.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS traces (
                request_id TEXT PRIMARY KEY,
                parent_span_id TEXT,
                start_time TEXT,
                total_duration_ms INTEGER,
                total_tokens_in INTEGER,
                total_tokens_out INTEGER,
                total_cost_usd REAL,
                trace_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS ix_traces_created_at ON traces(created_at)"
        )
        self.db.commit()

    async def save_trace(self, trace: DiagramGenerationTracer) -> None:
        """Save a trace to persistent storage."""
        trace_data = trace.export_json()

        cursor = self.db.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO traces
            (request_id, parent_span_id, start_time, total_duration_ms,
             total_tokens_in, total_tokens_out, total_cost_usd, trace_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trace.request_id,
                trace.parent_span_id,
                trace_data["start_time"],
                trace_data["total_duration_ms"],
                trace_data["total_tokens_in"],
                trace_data["total_tokens_out"],
                trace_data["total_cost_usd"],
                trace.export_json_string(),
            ),
        )
        self.db.commit()

    def get_trace(self, request_id: str) -> Optional[dict]:
        """Retrieve a trace by request_id."""
        cursor = self.db.cursor()
        cursor.execute("SELECT trace_json FROM traces WHERE request_id = ?", (request_id,))
        row = cursor.fetchone()

        if row:
            return json.loads(row[0])
        return None

    def list_traces(self, limit: int = 100, offset: int = 0) -> list[dict]:
        """List recent traces."""
        cursor = self.db.cursor()
        cursor.execute(
            """
            SELECT trace_json FROM traces
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        return [json.loads(row[0]) for row in cursor.fetchall()]

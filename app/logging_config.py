# app/logging_config.py
"""
Structured JSON logging via structlog.
Every API request is logged with: trace_id, store_id, endpoint, method,
latency_ms, and status_code. Output is machine-parseable JSON.
"""

import uuid
import time
import structlog
from fastapi import Request

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

log = structlog.get_logger()


async def logging_middleware(request: Request, call_next):
    """FastAPI middleware: log every request with timing and context."""
    trace_id = uuid.uuid4().hex[:8]
    start = time.monotonic()

    response = await call_next(request)

    latency_ms = round((time.monotonic() - start) * 1000, 1)
    log.info(
        "request",
        trace_id=trace_id,
        store_id=request.path_params.get("store_id"),
        endpoint=str(request.url.path),
        method=request.method,
        latency_ms=latency_ms,
        status_code=response.status_code,
    )
    return response

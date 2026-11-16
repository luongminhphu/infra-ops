"""Structured logging with correlation IDs and secret redaction."""

from __future__ import annotations

import logging
import re
import uuid
from contextvars import ContextVar
from typing import Any

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)

SENSITIVE_KEY = re.compile(
    r"(pass(word)?|secret|token|authtoken|api[_-]?key|private[_-]?key|certificate|cookie|authorization|session)",
    re.IGNORECASE,
)
REDACTED = "••••"


def redact(value: Any) -> Any:
    """Recursively mask values whose key looks sensitive."""
    if isinstance(value, dict):
        return {
            k: (REDACTED if isinstance(k, str) and SENSITIVE_KEY.search(k) else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    return value


def _add_correlation_id(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    cid = correlation_id_var.get()
    if cid:
        event_dict.setdefault("correlation_id", cid)
    return event_dict


def _redact_processor(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    return redact(event_dict)  # type: ignore[no-any-return]


def configure_logging(level: str = "INFO", json: bool = True) -> None:
    renderer: Any = structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _add_correlation_id,
            _redact_processor,
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelNamesMapping().get(level, 20)),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    header = "X-Request-ID"

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get(self.header, "")
        cid = incoming if re.fullmatch(r"[A-Za-z0-9._-]{8,64}", incoming) else uuid.uuid4().hex
        token = correlation_id_var.set(cid)
        try:
            response = await call_next(request)
        finally:
            correlation_id_var.reset(token)
        response.headers[self.header] = cid
        return response

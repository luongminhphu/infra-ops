"""Shared HTTP helpers: timeouts, retry with backoff + jitter, Retry-After and friendly errors."""

from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx

from app.core.logging import get_logger

log = get_logger(__name__)

RETRY_STATUS = {429, 502, 503, 504}


class ConnectorHttpError(Exception):
    def __init__(self, message: str, *, status: int | None = None, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.hint = hint


def make_client(
    base_url: str, *, verify: bool, timeout: float, headers: dict[str, str] | None = None
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        verify=verify,
        timeout=httpx.Timeout(timeout, connect=min(timeout, 10)),
        headers={"User-Agent": "infra-ops/1.0", **(headers or {})},
        follow_redirects=False,
    )


def _retry_after(resp: httpx.Response, attempt: int) -> float:
    header = resp.headers.get("Retry-After")
    if header and header.isdigit():
        return min(float(header), 30.0)
    return min(2**attempt + random.uniform(0, 0.5), 30.0)  # noqa: S311 - jitter, not crypto


async def request(
    client: httpx.AsyncClient, method: str, url: str, *, max_attempts: int = 3, **kwargs: Any
) -> httpx.Response:
    """Send a request, retrying transient failures. Never logs bodies (may contain secrets)."""
    for attempt in range(1, max_attempts + 1):
        try:
            resp = await client.request(method, url, **kwargs)
        except httpx.ConnectTimeout as exc:
            if attempt == max_attempts:
                raise ConnectorHttpError(
                    "Hết thời gian chờ kết nối.", hint="Kiểm tra firewall và địa chỉ máy chủ."
                ) from exc
        except httpx.ReadTimeout as exc:
            if attempt == max_attempts:
                raise ConnectorHttpError(
                    "Máy chủ phản hồi quá chậm.", hint="Thử lại sau hoặc tăng timeout."
                ) from exc
        except httpx.ConnectError as exc:
            if attempt == max_attempts:
                raise ConnectorHttpError(
                    f"Không kết nối được máy chủ: {exc}", hint="Kiểm tra địa chỉ, cổng, SSL."
                ) from exc
        else:
            if resp.status_code not in RETRY_STATUS or attempt == max_attempts:
                return resp
            delay = _retry_after(resp, attempt)
            log.info(
                "http_retry",
                url=str(resp.request.url.copy_with(query=None)),
                status=resp.status_code,
                delay=delay,
            )
            await asyncio.sleep(delay)
            continue
        await asyncio.sleep(min(2**attempt, 10))
    raise AssertionError("unreachable")

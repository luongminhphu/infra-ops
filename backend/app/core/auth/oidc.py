"""Microsoft Entra ID sign-in (OIDC authorization code flow with PKCE).

The browser never sees tokens: the backend exchanges the code, validates the ID token
and issues its own session cookie.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.core.config import Settings
from app.core.errors import NotAuthenticated

STATE_COOKIE = "infraops_oidc"
STATE_MAX_AGE = 600


@dataclass(frozen=True)
class OidcIdentity:
    oid: str
    upn: str
    display_name: str
    email: str | None


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.signing_secret.get_secret_value(), salt="oidc-state")


def redirect_uri(settings: Settings) -> str:
    return f"{settings.public_base_url.rstrip('/')}/api/core/auth/oidc/callback"


def build_authorize(settings: Settings, next_path: str) -> tuple[str, str]:
    """Return (authorize URL, signed state cookie value)."""
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    params = {
        "client_id": settings.oidc_client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri(settings),
        "response_mode": "query",
        "scope": "openid profile email",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    url = (
        f"{settings.oidc_authority_host}/{settings.oidc_tenant_id}/oauth2/v2.0/authorize?{urlencode(params)}"
    )
    safe_next = next_path if next_path.startswith("/") and not next_path.startswith("//") else "/"
    cookie = _serializer(settings).dumps({"s": state, "n": nonce, "v": verifier, "next": safe_next})
    return url, cookie


def read_state(settings: Settings, cookie: str | None, state: str) -> dict[str, str]:
    if not cookie:
        raise NotAuthenticated("Phiên đăng nhập SSO đã hết hạn. Vui lòng thử lại.")
    try:
        data: dict[str, str] = _serializer(settings).loads(cookie, max_age=STATE_MAX_AGE)
    except BadSignature as exc:
        raise NotAuthenticated("Phiên đăng nhập SSO không hợp lệ. Vui lòng thử lại.") from exc
    if not secrets.compare_digest(data.get("s", ""), state):
        raise NotAuthenticated("Phiên đăng nhập SSO không hợp lệ. Vui lòng thử lại.")
    return data


_jwks_clients: dict[str, jwt.PyJWKClient] = {}


def _jwks(settings: Settings) -> jwt.PyJWKClient:
    url = f"{settings.oidc_authority_host}/{settings.oidc_tenant_id}/discovery/v2.0/keys"
    if url not in _jwks_clients:
        _jwks_clients[url] = jwt.PyJWKClient(url, cache_keys=True)
    return _jwks_clients[url]


async def exchange_code(settings: Settings, code: str, state_data: dict[str, str]) -> OidcIdentity:
    token_url = f"{settings.oidc_authority_host}/{settings.oidc_tenant_id}/oauth2/v2.0/token"
    form = {
        "client_id": settings.oidc_client_id,
        "client_secret": settings.oidc_client_secret.get_secret_value(),
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(settings),
        "code_verifier": state_data["v"],
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(token_url, data=form)
    if resp.status_code != 200:
        raise NotAuthenticated(
            "Đăng nhập SSO thất bại khi đổi mã xác thực.", details={"status": resp.status_code}
        )
    id_token = resp.json().get("id_token")
    if not id_token:
        raise NotAuthenticated("Đăng nhập SSO thất bại: thiếu ID token.")
    key = await asyncio.to_thread(_jwks(settings).get_signing_key_from_jwt, id_token)
    claims: dict[str, Any] = jwt.decode(
        id_token,
        key.key,
        algorithms=["RS256"],
        audience=settings.oidc_client_id,
        issuer=settings.oidc_issuer,
        options={"require": ["exp", "iat", "aud", "iss", "nonce"]},
    )
    if not secrets.compare_digest(str(claims.get("nonce", "")), state_data["n"]):
        raise NotAuthenticated("Đăng nhập SSO không hợp lệ (nonce).")
    upn = str(claims.get("preferred_username") or claims.get("upn") or "").lower()
    if not claims.get("oid") or not upn:
        raise NotAuthenticated("Tài khoản SSO thiếu thông tin định danh.")
    return OidcIdentity(
        oid=str(claims["oid"]), upn=upn, display_name=str(claims.get("name", upn)), email=claims.get("email")
    )

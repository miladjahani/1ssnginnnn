import base64
import datetime
from typing import Optional, Tuple
from fastapi import APIRouter, Request, Response

from app.subscriptions.engine import subscription_engine
from app.services.repository import repo
from app.services.access import parse_expiry
from app.config import settings

router = APIRouter(tags=["subscriptions"])

DEFAULT_PROFILE_TITLE = "MILICONFIG Subscription"


def _epoch_seconds(value: Optional[str]) -> int:
    """Convert a stored expiry value to a Unix timestamp (0 = never)."""
    parsed = parse_expiry(value)
    if parsed is None:
        return 0
    return int(parsed.replace(tzinfo=datetime.timezone.utc).timestamp())


def build_subscription_headers(token: str) -> dict:
    """
    Real per-user usage/expiry headers so clients show correct data.

    Previously these were hard-coded to `upload=0; download=0; total=100GB; expire=0`,
    which made every client display fake quota numbers (inspired by StanNG, which
    reports the live counters instead).
    """
    user = repo.get_user_by_sub_token(token)
    title = settings.PROFILE_TITLE or DEFAULT_PROFILE_TITLE
    base = {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
        "Profile-Update-Interval": "1",
        "Profile-Title": "base64:" + base64.b64encode(title.encode("utf-8")).decode("ascii"),
    }
    if not user:
        return base

    upload = int(user.upload or 0)
    download = int(user.download or 0)
    total = int(user.traffic_limit or 0)
    userinfo = (
        f"upload={upload}; download={download}; total={total}; "
        f"expire={_epoch_seconds(user.expires_at)}"
    )
    # Single header entry: HTTP header names are case-insensitive and clients such as
    # v2rayNG read it either way, while a duplicate key would be sent twice.
    base["Subscription-Userinfo"] = userinfo
    return base


def _subscription_response(token: str, request: Request, target: Optional[str]) -> Response:
    ua = request.headers.get("user-agent", "")
    req_host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or settings.DEFAULT_DOMAIN
    )

    status_code, content, content_type = subscription_engine.build_subscription(
        token=token,
        user_agent=ua,
        target_param=target,
        request_host=req_host,
    )
    headers = {"Content-Type": content_type}
    headers.update(build_subscription_headers(token))
    return Response(content=content, status_code=status_code, headers=headers)


@router.get("/sub/{token}")
async def get_subscription(token: str, request: Request, target: Optional[str] = None):
    return _subscription_response(token, request, target)


@router.get("/api/sub/{token}")
async def get_api_subscription(token: str, request: Request, target: Optional[str] = None):
    return _subscription_response(token, request, target)

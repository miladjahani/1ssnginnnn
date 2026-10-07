"""Central user entitlement checks (status, expiry and traffic quota).

The admin panel and the feature matrix promise that subscriptions and live
proxy sessions are refused for suspended, expired or over-quota users, so the
same rule set is applied by every entry point (subscriptions, WebSocket,
xHTTP and the ShadowSocks engine).
"""
import datetime
import logging
from typing import Optional

from app.models.models import User

logger = logging.getLogger("miliconfig.access")


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def parse_expiry(value: Optional[str]) -> Optional[datetime.datetime]:
    """Parse the many expiry formats an operator may enter. None = no expiry."""
    if value is None:
        return None
    raw = str(value).strip()
    if not raw or raw.lower() in ("0", "none", "never", "null"):
        return None

    # Epoch seconds / milliseconds
    if raw.isdigit():
        try:
            ts = int(raw)
            if ts > 10 ** 12:
                ts //= 1000
            return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).replace(tzinfo=None)
        except Exception:
            pass

    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.datetime.fromisoformat(normalized)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        return parsed
    except Exception:
        pass

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.datetime.strptime(raw, fmt)
        except Exception:
            continue

    logger.warning("Unparseable expires_at value ignored: %s", raw)
    return None


def user_access_error(user: Optional[User]) -> Optional[str]:
    """Return a human readable reason when the user may not use proxy services."""
    if user is None:
        return "user not found"

    status = str(user.status or "active").strip().lower()
    if status != "active":
        return f"user is {status}"

    expires_at = parse_expiry(user.expires_at)
    if expires_at is not None and expires_at < _utcnow():
        return "subscription expired"

    limit = int(user.traffic_limit or 0)
    if limit > 0:
        used = int(user.upload or 0) + int(user.download or 0)
        if used >= limit:
            return "traffic quota exceeded"

    return None


def is_user_active(user: Optional[User]) -> bool:
    return user_access_error(user) is None

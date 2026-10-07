from typing import Optional
from fastapi import APIRouter, Request, Response
from app.subscriptions.engine import subscription_engine
from app.config import settings

router = APIRouter(tags=["subscriptions"])

@router.get("/sub/{token}")
async def get_subscription(token: str, request: Request, target: Optional[str] = None):
    ua = request.headers.get("user-agent", "")
    req_host = request.headers.get("x-forwarded-host") or request.headers.get("host") or settings.DEFAULT_DOMAIN
    
    status_code, content, content_type = subscription_engine.build_subscription(
        token=token,
        user_agent=ua,
        target_param=target,
        request_host=req_host
    )
    headers = {
        "Content-Type": content_type,
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Profile-Update-Interval": "24",
        "Subscription-Userinfo": "upload=0; download=0; total=107374182400; expire=0"
    }
    return Response(content=content, status_code=status_code, headers=headers)

@router.get("/api/sub/{token}")
async def get_api_subscription(token: str, request: Request, target: Optional[str] = None):
    return await get_subscription(token, request, target)

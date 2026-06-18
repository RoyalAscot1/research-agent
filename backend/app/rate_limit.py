"""Per-user API rate limiting.

The expensive endpoints (`POST /queries`, `POST /reports/{id}/followup`) each fan out to
paid/metered third-party APIs (Tavily, Gemini, YouTube). Without a ceiling, one signed-in
user — or a leaked token — could drain quota and run up cost in minutes.

We key limits on the Clerk user id, NOT the client IP: IP keying both punishes users behind
a shared NAT and is trivially bypassed, and the real cost unit is the authenticated user.
`get_current_user` (app/auth.py) stashes the verified id on `request.state.clerk_user_id`;
this module's key function reads it. Limits are checked after auth runs (the dependency
resolves before the endpoint body), so an unauthenticated request 401s before it ever gets
here. If the id is somehow unset we fall back to the remote address rather than crash.

Storage is slowapi's default in-memory backend: counters are per-process and reset on
restart. That's fine for a single-instance deploy; a multi-instance / durable setup wants a
shared Redis backend (see PRODUCTION_READINESS.md — pairs with the deferred ARQ/Redis work).
"""

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import settings

# Limit strings (slowapi syntax). The research endpoint is where the real cost is, so it
# gets the tighter ceiling; follow-ups are one cheap Gemini call each and are looser. Both
# are deliberately generous — this is a low-traffic portfolio app, so the goal is to stop
# abuse/runaway scripts, not to ever inconvenience a real visitor.
QUERIES_LIMIT = "10/hour;3/minute"
FOLLOWUP_LIMIT = "20/hour;5/minute"


def _rate_limit_key(request: Request) -> str:
    clerk_user_id = getattr(request.state, "clerk_user_id", None)
    if clerk_user_id:
        return f"user:{clerk_user_id}"
    return get_remote_address(request)


limiter = Limiter(key_func=_rate_limit_key, enabled=settings.rate_limit_enabled)


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Return a clean 429 in the app's `{"detail": ...}` shape, with the standard headers."""
    response = JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded — please slow down and try again."},
    )
    return request.app.state.limiter._inject_headers(response, request.state.view_rate_limit)

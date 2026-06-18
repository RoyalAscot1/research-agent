"""Per-user rate limiting on the expensive endpoints.

The suite runs with the limiter disabled (RATE_LIMIT_ENABLED=false in conftest), so this
test re-enables it locally and resets the in-memory counters around itself. It drives
POST /queries past the 3/minute leg of QUERIES_LIMIT and asserts a clean 429.
"""

import uuid

import pytest

from app import rate_limit
from app.routers import queries as queries_mod
from tests.conftest import FakeSession, make_user


@pytest.fixture
def enabled_limiter(monkeypatch):
    monkeypatch.setattr(rate_limit.limiter, "enabled", True)
    rate_limit.limiter.reset()
    yield rate_limit.limiter
    rate_limit.limiter.reset()


async def test_queries_returns_429_past_the_per_minute_cap(
    client, override_deps, monkeypatch, enabled_limiter
):
    user = make_user(user_id=uuid.uuid4())
    override_deps(user, FakeSession())

    # Keep the fired background task harmless — we only care about the rate-limit gate.
    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr(queries_mod, "run_graph", _noop)

    # QUERIES_LIMIT allows 3/minute; the 4th call in the window is rejected. With
    # get_current_user overridden, request.state.clerk_user_id is unset, so the key
    # function falls back to the (shared) remote address — every call lands in one bucket.
    statuses = []
    for _ in range(4):
        resp = await client.post("/queries", json={"query": "What is X?"})
        statuses.append(resp.status_code)

    assert statuses[:3] == [202, 202, 202]
    assert statuses[3] == 429

"""Shared test setup and fakes.

IMPORTANT: this module sets dummy environment variables at import time, BEFORE any
`app.*` module is imported. `app.config` instantiates `Settings()` at import and several
fields are required, so without this every test collection would fail. pytest imports
conftest.py before collecting the test modules in its directory, so doing it here is enough.
"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")
os.environ.setdefault("TAVILY_API_KEY", "test-tavily-key")
os.environ.setdefault("YOUTUBE_API_KEY", "test-youtube-key")
os.environ.setdefault("CLERK_ISSUER", "https://clerk.test.example")
os.environ.setdefault("CLERK_AUTHORIZED_PARTIES", "http://localhost:3000")
# Off by default so the suite isn't throttled; the dedicated 429 test re-enables it.
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")

import uuid  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.models.models import FollowUp, Report, ResearchJob, User  # noqa: E402

# ---------------------------------------------------------------------------
# Fake LLM / external-client doubles (graph nodes reference these as module
# globals in app.graph.graph, so tests monkeypatch them there).
# ---------------------------------------------------------------------------


class FakeLLM:
    """Stand-in for ChatGoogleGenerativeAI.

    Covers all four call shapes in graph.py:
    - structured output (planner/coverage): `.with_structured_output(Schema)` then
      `.ainvoke()` returns `structured_result`.
    - plain text (synthesizer/follow-up): `.ainvoke()` returns an object with `.content`.
    - failure: `.ainvoke()` raises `exc` (used for fallback / timeout paths).
    """

    def __init__(self, *, structured_result=None, content=None, exc=None):
        self.structured_result = structured_result
        self.content = content
        self.exc = exc
        self.calls = 0

    def with_structured_output(self, _schema):
        return self

    async def ainvoke(self, _messages):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        if self.structured_result is not None:
            return self.structured_result
        return SimpleNamespace(content=self.content)


def patch_llm(monkeypatch, llm):
    """Point graph.ChatGoogleGenerativeAI at a FakeLLM (ignores constructor kwargs)."""
    from app.graph import graph

    monkeypatch.setattr(graph, "ChatGoogleGenerativeAI", lambda **_kw: llm)
    return llm


class FakeTavily:
    """Stand-in for TavilyClient. Records every query it's asked to search."""

    def __init__(self, results_by_query=None, default_results=None):
        self.results_by_query = results_by_query or {}
        self.default_results = default_results or []
        self.calls = []

    def search(self, query, **_kwargs):
        self.calls.append(query)
        return {"results": self.results_by_query.get(query, self.default_results)}


def patch_tavily(monkeypatch, fake):
    from app.graph import graph

    monkeypatch.setattr(graph, "TavilyClient", lambda **_kw: fake)
    return fake


def make_fake_youtube(video_ids, comments_by_video):
    """Build a fake googleapiclient YouTube resource with the chained-call shape."""
    search_result = {"items": [{"id": {"videoId": v}} for v in video_ids]}

    class FakeYouTube:
        def search(self):
            return SimpleNamespace(
                list=lambda **_kw: SimpleNamespace(execute=lambda: search_result)
            )

        def commentThreads(self):  # noqa: N802 - mirrors the googleapiclient method name
            def list_(**kwargs):
                vid = kwargs.get("videoId")
                items = [
                    {"snippet": {"topLevelComment": {"snippet": {"textDisplay": text}}}}
                    for text in comments_by_video.get(vid, [])
                ]
                return SimpleNamespace(execute=lambda: {"items": items})

            return SimpleNamespace(list=list_)

    return FakeYouTube()


def patch_youtube(monkeypatch, fake_youtube):
    from app.graph import graph

    monkeypatch.setattr(graph, "build", lambda *_a, **_k: fake_youtube)
    return fake_youtube


# ---------------------------------------------------------------------------
# Fake DB session for endpoint tests (no real Postgres — see PRODUCTION_READINESS.md
# for why the API layer is tested against a fake session rather than testcontainers).
# ---------------------------------------------------------------------------


class FakeResult:
    def __init__(self, *, scalar_one=None, scalar_one_or_none=None, scalars_all=None, all_=None):
        self._scalar_one = scalar_one
        self._scalar_one_or_none = scalar_one_or_none
        self._scalars_all = scalars_all if scalars_all is not None else []
        self._all = all_ if all_ is not None else []

    def scalar_one(self):
        return self._scalar_one

    def scalar_one_or_none(self):
        return self._scalar_one_or_none

    def scalars(self):
        return SimpleNamespace(all=lambda: self._scalars_all)

    def all(self):
        return self._all


class FakeSession:
    """Minimal async-session double.

    `get_map` is keyed by primary-key UUID. `execute_results` is a queue consumed in
    call order — each `execute()` pops the next prepared `FakeResult`.
    """

    def __init__(self, *, get_map=None, execute_results=None):
        self.get_map = get_map or {}
        self._execute_results = list(execute_results or [])
        self.added = []
        self.committed = False
        self.rolled_back = False
        self.deleted = []

    async def get(self, _model, pk):
        return self.get_map.get(pk)

    async def execute(self, _stmt):
        if self._execute_results:
            return self._execute_results.pop(0)
        return FakeResult()

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    async def delete(self, obj):
        self.deleted.append(obj)

    async def refresh(self, _obj):
        pass


# ---------------------------------------------------------------------------
# Model factories (transient ORM instances — no session/flush needed)
# ---------------------------------------------------------------------------


def make_user(user_id=None, clerk_user_id="user_test", email="test@example.com"):
    return User(id=user_id or uuid.uuid4(), clerk_user_id=clerk_user_id, email=email)


def make_job(job_id=None, user_id=None, query="What is X?", status="done", completed_at=None):
    return ResearchJob(
        id=job_id or uuid.uuid4(),
        user_id=user_id or uuid.uuid4(),
        query=query,
        status=status,
        completed_at=completed_at,
    )


def make_report(report_id=None, job_id=None, user_id=None, raw_context=None):
    return Report(
        id=report_id or uuid.uuid4(),
        job_id=job_id or uuid.uuid4(),
        user_id=user_id or uuid.uuid4(),
        report_markdown="# Report",
        raw_context=raw_context if raw_context is not None else [],
        source_count=0,
        overall_sentiment=None,
    )


def make_followup(report_id, user_id, turn_number, question="Q?", answer="A."):
    return FollowUp(
        id=uuid.uuid4(),
        report_id=report_id,
        user_id=user_id,
        question=question,
        answer=answer,
        turn_number=turn_number,
    )


# ---------------------------------------------------------------------------
# App / HTTP client fixtures for endpoint tests
# ---------------------------------------------------------------------------


@pytest.fixture
def app():
    from app.main import app as fastapi_app

    yield fastapi_app
    fastapi_app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def override_deps(app):
    """Override get_current_user and get_db on the app for one test."""
    from app.auth import get_current_user
    from app.database import get_db

    def _apply(user, session):
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_db] = lambda: session

    return _apply

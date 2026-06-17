"""GET /reports/{id} and POST /reports/{id}/followup — auth/ownership/404/cap branching.

Tested against a fake DB session (see PRODUCTION_READINESS.md for why the API layer uses
a fake session rather than a real Postgres). These assert HTTP-level behaviour — status
codes, ownership checks, the 5-follow-up cap — not SQL.
"""

import uuid

from tests.conftest import (
    FakeResult,
    FakeSession,
    make_followup,
    make_job,
    make_report,
    make_user,
)


async def test_malformed_uuid_returns_404(client, override_deps):
    user = make_user()
    override_deps(user, FakeSession())

    resp = await client.get("/reports/not-a-uuid")

    assert resp.status_code == 404


async def test_not_found_returns_404(client, override_deps):
    user = make_user()
    override_deps(user, FakeSession(get_map={}))  # db.get returns None

    resp = await client.get(f"/reports/{uuid.uuid4()}")

    assert resp.status_code == 404


async def test_other_users_report_returns_403(client, override_deps):
    user = make_user()
    rid = uuid.uuid4()
    report = make_report(report_id=rid, user_id=uuid.uuid4())  # owned by someone else
    override_deps(user, FakeSession(get_map={rid: report}))

    resp = await client.get(f"/reports/{rid}")

    assert resp.status_code == 403


async def test_owner_gets_report(client, override_deps):
    user = make_user()
    rid, jid = uuid.uuid4(), uuid.uuid4()
    report = make_report(report_id=rid, job_id=jid, user_id=user.id)
    job = make_job(job_id=jid, user_id=user.id, query="my query")
    session = FakeSession(
        get_map={rid: report, jid: job},
        execute_results=[FakeResult(scalars_all=[])],  # no follow-ups
    )
    override_deps(user, session)

    resp = await client.get(f"/reports/{rid}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["report_id"] == str(rid)
    assert body["query"] == "my query"
    assert body["follow_ups"] == []


async def test_followup_cap_returns_429(client, override_deps, monkeypatch):
    user = make_user()
    rid = uuid.uuid4()
    report = make_report(report_id=rid, user_id=user.id)
    # Count query already returns 5 -> cap hit before any Gemini call.
    session = FakeSession(
        get_map={rid: report},
        execute_results=[FakeResult(scalar_one=5)],
    )
    override_deps(user, session)

    # Guard: the LLM must not be called when the cap is hit.
    import app.routers.reports as reports_mod

    async def boom(*_a, **_k):
        raise AssertionError("call_gemini_followup should not run when cap is reached")

    monkeypatch.setattr(reports_mod, "call_gemini_followup", boom)

    resp = await client.post(f"/reports/{rid}/followup", json={"question": "Why?"})

    assert resp.status_code == 429


async def test_followup_happy_path(client, override_deps, monkeypatch):
    user = make_user()
    rid, jid = uuid.uuid4(), uuid.uuid4()
    report = make_report(report_id=rid, job_id=jid, user_id=user.id)
    job = make_job(job_id=jid, user_id=user.id, query="original query")
    session = FakeSession(
        get_map={rid: report, jid: job},
        execute_results=[
            FakeResult(scalar_one=0),  # current follow-up count
            FakeResult(scalars_all=[]),  # prior turns
        ],
    )
    override_deps(user, session)

    import app.routers.reports as reports_mod

    async def fake_followup(**_kwargs):
        return "Here is the answer."

    monkeypatch.setattr(reports_mod, "call_gemini_followup", fake_followup)

    resp = await client.post(f"/reports/{rid}/followup", json={"question": "Why?"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "Here is the answer."
    assert body["turn_number"] == 1
    assert session.committed is True


async def test_followup_on_other_users_report_returns_403(client, override_deps):
    user = make_user()
    rid = uuid.uuid4()
    report = make_report(report_id=rid, user_id=uuid.uuid4())
    override_deps(user, FakeSession(get_map={rid: report}))

    resp = await client.post(f"/reports/{rid}/followup", json={"question": "Why?"})

    assert resp.status_code == 403


async def test_report_includes_existing_followups(client, override_deps):
    user = make_user()
    rid, jid = uuid.uuid4(), uuid.uuid4()
    report = make_report(report_id=rid, job_id=jid, user_id=user.id)
    job = make_job(job_id=jid, user_id=user.id)
    fu = make_followup(report_id=rid, user_id=user.id, turn_number=1, question="Q1", answer="A1")
    session = FakeSession(
        get_map={rid: report, jid: job},
        execute_results=[FakeResult(scalars_all=[fu])],
    )
    override_deps(user, session)

    resp = await client.get(f"/reports/{rid}")

    body = resp.json()
    assert len(body["follow_ups"]) == 1
    assert body["follow_ups"][0]["turn_number"] == 1

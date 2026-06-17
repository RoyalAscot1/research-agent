"""GET /jobs/{id}/status and the /history endpoints — auth/ownership/404 branching."""

import datetime
import uuid

from tests.conftest import FakeResult, FakeSession, make_job, make_report, make_user

# --- jobs ---------------------------------------------------------------------


async def test_status_malformed_uuid_returns_404(client, override_deps):
    override_deps(make_user(), FakeSession())
    resp = await client.get("/jobs/not-a-uuid/status")
    assert resp.status_code == 404


async def test_status_not_found_returns_404(client, override_deps):
    override_deps(make_user(), FakeSession(get_map={}))
    resp = await client.get(f"/jobs/{uuid.uuid4()}/status")
    assert resp.status_code == 404


async def test_status_other_users_job_returns_403(client, override_deps):
    user = make_user()
    jid = uuid.uuid4()
    job = make_job(job_id=jid, user_id=uuid.uuid4(), status="running")
    override_deps(user, FakeSession(get_map={jid: job}))

    resp = await client.get(f"/jobs/{jid}/status")

    assert resp.status_code == 403


async def test_status_running_omits_report_id(client, override_deps):
    user = make_user()
    jid = uuid.uuid4()
    job = make_job(job_id=jid, user_id=user.id, status="running")
    override_deps(user, FakeSession(get_map={jid: job}))

    resp = await client.get(f"/jobs/{jid}/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert "report_id" not in body


async def test_status_done_includes_report_id(client, override_deps):
    user = make_user()
    jid, rid = uuid.uuid4(), uuid.uuid4()
    job = make_job(job_id=jid, user_id=user.id, status="done")
    report = make_report(report_id=rid, job_id=jid, user_id=user.id)
    session = FakeSession(
        get_map={jid: job},
        execute_results=[FakeResult(scalar_one_or_none=report)],
    )
    override_deps(user, session)

    resp = await client.get(f"/jobs/{jid}/status")

    body = resp.json()
    assert body["status"] == "done"
    assert body["report_id"] == str(rid)


# --- history ------------------------------------------------------------------


async def test_history_lists_user_reports(client, override_deps):
    user = make_user()
    rid, jid = uuid.uuid4(), uuid.uuid4()
    report = make_report(report_id=rid, job_id=jid, user_id=user.id)
    created = datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc)
    # list_history selects rows of (Report, query, created_at)
    session = FakeSession(execute_results=[FakeResult(all_=[(report, "my query", created)])])
    override_deps(user, session)

    resp = await client.get("/history")

    assert resp.status_code == 200
    reports = resp.json()["reports"]
    assert len(reports) == 1
    assert reports[0]["query"] == "my query"
    assert reports[0]["report_id"] == str(rid)


async def test_delete_malformed_uuid_returns_404(client, override_deps):
    override_deps(make_user(), FakeSession())
    resp = await client.delete("/history/not-a-uuid")
    assert resp.status_code == 404


async def test_delete_other_users_report_returns_403(client, override_deps):
    user = make_user()
    rid = uuid.uuid4()
    report = make_report(report_id=rid, user_id=uuid.uuid4())
    override_deps(user, FakeSession(get_map={rid: report}))

    resp = await client.delete(f"/history/{rid}")

    assert resp.status_code == 403


async def test_delete_owned_report_succeeds(client, override_deps):
    user = make_user()
    rid = uuid.uuid4()
    report = make_report(report_id=rid, user_id=user.id)
    session = FakeSession(get_map={rid: report})
    override_deps(user, session)

    resp = await client.delete(f"/history/{rid}")

    assert resp.status_code == 204
    assert session.committed is True

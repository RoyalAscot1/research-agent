# Production Readiness

A phased plan of action to take Lens from a working side project to a portfolio-ready
showcase of **agentic AI + Docker + deployment + tests + production considerations**. Phases
are sequenced by dependency and by *which resume claim each one makes true* — do them roughly
in order. Items tagged **[critical]** block everything after them.

Deploy architecture (per `app_summary.md`): the frontend deploys to **Vercel** (git
auto-deploy, no Docker), and the backend deploys to **Render** as a Docker container.

**Phases 1–6 are the portfolio-critical path** (~4.5 focused days). The **Deferred** and
**Maintainability** sections at the end are real but *not* required to put this on a resume —
documenting them as deliberate scope calls is itself the "I know the gaps" senior signal.

Provenance tags: *(Undocumented)* = found in this review, not in the project docs;
*(Already noted)* / *(Partly noted)* = referenced in `CLAUDE.md` / `app_summary.md`.

---

## Phase 1 — Security & correctness foundation (~half a day)

*Goal: the app is safe and bug-free on its core path before anyone sees it.*

**[done] Pin the JWT issuer + verify audience/`azp` (auth bypass)** (`backend/app/auth.py`).
~~The issuer is read from the token's *own unverified payload* and the JWKS is then
fetched from that URL — there is no allowlist tying it to your Clerk instance. An attacker
can host their own JWKS, self-sign a token with any `sub`, set `iss` to their domain, and
it validates: full token forgery / impersonation.~~ **Fixed**: `get_current_user` now rejects
any token whose `iss` doesn't match `settings.clerk_issuer` *before* fetching the JWKS, and
rejects any token whose `azp` isn't in `settings.clerk_authorized_parties` after signature
verification. New env vars `CLERK_ISSUER` and `CLERK_AUTHORIZED_PARTIES` added to
`backend/.env(.example)` and `config.py`. Verified live: a forged/garbage token returns 401,
a real Clerk session token (matching `iss`/`azp`) returns 200 on `/history`.

**[done] `CLERK_SECRET_KEY` is documented as required but the backend never uses it**
(`backend/.env.example`, `CLAUDE.md`; absent from `backend/app/config.py`). ~~`config.py`
doesn't declare it and `extra="ignore"` silently drops it, so no code reads it — reinforcing
the auth-bypass item above (the backend has no binding to your actual Clerk instance).~~
**Fixed (remove, not wire-in)**: the backend verifies Clerk JWTs asymmetrically via the public
JWKS (RS256), so the secret key is never needed — it's only for server-side Clerk Backend API
calls this backend doesn't make. Removed `CLERK_SECRET_KEY` from `backend/.env.example` and the
backend env-var list in `CLAUDE.md` so it no longer implies a security binding that isn't there.
Frontend references stay — the Next.js middleware genuinely uses it. (Undocumented.)

**[done] Concurrent first-request user creation can 500** (`backend/app/auth.py`).
~~The lazy upsert did `select` → if missing, `insert` + `commit`. Two near-simultaneous
first requests from a brand-new user both saw "no user" and both inserted, violating the
`clerk_user_id` unique constraint → unhandled `IntegrityError` → 500.~~ **Fixed**: the
insert now uses Postgres `INSERT ... ON CONFLICT (clerk_user_id) DO NOTHING`, followed by
a re-select to fetch the row regardless of which concurrent request won the insert. A
`user.created` webhook would also sidestep it, but the lazy path is now safe on its own.
(Undocumented.)

**[done] Frontend polls forever when a job is `done` but has no report** (`frontend/app/page.tsx`,
lines 110–117). ~~`GET /jobs/{id}/status` can legitimately return `status: "done"` with
`report_id: null` (`jobs.py:41`). The poll only redirects on `done && report_id` and only
errors on `failed`, so that case falls through to the `else` branch and re-polls
indefinitely with no end state.~~ **Fixed**: added a `done && !report_id` branch that sets
the error state ("Research finished but no report was generated. Please try again.")
instead of falling through to the re-poll `else`. Verified live by overriding `window.fetch`
in the browser console to mock `POST /queries` and `GET /jobs/{id}/status` (returning
`status: "done", report_id: null`) — the UI now surfaces the error immediately instead of
polling forever. (Undocumented.)

**[done] Frontend poll cap for stuck `running` jobs** (`frontend/app/page.tsx`, line 104).
~~If a job never completes (e.g. a Render restart/spin-down mid-run kills the in-process
task), the prompt screen polls forever with an eternal spinner. Add a cap (N ticks or a time
budget) that surfaces an error state.~~ **Fixed**: a `pollCountRef` tracks attempts; once it
exceeds `MAX_POLL_ATTEMPTS` (90, ~3 minutes at 2s/poll) the poll loop bails to the error state
("This is taking longer than expected. Please try again.") instead of rescheduling. 90 was
chosen generously — it also backstops the not-yet-fixed missing-Gemini-timeout item below,
since right now a hung Gemini call has no other backstop. Verified live by temporarily
lowering the cap to 3 and mocking `/jobs/{id}/status` to always return `status: "running"` —
the UI errored out after 3 polls instead of spinning forever. This handles the *symptom*; the
root-cause fix (a durable task queue) is a deferred real-production item — see **Deferred**.
(Undocumented.)

**[done] No timeout on the Gemini/LLM calls** (`backend/app/graph/graph.py`). ~~No
`timeout`/`request_timeout` is set on the `ChatGoogleGenerativeAI(...)` sites, so a hung
Gemini request leaves the job stuck in `running` forever (which the poll cap above then
surfaces). Set a request timeout so a stalled call fails fast instead.~~ **Fixed**: added a
module-level `_LLM_REQUEST_TIMEOUT_SECONDS = 60` constant and wrapped all four `ainvoke`
calls (planner, coverage_check, synthesizer, `call_gemini_followup`; researcher/sentiment
don't call Gemini) in `asyncio.wait_for(...)`. **The constructor's `timeout=` kwarg was tried
first and verified non-functional** — a 0.001s constructor timeout still let the request
complete, because the deprecated `google.generativeai` client silently ignores that field —
so enforcement is at the asyncio layer instead (provider-agnostic, guaranteed to raise).
On timeout the existing per-node try/except turns the raised `TimeoutError` into a fail-soft
for planner/coverage and a `failed` job for the synthesizer — no more zombie `running` jobs.
**Also fixed a latent bug this surfaced**: the synthesizer's `error: str(exc)` is `""` for
`asyncio.TimeoutError`, and `run_graph`'s `if state["error"]:` treats a falsy error as
success — a synthesizer timeout would have persisted a null report and marked the job `done`.
Changed to `str(exc) or type(exc).__name__` so the failure is non-empty. Verified live: a real
query plans/synthesizes normally at 60s; forcing the timeout to 0.001s makes the planner take
its raw-query fallback and the synthesizer set `error="TimeoutError"` → job `failed`. A shared
`_make_llm()` factory to dedup the repeated config is deliberately deferred to the
Maintainability section (gated on the Phase 2 test suite). (Undocumented.)

**[done] Treat empty synthesizer output as a failure** (`backend/app/graph/graph.py`,
`synthesizer_node`). ~~The job is marked `done` even when `report_markdown` is empty with no
error set, persisting a blank report with no failure signal.~~ **Fixed**: after the
`ainvoke`, the synthesizer normalises `response.content` (joining the list-of-parts shape into
a string) and, if it's empty or whitespace-only, returns `error="Synthesizer produced an empty
report"` with `report_markdown=None`. That non-empty error string trips `run_graph`'s
`if state["error"]:` so the job is marked `failed` instead of persisting a blank report — the
same falsy-error gotcha the synthesizer-timeout fix relied on. Verified by mocking the LLM to
return `""`, whitespace, an empty list, a real string, and a real list-of-parts: the first
three fail, the last two pass through with the list correctly joined. (Undocumented.)

**[deferred] Blocking synchronous I/O on the async event loop** (`backend/app/graph/graph.py`).
`researcher_node` calls `TavilyClient.search()` and `sentiment_node` calls the googleapiclient
`.execute()` (one YouTube search + one `commentThreads` call per video) — all synchronous,
blocking network calls — inside `async def` nodes run via `_graph.ainvoke`. Because `run_graph`
runs in `BackgroundTasks` (the *same* event loop as the web server), every Tavily/YouTube call
freezes all other incoming requests for its full latency; with up to 5 Tavily calls + YouTube
fetches per run, the API stalls in multi-second chunks under concurrency. The in-process
mitigation is to wrap each call in `asyncio.to_thread(...)` (or use `AsyncTavilyClient`).
**Deliberately deferred to the durable task queue (see Deferred) rather than fixed in place.**
The queue is a *superset* fix: moving the whole job out of the web process means these blocking
calls never touch the API event loop at all, and it simultaneously solves the in-process
`BackgroundTasks` durability problem — so the `to_thread` patch would be throwaway work we'd rip
out when the queue lands. For an async-native stack (FastAPI async, async SQLAlchemy,
`LangGraph.ainvoke`) the queue of choice is **ARQ**, not Celery — see the Deferred entry for the
rationale. The blocking only bites under *concurrent* users, which a private/recorded
single-user demo never hits. (Undocumented.)

**[done] Add a unique constraint on `reports.job_id`** (`backend/app/models/models.py`,
plus a migration). ~~The model declares a one-to-one (`uselist=False`) and `jobs.py` uses
`scalar_one_or_none()` on reports-by-job (line 37–40), but nothing enforces one report per
job at the DB level — a retry/bug producing two rows makes that query raise
`MultipleResultsFound` → 500. Cheaper to add before production data exists.~~ **Fixed**: added
`unique=True` to the `job_id` column (matching `User.clerk_user_id` in the same file) and a
hand-written migration (`uq_reports_job_id`, revision `a1b2c3d4e5f6`) that adds the named
unique constraint. Verified no duplicate `job_id` rows existed before applying (0 of 14
reports), ran `alembic upgrade head` cleanly, and confirmed the constraint now exists in
Postgres (`pg_constraint` shows `uq_reports_job_id`). The DB invariant now backs the ORM's
`uselist=False` / `scalar_one_or_none()` assumption. (Undocumented.)

**[done] Follow-up race / unique constraint** (`backend/app/routers/reports.py`, plus
a new migration). ~~The count → cap-check → insert sequence isn't atomic and has a slow
Gemini call in the middle, so concurrent follow-ups for the same report can both pass the
5-cap and insert a duplicate `turn_number` (which the frontend uses as a React key). Add a
unique constraint on `(report_id, turn_number)` — much cheaper before production data
exists — and catch the resulting `IntegrityError` to return a clean 409.~~ **Fixed**: added a
composite `UniqueConstraint("report_id", "turn_number", name="uq_follow_ups_report_turn")` to
the `FollowUp` model and a hand-written migration (revision `b2c3d4e5f6a7`) creating it, then
wrapped the follow-up `commit()` in a `try/except IntegrityError` that rolls back and raises a
clean **409** ("A concurrent follow-up was submitted — please retry.") instead of a 500.
Verified no duplicate `(report_id, turn_number)` rows existed before applying (0 of 8
follow-ups), ran `alembic upgrade head` cleanly, and confirmed `pg_constraint` shows
`uq_follow_ups_report_turn`. The DB now backs the per-report turn-number invariant the
frontend relies on for React keys. (Undocumented.)

*Quick wins to fold in here (small, prevent code-review embarrassment):*

- **[done] Coerce `response.content` to `str`** (`backend/app/graph/graph.py`). LangChain's
  `content` can be a string or a list; the return type and the DB write assume it's always a
  string. **Fixed**: extracted a shared `_content_to_str()` helper (joins the list-of-parts
  shape into a string, passes a plain string through) and routed **both** call sites through
  it — `call_gemini_followup` now returns `_content_to_str(response.content)` instead of the
  raw content, and `synthesizer_node`'s previously-inline copy of the same logic was replaced
  with a call to the helper (dedup, not just an add). The tidy shared-helper option was chosen
  over inlining since the logic now lived in two places. Verified the helper against a plain
  string, a list of strings, a list with a non-string part, and an empty string. (Undocumented.)
- **[done] `HTTPBearer()` returns 403, not 401, on a missing token** (`backend/app/auth.py`).
  ~~`auto_error=True` (the default) raises 403 when the `Authorization` header is absent,
  but the frontend's "Session expired, sign in again" messaging implies 401. Use
  `HTTPBearer(auto_error=False)` + an explicit 401.~~ **Fixed**: switched `_bearer` to
  `HTTPBearer(auto_error=False)` so a missing/blank header yields `None` instead of an
  auto-403, typed the `credentials` param `… | None`, and added an explicit `if credentials
  is None: raise unauth` (401) check. The `unauth` 401 was also moved **above** the
  `token = credentials.credentials` access — otherwise a `None` would `AttributeError` into a
  500 instead of the clean 401. Verified by calling the dependency directly: a missing header
  and a garbage token both return 401 (previously 403 and 401 respectively). (Undocumented.)

---

## Phase 2 — Tests (~1 day)

*Goal: make the "tests" claim true; unblocks CI (Phase 4) and the Maintainability refactors.*

**[done] Test suite** (`backend/tests/`, `pytest` + `pytest-asyncio`, both already in
`requirements.txt`). **Done**: 70 tests, green locally and ruff-clean (so it drops straight
into Phase 4 CI). No external services touched — LLM/Tavily/YouTube and the DB are all faked.
Run with `python -m pytest` from `backend/` (venv active). Layout:

- `tests/unit/` — **Graph-node tests** with the LLM / Tavily / YouTube **mocked**: the re-plan
  loop, the hard caps (3 iterations / 5 Tavily calls, incl. the coverage short-circuit that
  asserts the LLM is *never* called once a cap is hit), the planner/coverage fail-safe
  fallbacks, the synthesizer empty-output and `TimeoutError`-→-non-empty-error gotchas, the
  sentiment fail-soft paths, and `_route_after_coverage` branching. Plus the **auth-dependency
  test** — `test_forged_issuer_is_rejected` proves the Phase 1 auth-bypass fix by asserting the
  JWKS is *never fetched* for a forged `iss`; also bad-signature, `azp`-mismatch,
  missing/garbage token, and a real happy path (in-test RSA keypair, mocked JWKS, upsert).
- `tests/api/` — **Endpoint tests** via httpx `ASGITransport`: malformed-UUID 404,
  not-found 404, ownership 403, and the follow-up cap 429 (with a guard asserting Gemini is
  *not* called when capped), plus happy paths.

**Mocking conventions** (mirrored in `CLAUDE.md` "Testing"): external clients are patched by
name in `app.graph.graph` (`ChatGoogleGenerativeAI`, `TavilyClient`, `build`); `conftest.py`
sets dummy env vars *before* any `app.*` import because `config.py` runs `Settings()` at
import time; endpoint tests use a `FakeSession` + `app.dependency_overrides`, not a real DB.

**Deliberate gap** (documented, not an oversight): because the API layer is tested against a
fake session (not testcontainers — see the choice rationale above), DB-level constraints
aren't exercised — notably the follow-up `409` from `uq_follow_ups_report_turn`. A
testcontainers-Postgres upgrade is the path to closing it if higher fidelity is ever wanted.

- A **rate-limit test** (429 past the cap) was deferred here until `slowapi` landed — **now
  done** in Phase 3 (`tests/api/test_rate_limit.py`); see the Phase 3 rate-limiting item.

---

## Phase 3 — Containerize, secure & deploy (~1.5 days)

*Goal: make "Docker + deployment" true, get a live URL, and never expose uncapped paid APIs.*

**✅ Phase 3 complete — deployed and verified end-to-end.** Backend live on **Render** (Docker,
`/health` check), frontend live on **Vercel** at `https://lens-research.vercel.app`. A full
sign-in → query → report → follow-up works in production. Three deploy-time issues surfaced and
were fixed (all captured in `CLAUDE.md` gotchas): the unused frontend Prisma client broke the
Vercel build (removed), Clerk's `middleware.ts` failed on the Edge runtime (renamed to `proxy.ts`
→ Node.js runtime), and a Vercel "No framework detected" 404 (Root Directory + Framework Preset
settings). Still on Clerk **dev** keys and Tavily `"basic"` (both deliberate). Item-level status
below.

**[done] Rate limiting / per-user quota** (`backend/app/routers/queries.py`; also
`POST /reports/{id}/followup`). ~~Every query fires Tavily, Gemini, and YouTube calls, all
metered or paid. With no cap, a single signed-in user (or a leaked token) can drain your
quotas and run up cost in minutes.~~ **Fixed**: added `slowapi` and a new `app/rate_limit.py`
with a `Limiter` keyed on the Clerk user id (not IP) — `get_current_user` now stashes the
verified `sub` on `request.state.clerk_user_id` and the key function reads it, falling back to
the remote address if unset. `POST /queries` is capped at `10/hour;3/minute` (where the real
cost is) and `POST /reports/{id}/followup` at `20/hour;5/minute`; both deliberately generous
for a low-traffic portfolio app. A `RateLimitExceeded` handler returns a clean **429** in the
app's `{"detail": ...}` shape with the standard rate-limit headers. Storage is slowapi's
in-memory backend (per-process, resets on restart) — fine for a single Render instance; a
shared Redis backend is the multi-instance/durable upgrade (pairs with the deferred ARQ/Redis
work). Gated by `RATE_LIMIT_ENABLED` (default true; the test suite sets it false so it isn't
throttled). **Closes the Phase 2 deferred 429 test**: `tests/api/test_rate_limit.py` re-enables
the limiter and drives `/queries` past the per-minute cap to assert the 429. (Already noted.)

**[done] Add a `.dockerignore`** in `backend/`. ~~The Dockerfile's `COPY . .` currently bakes
`.env`, `.venv/`, and `__pycache__` into image layers — a secret-leak risk and needless
bloat.~~ **Fixed**: added `backend/.dockerignore` excluding `.env`/`.env.*` (keeps
`.env.example` via a `!` un-ignore), `.venv`/`venv`, the Python caches (`__pycache__`,
`*.py[cod]`, `.pytest_cache`, `.ruff_cache`, `.mypy_cache`), `tests/` (not needed in the
runtime image — Phase 4 CI runs pytest directly, not against the image), and build/VCS/OS
noise (`.git`, `Dockerfile`, `.dockerignore`, `.DS_Store`). `alembic/` is deliberately *kept*
in the image since the release step runs `alembic upgrade head`. Not yet exercised by a local
`docker build` — the filter is verified the first time the image is built (locally or on
Render). (Undocumented.)

**[done] Drop unused heavyweight dependencies** (`backend/requirements.txt`). ~~`chromadb==0.5.18`,
`nltk==3.9.1`, and `python-dotenv==1.0.1` are imported nowhere (confirmed by grep).
`chromadb` is the costly one — it drags in `onnxruntime` and adds hundreds of MB to the
image and to every build; `nltk` is redundant (`vaderSentiment` is standalone) and
`python-dotenv` is unused (pydantic-settings reads `.env` itself). `chromadb` likely landed
in anticipation of the v2 vector work that became pgvector.~~ **Fixed**: removed all three
lines from `requirements.txt` after re-confirming with a fresh `grep -rniE
"chromadb|chroma|\bnltk\b|dotenv"` across `backend/**/*.py` (excluding `.venv`) — zero matches.
The local `.venv` still has them physically installed (removal only affects the next image
build, not the venv), so a local `pytest` can't prove the trim is safe; the clean
`docker build`/Render build is the real verification. Same "image hygiene before first
deploy" theme as `.dockerignore`. (Undocumented.)

**Deploy plumbing — no code change, all Render dashboard config.** The app is already
parameterized for this, so the earlier "edit `main.py:18`" framing is stale:
- **CORS origin**: `main.py` already reads `settings.frontend_url` (env `FRONTEND_URL`,
  default `localhost:3000` in `config.py`) — there is no hardcoded origin to edit. Just set
  `FRONTEND_URL` to the production Vercel URL in Render's env vars. (Chicken-and-egg: the
  Vercel URL doesn't exist until the frontend deploys, so set this on the circle-back pass.)
- **Migrations**: the Dockerfile `CMD` only starts uvicorn, so set Render's **Pre-Deploy
  Command** to `alembic upgrade head` so the schema exists before the app boots. `alembic/env.py`
  reads the DB URL from the `DATABASE_URL` env var via the async (asyncpg) engine — the same one
  the app uses — so it just works as long as `DATABASE_URL` is set in the
  `postgresql+asyncpg://…?ssl=require` form (the Alembic gotcha in `CLAUDE.md`).
The frontend is on Vercel and auto-deploys from git, so there is no frontend Dockerfile — only
the backend is containerized. (Partly noted as "step 17 deploy.")

**Wire `/health` as Render's health check** (`backend/app/main.py`, line 30). The endpoint
already exists; just configure Render to use it. (Undocumented.)

**[decided: keep `"basic"`] Tavily `search_depth`** (`backend/app/graph/graph.py`, line 257).
This is both a quality and a cost knob. **Decision: ship `"basic"` (1 credit/search),
do not flip to `"advanced"`.** The deeper extraction isn't worth the extra
credit cost; `"advanced"` is the lever to revisit only if source quality ever proves
insufficient. No longer a pre-deploy action. (Already noted in the docs.)

**[done — not a real issue] Verify the `next build` type error** on the nullable `result`
(`frontend/app/page.tsx`, now line 148). The worry: with `strict: true`, `result` might be
dereferenced while still possibly-null across the try/catch, which would block the Vercel
build (Turbopack dev mode skips typechecking, so it could have been hidden). **Verified by
running a real `npm run build` (Node 20):** TypeScript completed clean, no errors — the
`let result: {...} | null = null` pattern type-checks fine. The Vercel build is not at risk
from this. (Build did surface one *non-blocking* deprecation warning — Next.js 16 wants the
`middleware` file renamed to `proxy`; left as-is since it's a Clerk-touching rename and only
a warning, not a build failure.) (Undocumented.)

**Clerk production instance** — *only needed for a public sign-in demo.* Clerk dev and
production are separate instances, not a toggle. Create the production instance, swap the
`pk_test`/`sk_test` keys for `pk_live`/`sk_live` in `frontend/.env.local`, `backend/.env`,
and the Render/Vercel environment, set up a custom domain with the DNS/SSL records Clerk
provides, and set the app name and logo in the dashboard (this fixes the "Sign in to Clerk"
branding and removes the dev-mode badge). Critically, you must also supply your **own Google
OAuth credentials** — dev mode borrows Clerk's shared Google credentials, so Google sign-in
breaks on a production instance until you create a Google Cloud OAuth app and plug the client
ID/secret into Clerk. **Decision point:** for a portfolio, a private/recorded demo on dev
keys is much faster; do this only if you want a publicly signable-in URL. (Partly noted.)

**[done] Create & deploy the Render backend service — the actual deploy, not just prep.**
**Done**: the backend is live as a Render Docker Web Service (root `backend/`, the existing
`Dockerfile`), all env vars set in the dashboard, health check `/health`, `FRONTEND_URL` +
`CLERK_AUTHORIZED_PARTIES` pointed at the Vercel URL, and `GET /health` returns 200 on the live
URL. One deviation from the prep below: **`alembic upgrade head` was *not* wired as a pre-deploy
command** — Render's Pre-Deploy Command is a paid feature, and the live Neon DB was already at
head (same DB used in dev), so migrations were left manual rather than baked into container
startup. If a future migration ships, either run `alembic upgrade head` against the prod DB
manually or fold it into the Dockerfile `CMD`. Original prep notes retained below for reference.
Everything
above readies the image/config; this is the step that produces a live backend URL, and it
gates Phase 4's auto-deploy-on-merge. Concretely: (1) create a Render **Web Service** from the
repo with `backend/` as the root and the existing `backend/Dockerfile` as the runtime; (2)
**bind to Render's `$PORT`** — **[done]** the Dockerfile CMD now binds to `${PORT:-8000}`
(`backend/Dockerfile`, line 11) so it uses Render's injected `$PORT` and falls back to 8000
locally. Note the CMD was switched from exec-form (`["uvicorn", ...]`) to shell-form
(`sh -c "..."`) because the exec form does not expand env vars — `${PORT}` would be passed
literally; (3) set every backend env var from `backend/.env.example` in the Render dashboard
(`DATABASE_URL`, `GEMINI_API_KEY`, `TAVILY_API_KEY`, `YOUTUBE_API_KEY`, the `LANGFUSE_*`
keys, and `FRONTEND_URL` set to the Vercel URL once known — they reference each other, so
expect to circle back); (4) set the **health check path to `/health`** (the wiring item
above); (5) add the `alembic upgrade head` release/pre-deploy command (the deploy-plumbing
item above); (6) trigger the first deploy and confirm `GET /health` returns `200` on the live
URL. **Sequence this after the `.dockerignore`, dropped-deps, and deploy-plumbing items** —
they all have to be true *in the image you ship here.* (Undocumented — the prep was listed but
the deploy action itself was missing.)

**[done] Set up the Vercel frontend project + first deploy.** **Done**: live at
`https://lens-research.vercel.app`, Root Directory `frontend`, Framework Preset Next.js,
env vars `NEXT_PUBLIC_API_URL` (→ Render) + the two Clerk keys set (`DATABASE_URL` deliberately
skipped), auto-deploy on push to `main`, and the backend circle-back (`FRONTEND_URL` +
`CLERK_AUTHORIZED_PARTIES`) done. **Three issues hit and fixed along the way:** (a) the build
failed on `lib/prisma.ts` importing the gitignored generated client → removed the dead file
(see the Prisma stack Maintainability item, partly done); (b) the build then failed with "Edge
Function references unsupported modules" because Clerk's `middleware.ts` ran on the Edge runtime
→ renamed to `proxy.ts` (Node.js runtime); (c) a "No framework detected" 404-on-everything →
Vercel Framework Preset/Root Directory settings. All three are now `CLAUDE.md` gotchas so they
don't recur. Original prep notes retained below. The frontend has no Dockerfile (Vercel
builds it from git), so "deploy" here means project setup, not containerization: (1) import
the repo into Vercel with `frontend/` as the project root; (2) set the frontend env vars from
`frontend/.env.local.example` in the Vercel dashboard — `NEXT_PUBLIC_API_URL` pointing at the
live Render URL from the step above, `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, and
`CLERK_SECRET_KEY`. **Skip `DATABASE_URL` unless you keep the Prisma stack** — it's only read
by the unused frontend Prisma client (see the "Remove the unused Prisma stack" Maintainability
item); setting it ships your Postgres credentials to the Vercel build for nothing. (3) confirm
the production build succeeds on Vercel (do the `next build`
type-error verification item *first* — a `strict`-mode failure blocks this build); (4) once
the Vercel URL exists, set it as the backend's `FRONTEND_URL`/CORS origin on Render and
redeploy the backend so cross-origin calls are allowed. Auto-deploy-on-push is on by default
once the project is imported, so no extra CI is needed for the frontend (the Render
auto-deploy is wired in Phase 4). (Undocumented.)

---

## Phase 4 — CI/CD (~half a day)

*Goal: the "production considerations" signal; ties tests + deploy together with green checks.*

**GitHub Actions.** Run `ruff` lint + `pytest` on every push, and trigger the Render deploy
on merge to `main`. Table stakes for a production-aware project, and the visible green checks
on PRs are exactly the signal a reviewer looks for. (Already noted as build-order step 17.)
The **Ruff side is ready**: `backend/ruff.toml` exists (rules `E/W/F/I/B/UP/C4`, `target-version
= "py310"` to match the local venv, FastAPI `Depends()` whitelisted) and the backend is
lint-clean + formatted, so the CI workflow just needs to call `ruff check` / `ruff format
--check`. The `pytest` half waits on the Phase 2 suite.

**[done] CI workflow live** (`.github/workflows/ci.yml`). Two parallel jobs:
`lint` (`ruff check .` + `ruff format --check .`) and `test` (`python -m pytest`), each set
up with `working-directory: backend`, **Python 3.12 to match `backend/Dockerfile`** (not the
local 3.10 venv — CI should exercise the version production runs), `actions/setup-python`
pip-caching keyed on `backend/requirements.txt`, and a `concurrency` group that cancels an
in-progress run when newer commits land on the same ref. Uses the **Node 24 action majors**
(`actions/checkout@v5`, `actions/setup-python@v6`) — the `@v4`/`@v5` pair emitted two "Node 20
is deprecated" runner annotations, the bump cleared both (verified 2 → 0 via the check-runs
API). `ruff` and `pytest` both come from `requirements.txt` (pinned `ruff==0.7.0`,
`pytest==8.3.3`), so a single `pip install -r requirements.txt` provides both tools. Triggers:
`pull_request` into `main` and `push` to any branch (early feedback). **Verified green on PRs
and on `main`** (merge commit `ba1bfa8`), 71 passed + ruff clean, zero annotations. The jobs
are named `lint` / `test` so they surface as two distinct, selectable status checks.

**[done] Branch protection on `main`** — a GitHub **ruleset** ("Branch protection",
enforcement `active`, confirmed via the `/rulesets` API): require a pull request before merging
(blocks direct pushes), require the `lint` + `test` status checks, require branches up to date,
block force pushes, **empty bypass list** so it applies to the solo owner too. **Required
approvals deliberately set to `0`** — GitHub won't let an author approve their own PR, so any
non-zero value would deadlock merges on a solo repo; the gate's teeth come from "require a PR" +
the required status checks, not from a human approver. This makes "can't merge broken code"
real, and since deploys fire from `main`, keeps broken code from deploying.

**Render does NOT auto-deploy this service — deploys are manual (corrected 2026-06-19).** The
earlier claim here — that Render skips deploys via Root-Directory path detection — was wrong.
Per Render's docs, auto-deploy requires a linked GitHub/GitLab/Bitbucket *account*; a service
connected via a **public Git repository URL or a prebuilt Docker image must be deployed
manually**, and this backend is connected that way, so the "Auto-Deploy: On Commit" toggle is
inert and no push/merge ever triggers a build. This was caught when the entire Phase-5 logging
PR (and everything merged since PR #23) silently never shipped — the live container sat on the
last *manually* deployed commit while CI was green and PRs were merged. **Verify the live commit
SHA in Render → Events after every backend merge**, and ship with **Manual Deploy → "Clear build
cache & deploy."** Vercel *does* auto-deploy the frontend (linked-account connection).

**Still optional (Phase 4 polish, not required) — and now the real fix for the manual-deploy
problem above:** a `main`-only workflow job that `curl`s Render's **Deploy Hook** URL. A deploy
hook triggers a build regardless of connection mode, so it both automates Render *and* gates the
deploy behind CI. Vercel already auto-deploys from `main`; this closes the gap for Render.

Note the workflow runs on `pull_request` using the file from the PR head branch, so it
exercised itself on the very first PR even before `ci.yml` existed on `main`; now that it's
merged to `main`, future PRs into `main` trigger it too.

---

## Phase 5 — Observability (~half a day)

*Goal: complete the three-layer story — logs + tracing + alerting. Most impressive once the
app is deployed and producing real logs, which is why it follows Phase 3.*

**[done] Structured logging** (whole backend). ~~No logging exists today; `print`-free but also
log-free.~~ **Done** on the `phase-5-logging` branch (purely additive — all 71 tests stayed green
unchanged; ruff clean + formatted). Implementation:

  - **Logger**: `structlog` emitting one JSON line per event to stdout, configured once via
    `configure_logging()` in the new `app/logging_config.py`, called at the top of `main.py`.
    Stdlib/uvicorn logs are routed through the same JSON renderer so the stream is uniform.
  - **Correlation IDs**: `bind_job_id(job_id)` at the top of `run_graph` binds the id to
    `structlog.contextvars` so every line for one research run is filterable end to end
    (cleared in a `finally`). A `main.py` request-middleware binds a per-request id, logs
    `request.complete`/`request.error` (method/path/status/latency), and echoes it as the
    `X-Request-ID` response header.
  - **Node boundaries**: `planner.complete`, `researcher.complete` (sources/tavily_calls/
    iteration), `coverage.decision` (`reason="hard_cap"|"model"`, `sufficient`),
    `sentiment.complete`, `synthesizer.complete` — the re-plan loop is now visible without
    opening Langfuse.
  - **Silent errors now speak**: the seven `except Exception: pass`/swallow blocks in
    `graph.py` log warnings (`tavily.search_failed`, `researcher.failed`, `sentiment.failed`,
    `coverage.check_failed`, `planner.fallback`, `synthesizer.failed`/`synthesizer.empty_report`;
    the expected comments-disabled skip is DEBUG). The worst was `run_graph`'s outer catch,
    which discarded DB exceptions entirely and now `log.exception`s.
  - **Auth + lifecycle**: `auth.rejected` (WARNING) with a `reason`
    (`missing_token`/`malformed_token`/`iss_mismatch`/`unknown_kid`/`bad_signature`/
    `azp_mismatch`) — **never logging the token itself**; `job.created`, `followup.cap_reached`,
    `followup.conflict`, `followup.created`.
  - **Levels**: INFO for lifecycle/node boundaries, WARNING for fail-soft degradations,
    `log.exception` for the synthesizer failure and the `run_graph` DB-rollback path.
  - **Config**: gated by `LOG_LEVEL` (default INFO) + `ENVIRONMENT` (default development),
    both optional `Settings` fields with defaults (no `conftest.py` dummy needed).

  Verified live by smoke-testing the JSON output (job_id binds then clears) and hitting
  `/health` through a real ASGI request (the middleware emitted `request.complete` + set
  `X-Request-ID`). **Complements** the already-live Langfuse tracing rather than duplicating it:
  Langfuse captures LLM-call traces (prompts, outputs, per-node latency); structured logs
  cover the non-LLM operational surface — DB writes, auth, Tavily/YouTube failures, and the
  request lifecycle — and are what you'll actually grep in Render when something breaks.
  (Undocumented before this pass; now in `CLAUDE.md` Current state + a "Logging" gotcha.)

**[code done; activation deferred] Sentry for error alerting** (`backend/app/main.py` startup
+ `run_graph`). ~~A ~two-line `sentry-sdk` init gives real error capture and alerting on top of
the logs above — logs tell you what happened after you go looking; Sentry tells you *that*
something broke without you looking.~~ **Code done** on the `phase-5-logging` branch (alongside
the logging half — the branch ended up carrying both Phase 5 pieces). `sentry-sdk[fastapi]==2.19.2`
init in `main.py`, **guarded by `if settings.sentry_dsn`** so it's a no-op until a DSN is set
(verified: DSN unset → `get_client().is_active()` is False, and `capture_exception` is a safe
no-op). The FastAPI integration auto-captures unhandled request exceptions; `run_graph` is a
`BackgroundTask` that catches its own exceptions (to mark the job `failed`), so those never
reach the integration — captured explicitly via `sentry_sdk.capture_exception()` in the
`run_graph` outer `except`. `traces_sample_rate=0.0` (request/LLM tracing is Langfuse's job;
Sentry is errors-only), events tagged with `ENVIRONMENT`. All 71 tests stayed green.

  **Activation is deliberately deferred** (create a Sentry project, set `SENTRY_DSN` + confirm
  `ENVIRONMENT=production` in Render): Lens is a no-real-users portfolio/demo app, so
  alerting-when-nobody's-watching has little functional value, and the structured logs already
  cover single-user forensics. The wired, correctly-designed integration is the portfolio
  signal; turning it on is a two-minute config flip, not a code change. The three-layer
  observability story — Langfuse (LLM traces) + structured logs (operational) + Sentry
  (alerting) — is complete in code, with the last layer armed but not firing by choice.
  (Already noted in the docs.)

---

## Phase 6 — README & storefront (~half a day)

*Goal: the most-read file tells the story.*

**[done] Real root `README.md` written** (`README.md`), following the structure below: pitch +
CI badge, two screenshots (prompt + report) under the intro, *What it does*, *The agent* (with a
Mermaid graph diagram + the hard-cap cost story), *Architecture* (Mermaid topology diagram),
*Tech stack* (compact table linking to `app_summary.md`, not duplicating it), *Running locally*
(backend + frontend + a brief tests/CI line), *Observability* (the Langfuse + structlog + Sentry
three-layer story), and *Production considerations* (pointing here, framing the v2 deferrals as
deliberate). Two deviations from the prep below: the **live demo link was dropped** (the recorded
screenshots carry the proof instead), and **static screenshots replaced the GIF** (`docs/prompt.png`,
`docs/report.png`). **The stale `frontend/README.md` was deleted** (was untouched `create-next-app`
boilerplate referencing the Geist font; the app uses Space Grotesk). Original prep notes retained
below for reference.

**Write a real root `README.md`** (currently a 2-line stub). For a portfolio/showcase
project this is the single most-read file — it carries the "production-grade considerations"
story more than any individual code fix. It should also link to this doc, since a curated
list of known gaps and deliberate deferrals is itself a senior signal. Suggested structure:

  - **One-line pitch + live demo link.** What Lens does, in a sentence, and a clickable URL
    to the deployed app (a live demo outweighs almost any code detail to a reviewer).
  - **Screenshot or short GIF** of the prompt → report → follow-up flow.
  - **What it does** — natural-language research queries → AI report combining web research
    (Tavily) with YouTube-comment sentiment (VADER); up to 5 follow-ups per report; per-user
    history.
  - **Architecture diagram** — frontend (Next.js/Vercel) ↔ backend (FastAPI/Render, Docker)
    ↔ Postgres (Neon), with Clerk for auth and Langfuse for tracing.
  - **The agent design** (the centerpiece — give it real space): the
    `planner → researcher → coverage_check → [re-plan loop | sentiment? | synthesizer]`
    graph, LLM-driven structured-output decisions, and the hard caps (3 iterations / 5
    Tavily calls) as cost backstops. A small graph diagram here earns its keep.
  - **Tech stack** — concise table (can lift from `app_summary.md`, don't duplicate it
    wholesale — link instead).
  - **Running locally** — backend (`.venv`, `.env` from `.env.example`, `alembic upgrade
    head`, `uvicorn`) and frontend (`nvm use 20`, `.env.local`, `npm run dev`); note the
    Node 20+ and two-ORM setup.
  - **Production considerations** — a short paragraph pointing to `PRODUCTION_READINESS.md`,
    framing the deferred items as deliberate scope calls, not oversights.
  - **What's intentionally deferred (v2)** — pgvector follow-up retrieval, Redis caching,
    per-step progress reporting; shows judgment about scope.

  Also replace or remove the stale `frontend/README.md` (untouched `create-next-app`
  boilerplate — references the Geist font; the app uses Space Grotesk). (Undocumented.)

---

## Deferred — v2 / real-production (NOT required for portfolio)

These are genuine production concerns but reviewers won't expect them from a side project.
Documenting them as deliberate deferrals is the right move — and is itself a maturity signal.

**Durable task queue** (`backend/app/routers/queries.py`, line 35). In-process
`BackgroundTasks` die on any Render restart or spin-down, leaving a job stuck in `running`
(the Phase 1 frontend poll cap handles the symptom). Move the job to a durable queue for real
load. **Prefer ARQ over Celery here:** the stack is async top to bottom (FastAPI async, async
SQLAlchemy, `LangGraph.ainvoke`), and ARQ is natively async on Redis, so it runs the graph
without wrapping every task in `asyncio.run(...)`; Celery is sync-first and pairing it with an
all-async app invites the "why Celery?" question. This is also the *real* fix for the Phase 1
blocking-I/O item (it pulls the whole job out of the web process, so the blocking calls never
touch the API event loop) — which is why that item is deferred here rather than patched with
`asyncio.to_thread`. Heavy for a portfolio, and a private/recorded demo rarely hits the
failure. (Durability noted in the docs.)

**Clerk `user.deleted` webhook.** Needs a public HTTPS endpoint (hence post-deploy). No code
path cleans up when a user deletes their Clerk account, so deleted users leave orphaned rows
in `users`, `reports`, and `follow_ups`. Add a `POST /webhooks/clerk` route that verifies the
Svix signature and subscribe to `user.deleted` (and optionally `user.updated` to keep email
in sync). `user.created` is optional — the backend already lazily upserts on first request.
(Already noted in the docs.)

**YouTube quota ceiling** (`backend/app/graph/graph.py`, `sentiment_node`). Each
sentiment-enabled run costs ~105 units (1 search × 100 + ~5 comment lists) against the
10k/day free tier → only ~95 sentiment runs/day *total across all users*, with no caching.
Fine for a demo; a real public launch needs the deferred Redis sentiment cache. Worth knowing
before opening a public demo. (Partly noted.)

**Neon connection pooling** (`backend/app/database.py`, line 6). Serverless Postgres wants
pooled connections under concurrency rather than the default engine settings. (Undocumented.)

Also already scoped to v2 in `app_summary.md`: pgvector + `document_chunks` semantic
follow-up retrieval, Redis caching, and per-step progress reporting (`current_step`).

---

## Maintainability / tech debt (after tests; non-blocking)

Code quality is good and consistent; none of these change runtime behavior. They're recorded
so the debt isn't forgotten. **Sequence them after the Phase 2 test suite exists** — the
highest-value refactor here touches the most logic-dense file, and doing it without tests
risks a regression.

**Extract the Gemini model name to config** (`backend/app/graph/graph.py`, lines 144, 205,
336, 456, plus `call_gemini_followup`). The string `"gemini-3.1-flash-lite-preview"` is
hardcoded in ~5 places, so the model can't be changed per-environment or swapped without
editing every call site. Move it to `Settings` in `config.py`. (Undocumented.)

**Name the re-plan hard caps as constants** (`backend/app/graph/graph.py`, lines 249, 332,
497). The caps (`5` Tavily calls, `3` iterations) are magic literals duplicated across
`researcher_node`, `coverage_check_node`, and `_route_after_coverage`. Changing a cap means
editing 3+ sites consistently or the loop logic silently breaks — extract to module-level
constants. (Undocumented.)

**Add an LLM factory** (`backend/app/graph/graph.py`). A fresh `ChatGoogleGenerativeAI(...)`
is constructed on every node call with the same config copy-pasted ~5×. A small
`_make_llm(structured=...)` helper removes the duplication and centralizes the model-name
change above. (Undocumented.)

**Split `graph.py`** (591 lines). It's cohesive, not tangled, but mixes Pydantic
schemas/state, prompt templates, formatting helpers, the node functions, and the graph
builder + `run_graph` persistence. A clean split is `prompts.py` / `state.py` / `nodes.py`
/ `graph.py`. **Gate this on the test suite existing** — it's the riskiest refactor to do
blind. (Undocumented.)

**Drop the unused `swr` dependency and boilerplate `public/` assets** (`frontend/`). `swr` is
in `package.json` but imported nowhere — all data access is manual `fetch` in `lib/api.ts`;
remove it. Separately, `frontend/public/` still holds the five default `create-next-app` SVGs
(`file.svg`, `globe.svg`, `next.svg`, `vercel.svg`, `window.svg`), referenced nowhere — delete
them (same `create-next-app` leftover theme as the stale `frontend/README.md` in Phase 6).
Both are trivial, zero-risk dead-weight removals. (Undocumented.)

**[partly done] Remove the unused Prisma stack from the frontend** (`frontend/`). **Update:**
option (a) is underway — `lib/prisma.ts` was deleted during the Vercel deploy (it broke the
build by importing the gitignored generated client), and `DATABASE_URL` was never set on Vercel.
**Still to drop for full cleanup:** the `prisma`, `@prisma/client`, `@prisma/adapter-pg`, `pg`,
and `@types/pg` deps in `package.json`, plus `prisma.config.ts`, `prisma/schema.prisma`, and the
gitignored `lib/generated/` tree. Original analysis below. Prisma is configured but
plays *no runtime role*: the client in `lib/prisma.ts` is never imported, the generated types
under `lib/generated/prisma/` are never imported (the frontend's types are hand-defined in
`lib/api.ts`), and there are no Next.js API routes — all data access goes browser →
`lib/api.ts` → FastAPI → Postgres via SQLAlchemy. The docs frame Prisma as a schema *mirror*
(`prisma db pull` → `prisma generate`), but the mirrored types aren't consumed, so even that
benefit is unrealized. Two coherent resolutions: **(a) drop it** — remove the `prisma`,
`@prisma/client`, `@prisma/adapter-pg`, and the now-orphaned `pg` + `@types/pg` deps (the
latter two only back the `PrismaPg` adapter) from `package.json`, delete `lib/prisma.ts`,
`lib/generated/`, `prisma.config.ts`, and `prisma/schema.prisma`, and drop `DATABASE_URL`
from the frontend env (this is the cleaner option and removes the Vercel credential-exposure
flagged in the Phase 3 Vercel item); **or (b) actually use it** — import the generated types
in `lib/api.ts` instead of hand-maintaining parallel `ReportData`/`HistoryItem` interfaces,
buying real schema-drift protection. Either is fine; the status quo (carrying three deps + a
generated-code tree + a leaked DB URL for zero benefit) is the one to avoid. Note this updates
`CLAUDE.md`'s "Two ORMs, one DB" / Prisma v7 gotchas if option (a) is taken. (Undocumented.)

**Drop the dead `suggested_followups` column** (`backend/app/models/models.py`, line 62,
plus a migration). It's never written and was removed from the API; currently retained
intentionally per `CLAUDE.md`. (Partly noted.)

**Dedup `resolveToken`, `Background`, and the motion variants** into shared modules — they
are copy-pasted across all three page components. Note that this conflicts with `CLAUDE.md`
(line 68), which currently instructs copying `resolveToken` to new pages, so update that
guidance if dedup is adopted. (Undocumented.)

**eslint-disable consistency** on the polling effect (`frontend/app/page.tsx`, line 125).
The chat and history pages carry the `exhaustive-deps` disable comment; the prompt page
doesn't, so it will emit a hooks-deps warning the others suppress. (Undocumented.)

**Return 404 (not 403) when a valid resource isn't yours** (`backend/app/routers/`:
`jobs.py:27`, `reports.py:33,99`, `history.py:54`). Ownership failures currently return 403,
which confirms the ID exists — a minor IDOR/existence leak. Very low severity: IDs are random
UUIDv4s (unguessable), so nothing is enumerable and no content leaks; the fix is trivial
(return 404 to mask existence). Do it opportunistically if already touching those handlers, or
leave it as a deliberate, documented trade-off. (Undocumented.)

**Dedup the docs to prevent drift** (`CLAUDE.md` and `app_summary.md`). The same facts are
stated at length in both files — the build order (`CLAUDE.md` lines 33–53 ↔ `app_summary.md`
lines 233–253, near-verbatim) and the LangGraph re-plan loop description (`CLAUDE.md`
"Current state" ↔ `app_summary.md` lines 149–178). Duplicated facts drift: the 403→429
follow-up-cap mismatch fixed in this pass existed because the cap was stated in four places.
Make `app_summary.md` the canonical spec/architecture/build-order source and have
`CLAUDE.md` point to it instead of restating — but **keep `CLAUDE.md`'s "Key gotchas"
intact** (the Next.js 16 / Clerk / Alembic quirks aren't discoverable from code, which is
the whole point of that file). This is doc hygiene, not size reduction. (Undocumented.)

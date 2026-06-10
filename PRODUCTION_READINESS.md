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

**`CLERK_SECRET_KEY` is documented as required but the backend never uses it**
(`backend/.env.example`, `CLAUDE.md`; absent from `backend/app/config.py`). `config.py`
doesn't declare it and `extra="ignore"` silently drops it, so no code reads it — reinforcing
the auth-bypass item above (the backend has no binding to your actual Clerk instance). Either
wire it into verification or remove it from the example so it doesn't imply security that
isn't there. (Undocumented.)

**Concurrent first-request user creation can 500** (`backend/app/auth.py`, lines 60–68).
The lazy upsert does `select` → if missing, `insert` + `commit`. Two near-simultaneous
first requests from a brand-new user both see "no user" and both insert, violating the
`clerk_user_id` unique constraint → unhandled `IntegrityError` → 500. Use Postgres
`INSERT ... ON CONFLICT DO NOTHING` (or catch the integrity error and re-select). A
`user.created` webhook would also sidestep it, but the lazy path needs to be safe on its
own. (Undocumented.)

**Frontend polls forever when a job is `done` but has no report** (`frontend/app/page.tsx`,
lines 110–117). `GET /jobs/{id}/status` can legitimately return `status: "done"` with
`report_id: null` (`jobs.py:41`). The poll only redirects on `done && report_id` and only
errors on `failed`, so that case falls through to the `else` branch and re-polls
indefinitely with no end state. Handle `done && !report_id` as an error. (Undocumented.)

**Frontend poll cap for stuck `running` jobs** (`frontend/app/page.tsx`, line 104). If a job
never completes (e.g. a Render restart/spin-down mid-run kills the in-process task), the
prompt screen polls forever with an eternal spinner. Add a cap (N ticks or a time budget)
that surfaces an error state. This handles the *symptom*; the root-cause fix (a durable task
queue) is a deferred real-production item — see **Deferred**. (Undocumented.)

**No timeout on the Gemini/LLM calls** (`backend/app/graph/graph.py`, line 204 and the 4
other `ChatGoogleGenerativeAI(...)` sites). No `timeout`/`request_timeout` is set, so a hung
Gemini request leaves the job stuck in `running` forever (which the poll cap above then
surfaces). Set a request timeout so a stalled call fails fast instead. (Undocumented.)

**Treat empty synthesizer output as a failure** (`backend/app/graph/graph.py`, line 557).
The job is marked `done` even when `report_markdown` is empty with no error set,
persisting a blank report with no failure signal. (Undocumented.)

**Blocking synchronous I/O on the async event loop** (`backend/app/graph/graph.py`, lines
255, 378). `researcher_node` calls `TavilyClient.search()` and `sentiment_node` calls the
googleapiclient `.execute()` — both synchronous, blocking network calls — inside `async def`
nodes run via `_graph.ainvoke`. Because `run_graph` runs in `BackgroundTasks` (the *same*
event loop as the web server), every Tavily/YouTube call freezes all other incoming requests
for its full latency; with up to 5 Tavily calls + YouTube fetches per run, the API stalls in
multi-second chunks under concurrency. This is live today, before any scaling. Fix: wrap the
calls in `asyncio.to_thread(...)` or use `AsyncTavilyClient`. (A worker-process task queue
would also resolve it — see Deferred.) Good async-competence signal to fix. (Undocumented.)

**Add a unique constraint on `reports.job_id`** (`backend/app/models/models.py` line 51,
plus a migration). The model declares a one-to-one (`uselist=False`) and `jobs.py` uses
`scalar_one_or_none()` on reports-by-job (line 37–40), but nothing enforces one report per
job at the DB level — a retry/bug producing two rows makes that query raise
`MultipleResultsFound` → 500. Cheaper to add before production data exists. (Undocumented.)

**Follow-up race / unique constraint** (`backend/app/routers/reports.py`, line 102, plus
a new migration). The count → cap-check → insert sequence isn't atomic and has a slow
Gemini call in the middle, so concurrent follow-ups for the same report can both pass the
5-cap and insert a duplicate `turn_number` (which the frontend uses as a React key). Add a
unique constraint on `(report_id, turn_number)` — much cheaper before production data
exists — and catch the resulting `IntegrityError` to return a clean 409. (Undocumented.)

*Quick wins to fold in here (small, prevent code-review embarrassment):*

- **Coerce `response.content` to `str`** (`backend/app/graph/graph.py`, lines 165 and 478).
  LangChain's `content` can be a string or a list; the return type and the DB write assume
  it's always a string. (Undocumented.)
- **`HTTPBearer()` returns 403, not 401, on a missing token** (`backend/app/auth.py`, line
  13). `auto_error=True` (the default) raises 403 when the `Authorization` header is absent,
  but the frontend's "Session expired, sign in again" messaging implies 401. Use
  `HTTPBearer(auto_error=False)` + an explicit 401. (Undocumented.)

---

## Phase 2 — Tests (~1 day)

*Goal: make the "tests" claim true; unblocks CI (Phase 4) and the Maintainability refactors.*

**Test suite** (none exists today; `pytest` + `pytest-asyncio` are already in
`requirements.txt`). Don't chase a coverage %; build a credible, representative suite:

- **Graph-node tests** with the LLM / Tavily / YouTube **mocked** — the re-plan loop logic,
  the hard caps (3 iterations / 5 Tavily calls), and the planner/coverage fail-safe fallbacks.
- **Auth-dependency test** including a **forged-token rejection** — this doubles as proof the
  Phase 1 auth-bypass fix works.
- **Endpoint tests** — ownership 403 / not-found 404, and the follow-up cap 429.
- A **rate-limit test** (429 past the cap) is added once `slowapi` lands in Phase 3.

**Done when:** the suite runs green locally. (Already noted in the docs as a gap.)

---

## Phase 3 — Containerize, secure & deploy (~1.5 days)

*Goal: make "Docker + deployment" true, get a live URL, and never expose uncapped paid APIs.*

**Rate limiting / per-user quota** (`backend/app/routers/queries.py`, line 19; also
`POST /reports/{id}/followup`). Every query fires Tavily, Gemini, and YouTube calls, all
metered or paid. With no cap, a single signed-in user (or a leaked token) can drain your
quotas and run up cost in minutes. Add `slowapi` to `requirements.txt` and apply per-user
limits keyed on the Clerk `sub` from the auth dependency (not IP), returning a clean 429.
**Do this before the deploy goes live**, not after. (Already noted in the docs.)

**Add a `.dockerignore`** in `backend/`. The Dockerfile's `COPY . .` currently bakes
`.env`, `.venv/`, and `__pycache__` into image layers — a secret-leak risk and needless
bloat. (Undocumented.)

**Drop unused heavyweight dependencies** (`backend/requirements.txt`). `chromadb==0.5.18`,
`nltk==3.9.1`, and `python-dotenv==1.0.1` are imported nowhere (confirmed by grep).
`chromadb` is the costly one — it drags in `onnxruntime` and adds hundreds of MB to the
image and to every build; `nltk` is redundant (`vaderSentiment` is standalone) and
`python-dotenv` is unused (pydantic-settings reads `.env` itself). `chromadb` likely landed
in anticipation of the v2 vector work that became pgvector. Same "image hygiene before first
deploy" theme as `.dockerignore`. (Undocumented.)

**Deploy plumbing** (`backend/app/main.py`, line 18; release step). Set the production
`FRONTEND_URL`/CORS origin instead of `localhost:3000`, and run `alembic upgrade head` as
part of the backend release so the schema exists. The frontend is on Vercel and auto-deploys
from git, so there is no frontend Dockerfile — only the backend is containerized.
(Partly noted as "step 17 deploy.")

**Wire `/health` as Render's health check** (`backend/app/main.py`, line 30). The endpoint
already exists; just configure Render to use it. (Undocumented.)

**Flip Tavily `search_depth` from `"basic"` to `"advanced"`** before shipping
(`backend/app/graph/graph.py`, line 257). This is both a quality and a cost knob; decide
deliberately. (Already noted in the docs.)

**Verify the `next build` type error** on the nullable `result`
(`frontend/app/page.tsx`, line 135). With `strict: true`, `result` may be dereferenced
while still possibly-null across the try/catch; if real, this blocks the Vercel build.
Turbopack dev mode skips typechecking, so it may be hidden right now — confirm with a build
before relying on the deploy. (Undocumented.)

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

**Create & deploy the Render backend service — the actual deploy, not just prep.** Everything
above readies the image/config; this is the step that produces a live backend URL, and it
gates Phase 4's auto-deploy-on-merge. Concretely: (1) create a Render **Web Service** from the
repo with `backend/` as the root and the existing `backend/Dockerfile` as the runtime; (2)
**bind to Render's `$PORT`** — the Dockerfile currently hardcodes `--port 8000`
(`backend/Dockerfile`, line 11), but Render injects `$PORT` and routes to it, so change the
CMD to `--port ${PORT:-8000}` (or set Render's port to 8000) or the service won't receive
traffic; (3) set every backend env var from `backend/.env.example` in the Render dashboard
(`DATABASE_URL`, `GEMINI_API_KEY`, `TAVILY_API_KEY`, `YOUTUBE_API_KEY`, the `LANGFUSE_*`
keys, and `FRONTEND_URL` set to the Vercel URL once known — they reference each other, so
expect to circle back); (4) set the **health check path to `/health`** (the wiring item
above); (5) add the `alembic upgrade head` release/pre-deploy command (the deploy-plumbing
item above); (6) trigger the first deploy and confirm `GET /health` returns `200` on the live
URL. **Sequence this after the `.dockerignore`, dropped-deps, and deploy-plumbing items** —
they all have to be true *in the image you ship here.* (Undocumented — the prep was listed but
the deploy action itself was missing.)

**Set up the Vercel frontend project + first deploy.** The frontend has no Dockerfile (Vercel
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

---

## Phase 5 — Observability (~half a day)

*Goal: complete the three-layer story — logs + tracing + alerting. Most impressive once the
app is deployed and producing real logs, which is why it follows Phase 3.*

**Structured logging** (whole backend — no logging exists today; `print`-free but also
log-free). A strong portfolio signal for the "production-grade observability" story, so do it
properly rather than scattering `print`s. Concretely:

  - **Pick a logger.** `structlog` emitting JSON (or stdlib `logging` with a JSON formatter)
    configured once at startup in `main.py`. JSON logs are greppable/queryable in Render's
    log stream and any future aggregator.
  - **Thread a correlation ID.** Generate a `job_id`-scoped (and per-request) id and bind it
    so every line for one research run is filterable end to end — planner → researcher →
    coverage → sentiment → synthesizer → DB write.
  - **Log at node boundaries.** Each graph node should log entry/exit with inputs, key
    outputs, and latency (e.g. sub-query count, `tavily_call_count`, `iteration_count`,
    sources found, sentiment volume, coverage decision). This makes the re-plan loop's
    behavior visible without opening Langfuse.
  - **Stop swallowing errors silently** (`backend/app/graph/graph.py`, lines 266, 413, 447,
    and the planner/coverage fallbacks). The bare `except Exception: pass` blocks mean a bad
    API key or a Tavily/YouTube outage produces a silent empty report with no signal —
    `log.warning(...)`/`log.exception(...)` at minimum so the failure is visible while the
    node still fails soft.
  - **Log the auth and request-lifecycle events** — token rejections (meaningful once the
    Phase 1 auth fix lands), job created/started/done/failed transitions, follow-up cap hits.
  - **Set log levels deliberately** — INFO for lifecycle/node boundaries, WARNING for
    fail-soft degradations, ERROR/exception for the synthesizer failure and DB rollback
    paths in `run_graph`.

  Note this **complements** the already-live Langfuse tracing rather than duplicating it:
  Langfuse captures LLM-call traces (prompts, outputs, per-node latency); structured logs
  cover the non-LLM operational surface — DB writes, auth, Tavily/YouTube failures, and the
  request lifecycle — and are what you'll actually grep in Render when something breaks.
  (Logging noted only narrowly in the docs; the broader story is undocumented.)

**Sentry for error alerting** (`backend/app/main.py` startup + `run_graph`). A ~two-line
`sentry-sdk` init gives real error capture and alerting on top of the logs above — logs tell
you what happened after you go looking; Sentry tells you *that* something broke without you
looking. Wire it once logging exists. (Already noted in the docs.)

---

## Phase 6 — README & storefront (~half a day)

*Goal: the most-read file tells the story.*

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
(the Phase 1 frontend poll cap handles the symptom). Move the job to a durable queue — ARQ
(async, fits the codebase) or Celery — for real load. Heavy for a portfolio, and a
private/recorded demo rarely hits the failure. (Durability noted in the docs.)

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

**Remove the unused Prisma stack from the frontend** (`frontend/`). Prisma is configured but
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

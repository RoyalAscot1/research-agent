# Lens — Claude Working Guide

## What this is
Lens is a full-stack AI research and sentiment app. Users submit natural language queries and receive AI-generated reports combining web research with YouTube comment sentiment data. See `app_summary.md` for the full product spec.

## Repo structure
```
backend/   FastAPI + LangGraph (Python)
frontend/  Next.js 16 (TypeScript)
```

## Current state
- Backend: FastAPI with Clerk JWT auth wired up; `POST /queries` creates a `research_jobs` row and fires a LangGraph background task; all report/history endpoints live — `GET /jobs/{job_id}/status` returns `report_id` when done, `GET /reports/{report_id}` returns full report + follow_ups, `GET /history` returns paginated list, `DELETE /history/{report_id}` and `DELETE /history` both implemented. All endpoints auth guarded.
- Follow-up endpoint live (`POST /reports/{report_id}/followup`): auth guarded, 5-follow-up cap (429 on breach), loads `raw_context` + prior Q&A turns, calls Gemini via `call_gemini_followup()` in `graph.py`, persists `FollowUp` row. Both `/reports/{id}` and `/followup` return 404 (not 500) for malformed or non-existent UUIDs.
- Frontend: Next.js 16, Tailwind, shadcn/ui, Prisma v7, framer-motion. All three screens live: `/` (prompt), `/history`, `/chat/[id]`.
- Prompt screen (`app/page.tsx`): Client Component with four states — loading, sign-in gate, researching (polling), idle/error. Submits query via `POST /queries`, polls `GET /jobs/{job_id}/status` every 2s, redirects to `/chat/{report_id}` on completion. Clerk token fetched fresh each poll tick via `getToken()`. Floating History + Sign Out buttons top-right when signed in.
- Chat screen (`app/chat/[id]/page.tsx`): Client Component. Uses `React.use(params)` to unwrap the route param. Three render states: loading, error, loaded. Fetches `GET /reports/{id}` on mount using `resolveToken`. Layout: transparent sticky nav (Lens wordmark + History link left, copy/download buttons + completion time right); query title; three sentiment tiles (conditionally hidden if sentiment is null); markdown report via `react-markdown` + `remark-gfm` with custom dark-themed component overrides; source cards linking to Tavily URLs; follow-up Q&A thread + chat input below the report. All sections are conditional — degrades gracefully with no sentiment or no sources. Follow-up state: `followUps` seeded from `report.follow_ups` on load, `question`, `submitting`, `limitReached` (set on load if already at 5, after 5th successful submit, or on 429), `followUpError` (shown on non-429 errors, cleared on next submit). Optimistic UI — question bubble appears immediately, spinner while answer is in flight, answer fades in via `AnimatePresence mode="wait"`. `suggested_followups` field removed from API response and `ReportData` type (DB column kept, always null, never populated).
- History screen (`app/history/page.tsx`): Client Component. Three render states: loading, error, loaded. Fetches `GET /history` on mount. Lists reports as glassmorphism cards — query text, colour-coded sentiment badge, date. Click row navigates to `/chat/{report_id}`. Hover-revealed per-row delete (trash icon) with confirm-free deletion. "Clear all" button with `window.confirm` guard. Empty state with prompt to start a query. Transparent sticky nav (Lens wordmark left, Sign Out right).
- API client (`lib/api.ts`): all methods take `token: string` as first arg. `ReportData`, `ReportSource`, and `HistoryItem` types exported. `apiFetch` handles 204 No Content responses (skips `res.json()`) — required for DELETE endpoints.
- Design: dark-first (Space Grotesk font, deep navy background, violet accent). Animated gradient mesh (three drifting blobs via CSS keyframes) + SVG grain texture. Glassmorphism card with animated violet border glow on focus. Gradient wordmark. Framer Motion staggered entrance + AnimatePresence state transitions + spring-physics buttons. Typewriter placeholder.
- Database: Neon Postgres live — 5 tables (`users`, `research_jobs`, `reports`, `follow_ups`, `alembic_version`). Alembic owns all migrations; Prisma mirrors via `db pull`.
- Auth: Clerk (`@clerk/nextjs`) — `middleware.ts` and `ClerkProvider` in layout wired up, Google sign-in working, DB cleaned up (NextAuth tables dropped, `clerk_user_id` on `users`). Backend verifies Clerk JWTs and upserts users on first request (`app/auth.py`).
- LangGraph: three-node graph live (`app/graph/graph.py`) — `tavily_node` fetches web results (basic search depth, 8 results), `sentiment_node` fetches top YouTube comments and scores them with VADER (positive/neutral/negative), `gemini_node` synthesises everything into a markdown report with `[Source N]` citations and a Public Sentiment section. Sources, sentiment scores, comment volume, and overall_sentiment persisted to the `reports` row. `run_graph` runs as a FastAPI `BackgroundTasks` task — pending → running → done/failed. `call_gemini_followup()` is a standalone exported async function used by the follow-up endpoint — takes query, report_markdown, sources, prior_turns, and question; returns a markdown answer string.
- `GET /reports/{report_id}` returns `query` (from job), `sources` (array from `raw_context`), and `completed_in_seconds` in addition to existing fields.
- Tavily `search_depth` is temporarily `"basic"` (1 credit/search) to conserve credits during development — switch to `"advanced"` before shipping.
- YouTube API key required (`YOUTUBE_API_KEY` in `backend/.env`) — enable YouTube Data API v3 in Google Cloud Console. Quota: 10k units/day free (search = 100 units, comment list = 1 unit/page).
- Next step: Configure LangSmith (step 14) — set up tracing before adding agentic nodes.
- **Current graph is a pipeline, not an agent** — `tavily → sentiment → gemini → END`. No conditional edges, no LLM decision-making, no loops. The Planner and Researcher nodes that make it truly agentic will be added in steps 14–15.

## Build order
1. Postgres schema + Alembic migrations (done)
2. FastAPI skeleton (done)
3. Clerk — auth flow end to end (done)
4. LangGraph graph — single node (Gemini only) (done)
5. Add Tavily researcher node (done)
6. Add YouTube comments + VADER sentiment node (done)
7. Implement real FastAPI report + history endpoints (stubs → real DB reads, add auth guards) (done)
8. Next.js frontend — prompt screen + progress polling (done)
9. Next.js frontend — chat screen (report card display, no follow-ups yet) (done)
10. Synthesizer prompt iteration — skipped for now, output quality acceptable
11. Next.js frontend — history screen (done)
12. Follow-up endpoint (done)
13. Next.js frontend — follow-up chat UI (done)
14. Configure LangSmith — tracing before agentic nodes
15. Add Planner node — LLM decides query decomposition + whether to run sentiment (introduces true agentic behaviour)
16. Add Researcher node + pgvector — `document_chunks` table, re-plan loop (max 3 iterations, 5 Tavily calls hard cap)
17. Docker + GitHub Actions + Render deploy
18. Redis caching (post-v1)

## Key gotchas

### Next.js 16
- `params` in dynamic routes is a `Promise` — use `await params` in Server Components, `React.use(params)` in Client Components
- Turbopack is on by default for both `next dev` and `next build`
- Node.js 20+ required (`nvm use 20` before running frontend commands)

### Clerk
- Auth is handled entirely by Clerk — no NextAuth, no `@auth/prisma-adapter`
- Keys: `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` and `CLERK_SECRET_KEY` in `frontend/.env.local`; `CLERK_SECRET_KEY` also in `backend/.env`
- `middleware.ts` uses `clerkMiddleware` + `createRouteMatcher` to protect `/history` and `/chat`
- `<ClerkProvider>` wraps the root layout
- **Backend JWT verification**: `app/auth.py` — `get_current_user` dependency verifies the Bearer token via Clerk's JWKS endpoint (RS256), then upserts the user into Postgres. Use as a dependency on any protected endpoint.
- **User upsert**: happens lazily on first API request — no webhook needed for local dev. A `user.created` webhook (step 14) will complement this in production.
- **`getToken()` race condition**: On first page load, `getToken()` can return `null` even when `isSignedIn` is true — the session token hasn't been cached yet. Always use the `resolveToken` helper in `app/page.tsx` (tries once, waits 350ms, retries) rather than calling `getToken()` directly. Copy this pattern to any future page that makes authenticated API calls.
- **User deletion**: must be handled via a `user.deleted` Clerk webhook in step 14 — no code for this yet.
- **`users` table column names**: Prisma created the table with camelCase columns (`createdAt`, etc.). The SQLAlchemy `User` model maps these explicitly (e.g. `Column("createdAt", ...)`). Do not rename them without an Alembic migration.

### Prisma v7
- Connection URL lives in `prisma.config.ts`, NOT in `prisma/schema.prisma`
- Direct DB connections require `@prisma/adapter-pg` — see `frontend/lib/prisma.ts`
- Generated client is at `lib/generated/prisma/client`, not `@prisma/client`
- After schema changes: `prisma generate` to regenerate the client

### Alembic
- Alembic owns ALL migrations — Prisma must not manage schema changes
- `users` table is excluded from autogenerate (owned by Clerk/Prisma) — see `include_object` in `backend/alembic/env.py`
- Load `.env` before running Alembic: `export $(grep -v '^#' .env | xargs) && alembic <command>`
- Neon gives a `postgresql://` URL — change to `postgresql+asyncpg://` and replace `sslmode=require` with `ssl=require` for backend use

### Backend
- Run from `backend/` with venv active: `source .venv/bin/activate`
- Start server: `uvicorn app.main:app --reload`
- Requires `backend/.env` — copy from `.env.example` and fill in values

### LangGraph
- Graph lives in `backend/app/graph/graph.py` — `run_graph` is the entry point called by `BackgroundTasks`
- Uses `gemini-3.1-flash-lite-preview` (not 1.5-flash — that model is unavailable on the current API key)
- `run_graph` opens its own `AsyncSessionLocal` session — it runs outside any request context so it cannot use the request-scoped `get_db` dependency
- `tavily_node`, `sentiment_node`, and `gemini_node` catch exceptions internally. `tavily_node` and `sentiment_node` failures are non-fatal — the graph continues with empty sources/sentiment. `gemini_node` failures set `state["error"]` which flips the job to `failed`.
- Outer `run_graph` wraps the full body in try/except to flip status to `failed` on DB errors after `status = "running"`.


### Two ORMs, one DB
- SQLAlchemy (Python) and Prisma (TypeScript) both point at the same Postgres database
- Alembic runs all migrations; Prisma uses `prisma db pull` to mirror changes

## Environment variables
- Backend: `DATABASE_URL`, `GEMINI_API_KEY`, `TAVILY_API_KEY`, `YOUTUBE_API_KEY`, `CLERK_SECRET_KEY` — see `backend/.env.example`
- Frontend: `DATABASE_URL`, `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY` — see `frontend/.env.local.example`

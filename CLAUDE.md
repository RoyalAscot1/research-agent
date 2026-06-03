# Lens — Claude Working Guide

## What this is
Lens is a full-stack AI research and sentiment app. Users submit natural language queries and receive AI-generated reports combining web research with Reddit/Google Trends sentiment data. See `app_summary.md` for the full product spec.

## Repo structure
```
backend/   FastAPI + LangGraph (Python)
frontend/  Next.js 16 (TypeScript)
```

## Current state
- Backend: FastAPI with Clerk JWT auth wired up; `POST /queries` creates a `research_jobs` row and fires a LangGraph background task; `GET /jobs/{job_id}/status` reads live status from DB; report/history endpoints still stub
- Frontend: Next.js 16, Tailwind, shadcn/ui, Prisma v7, stub pages for `/`, `/history`, `/chat/[id]`
- Database: Neon Postgres live — 5 tables (`users`, `research_jobs`, `reports`, `follow_ups`, `alembic_version`). Alembic owns all migrations; Prisma mirrors via `db pull`.
- Auth: Clerk (`@clerk/nextjs`) — `middleware.ts` and `ClerkProvider` in layout wired up, Google sign-in working, DB cleaned up (NextAuth tables dropped, `clerk_user_id` on `users`). Backend verifies Clerk JWTs and upserts users on first request (`app/auth.py`).
- LangGraph: single Gemini node live (`app/graph/graph.py`). `run_graph` runs as a FastAPI `BackgroundTasks` task — pending → running → done/failed with a `reports` row written on success.
- Next step: Add Tavily researcher node (step 5)

## Build order
1. Postgres schema + Alembic migrations (done)
2. FastAPI skeleton (done)
3. Clerk — auth flow end to end (done)
4. LangGraph graph — single node (Gemini only) (done)
5. Add Tavily researcher node
6. Add PRAW + VADER + pytrends sentiment node (combined)
7. Synthesizer prompt — iterate on real queries
8. Next.js frontend — prompt screen + progress polling
9. Next.js frontend — chat screen (report card + follow-up chat)
10. Follow-up endpoint
11. Next.js frontend — history screen
12. Docker + GitHub Actions + Render deploy
13. Chroma integration (post-v1)
14. Redis caching (post-v1)

## Key gotchas

### Next.js 16
- `params` in dynamic routes is a `Promise` — always `await params` before destructuring
- Turbopack is on by default for both `next dev` and `next build`
- Node.js 20+ required (`nvm use 20` before running frontend commands)

### Clerk
- Auth is handled entirely by Clerk — no NextAuth, no `@auth/prisma-adapter`
- Keys: `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` and `CLERK_SECRET_KEY` in `frontend/.env.local`; `CLERK_SECRET_KEY` also in `backend/.env`
- `middleware.ts` uses `clerkMiddleware` + `createRouteMatcher` to protect `/history` and `/chat`
- `<ClerkProvider>` wraps the root layout
- **Backend JWT verification**: `app/auth.py` — `get_current_user` dependency verifies the Bearer token via Clerk's JWKS endpoint (RS256), then upserts the user into Postgres. Use as a dependency on any protected endpoint.
- **User upsert**: happens lazily on first API request — no webhook needed for local dev. A `user.created` webhook (step 14) will complement this in production.
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
- Error handling: `gemini_node` catches exceptions internally and sets `state["error"]`. Outer `run_graph` should also wrap the whole body in try/except to flip status to `failed` if a DB error occurs after `status = "running"` (not yet done — known gap)
- `GET /jobs/{job_id}/status` has no auth guard yet — add `get_current_user` before step 11

### Two ORMs, one DB
- SQLAlchemy (Python) and Prisma (TypeScript) both point at the same Postgres database
- Alembic runs all migrations; Prisma uses `prisma db pull` to mirror changes

## Environment variables
- Backend: `DATABASE_URL`, `GEMINI_API_KEY`, `TAVILY_API_KEY`, `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `CLERK_SECRET_KEY` — see `backend/.env.example`
- Frontend: `DATABASE_URL`, `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY` — see `frontend/.env.local.example`

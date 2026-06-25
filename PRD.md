# JellieRAG — Technical Documentation & PRD

> **Status:** Pivot to local homelab deployment — OpenSpec change `jellirag-local-pivot` supersedes `jellirag-mvp`.
> 
> **IMPORTANT ARCHITECTURE PIVOT:** This document reflects the original VPS+Cloudflare architecture. The project has pivoted to a fully local homelab deployment (LXC + Tailscale serve + Ollama-on-MacBook) with zero Cloudflare dependencies. See the [jellirag-local-pivot proposal](openspec/changes/jellirag-local-pivot/proposal.md) for the current architecture, or refer to the updated [README.md](README.md) for deployment guidance.
>
> This document is preserved as historical context for the original design decisions.

> **Original Status:** MVP design — OpenSpec change `jellirag-mvp` (proposal → design → specs → tasks all complete, now superseded).
> The original canonical product + technical reference reflected all reviewed decisions: broker-Worker credential boundary, **fused `/prepare-rag` two-call hot path**, conversational RAG with context budgeting, incremental sync (two-way set difference), D1 chunk store, **FR-3 deep links re-included via fail-closed Tailscale addresses**, **session-inactivity pruning**, and **app-owned multi-user auth with RBAC** (≤ ~3 family users; admin/member roles; per-owner private history; Cloudflare Access dropped in favor of FastAPI-issued JWTs).

---

## Table of Contents
1. [Executive Summary](#1-executive-summary)
2. [Product Requirements (PRD)](#2-product-requirements-prd)
3. [System Architecture](#3-system-architecture)
4. [Technology Stack](#4-technology-stack)
5. [Process Flows](#5-process-flows)
6. [Data Model](#6-data-model)
7. [Security Model](#7-security-model)
8. [Performance & Constraints](#8-performance--constraints)
9. [Deployment & Operations](#9-deployment--operations)
10. [Out of Scope / Deferred](#10-out-of-scope--deferred)

---

## 1. Executive Summary

JellieRAG is a self-hosted, privacy-conscious Retrieval-Augmented Generation (RAG) conversational agent. It ingests media metadata from a local **Jellyfin** instance, embeds it into a **Cloudflare Vectorize** semantic index, and answers natural-language questions about the user's library through a streaming chat interface.

**Core value:** enterprise-grade AI (Cloudflare Workers AI) on constrained consumer homelab hardware, with the homelab never exposed to the public internet.

**Key architectural principles:**
- *No Cloudflare credential ever resides on the VPS.* A thin credentials-broker Worker is the only component that touches Cloudflare. The VPS holds only the Jellyfin API key and a single rotatable broker secret.
- *Reads fuse on the edge; decisions stay in Python.* The chat hot path is two broker calls (`/prepare-rag` + `/llm-stream`), not five.
- *Deep links fail closed.* Click-to-play works on the Tailnet and is inert off it, so FR-3 and isolation (NFR-2) coexist.

---

## 2. Product Requirements (PRD)

### 2.1 Personas
- **Homelab Enthusiast (admin)** — asks mood/cast/genre questions about their media library via a fluid chat UI; also owns operational actions (sync trigger, session pruning, family account provisioning).
- **Family Member (member)** — a trusted ≤ ~3-user set (partner/kids) who can chat and manage their own private conversation history but cannot trigger sync/prune or manage accounts.
- **Remote Streamer** — accesses the catalog companion from outside the home network without exposing infrastructure (on the Tailnet).

### 2.2 Functional Requirements

| ID | Requirement | MVP Status |
|----|-------------|-----------|
| FR-1 | Automated library synchronization: extract Jellyfin metadata, chunk, embed, sync to vector index. Cron-scheduled + manual trigger. **Incremental** (two-way set difference). | ✅ In scope |
| FR-2 | Semantic search + **multi-turn** conversational streaming chat; cite source `jf_id`s. | ✅ In scope |
| FR-3 | Click-to-play deep links to Jellyfin web client — rooted at **Tailscale/MagicDNS** base, **fail-closed** off-network. | ✅ In scope (resolved) |

### 2.3 Non-Functional Requirements

| ID | Requirement |
|----|-------------|
| NFR-1 | TTFT < 800ms (⚠ monitored — see §8; risk reduced by fused reads). Ingestion < 1GB RAM for 5,000 items. |
| NFR-2 | No public homelab ports; VPS↔homelab over Tailscale WireGuard. Auth perimeter on public surface. |
| NFR-3 | No Cloudflare credentials on the VPS (credential-isolation boundary). |

---

## 3. System Architecture

### 3.1 Topology

```
                          ┌──────── End User (browser) ────────┐
                          │  Astro SSR shell + Vue 3 chat island │
                          │  (login page → JWT in HttpOnly cookie)│
                          └─────────────────┬───────────────────┘
                                            │ HTTPS (TLS via Traefik)
                                            ▼
    ┌──────────────────────── CLOUDFLARE EDGE ────────────────────────┐
    │  (Cloudflare Access is NOT used for MVP — app-owned JWT auth     │
    │   is the perimeter; CF Access may be re-layered later w/o code)  │
    │        │                                                         │
    │        ▼                                                         │
    │  ┌──────────────────────────────────────────────────────────┐   │
    │  │  broker Worker  (credentials boundary — ONLY CF creds)      │   │
    │  │   bindings: AI · INDEX(Vectorize) · DB(D1)                │   │
    │  │   secret:  BROKER_SECRET                                   │   │
    │  │   surface: /prepare-rag (fused) /search /embed /chunks    │   │
    │  │             /llm-stream /history/* /ingest/* /sync/state  │   │
    │  │             /sessions/prune /users/*                       │   │
    │  └───────▲────────────────▲────────────────▲─────────────────┘   │
    │          │                │                │  (in-process)       │
    │     [Vectorize]       [D1 chunks]     [D1 users/messages/sessions/sync]│
    └──────────┼────────────────┼────────────────┼─────────────────────┘
               │                │                │
               │  authenticated domain calls (X-Broker-Secret)
               │                │                │
    ┌──────────┼────────────────┼────────────────┼─────────────────────┐
    │  PUBLIC VPS  (Dokploy → Traefik :443)   │                        │
    │   ├──► Astro SSR container        :3000 │  static shell + login   │
    │   └──► FastAPI (RAG + ingestion   :8000 │  ALL decisions here     │
    │           + auth/RBAC)                   │                         │
    │           holds ONLY: Jellyfin key + BROKER_SECRET                  │
    │           (+ JELLYFIN_DEEPLINK_BASE, SESSION_TTL_DAYS,              │
    │            JWT_SECRET, JWT_TTL_DAYS, BOOTSTRAP_ADMIN_*)             │
    └──────────┼───────────────────────────────────────────────────────┘
               │ Tailscale (100.x.y.z / MagicDNS, WireGuard)
               ▼
          ┌──────────────────┐
          │  Jellyfin (home) │   no public ports
          └──────────────────┘
```

### 3.2 Responsibility split

```
┌─────────────────────┬──────────────────────────────────────────────────┐
│ broker (Worker)     │ Holds CF creds/bindings. Batches READS (incl.     │
│                     │ fused /prepare-rag). Validates input + secret.    │
│                     │ NO budget/policy decisions; NO password hashing/  │
│                     │ role interpretation (stores/returns pw_hash only).│
├─────────────────────┼──────────────────────────────────────────────────┤
│ backend — FastAPI   │ RAG DECISIONS + AUTH: budget reconciliation,      │
│ (VPS)               │ message assembly, deep-link templating,           │
│                     │ conversation, incremental sync, session pruning,  │
│                     │ login/JWT issuance, role enforcement, password    │
│                     │ hashing (argon2id), account provisioning. Reaches │
│                     │ CF ONLY via broker.                               │
├─────────────────────┼──────────────────────────────────────────────────┤
│ frontend — Astro +  │ SSR shell + login page + reactive streaming chat  │
│ Vue 3 (VPS)         │ island.                                           │
└─────────────────────┴──────────────────────────────────────────────────┘
```

### 3.3 Repository layout

The project is a small monorepo. The skeleton is **mandated** by OpenSpec task 1.1 (`apps/{backend,frontend,broker}/` + `packages/`); the internal layout of each app is a **proposed convention** to finalize at apply time. Capability ownership is tagged so it's clear which spec governs which files.

**Tooling by app:**
- `apps/backend` — Python 3.11+ / FastAPI, managed with **uv** (`uv init backend`, `uv add 'fastapi[standard]' httpx argon2-cffi` — the `standard` extra bundles `uvicorn`).
- `apps/frontend` — Astro 4 (SSR) + Vue 3, managed with **pnpm** (scaffolded with `pnpm create astro@latest`; Vue via `pnpm astro add vue`; Node adapter via `pnpm astro add node`).
- `apps/broker` — **Hono** on the Cloudflare Workers runtime (TypeScript), managed with **pnpm** (scaffolded with `pnpm create hono@latest broker -- --template cloudflare-workers`); deployed with Wrangler.

**Mandated skeleton:**
```
jellirag/
├── apps/
│   ├── backend/           # Python (uv) — FastAPI: RAG decisions + auth + RBAC
│   ├── frontend/          # pnpm — Astro 4 SSR + Vue 3 islands
│   └── broker/            # pnpm — Hono on Cloudflare Worker (TS) — CF bindings + secrets
├── packages/             # shared types/schemas (likely minimal — see note)
├── deploy/               # Dockerfiles + Dokploy/Traefik stack (task 1.5)
├── openspec/             # change tracking
└── PRD.md
```

**Proposed per-app layout (convention — finalize at apply):**
```
apps/backend/                                       [task 1.3: app/{routers,services,config,budget}; uv-managed]
├── pyproject.toml
├── Dockerfile
└── app/
    ├── main.py                # FastAPI app + ensure-bootstrap-admin startup hook
    ├── config.py              # BROKER_*, JELLYFIN_*, JWT_*, SESSION_TTL_DAYS, BOOTSTRAP_ADMIN_*
    ├── deps.py                # current_user, require_role("admin"|"member")            [auth]
    ├── routers/
    │   ├── auth.py            # POST /api/auth/login                                   [auth]
    │   ├── chat.py            # POST /api/chat/stream                                  [rag-chat]
    │   ├── history.py         # GET /api/history/*, DELETE /api/sessions/* (own)       [rag-chat]
    │   ├── sync.py            # POST /api/sync (admin-only)                            [library-sync]
    │   ├── sessions.py        # POST /api/sessions/prune (admin-only)                  [rag-chat]
    │   └── admin/users.py     # /api/admin/users/* (admin-only)                        [auth]
    ├── services/
    │   ├── broker_client.py   # async httpx → broker                                  (all)
    │   ├── jellyfin_client.py # async httpx → Jellyfin over Tailscale                  [library-sync]
    │   ├── auth.py            # argon2id hashing, JWT issue/verify                     [auth]
    │   ├── sync.py            # run_library_sync, two-way diff                         [library-sync]
    │   ├── chunks.py          # chunk synthesis + sha256, ≤512-token sizing            [library-sync]
    │   └── deep_links.py      # Tailscale-base link templating                         [rag-chat]
    └── budget/
        └── manager.py         # context-budget mgr, char-heuristic, max_tokens wiring  [rag-chat]

apps/frontend/                                      [task 1.2; pnpm]
├── package.json
├── Dockerfile
├── astro.config.mjs          # @astrojs/vue (Vue 3), @astrojs/node adapter
└── src/
    ├── pages/{login,index,admin/users}.astro
    ├── islands/{ChatIsland,Login,AdminUsers}.vue
    └── components/           # "requires Tailscale" affordance, source chips

apps/broker/                                       [task 1.4; pnpm — Hono on Cloudflare Workers]
├── package.json
├── wrangler.jsonc            # bindings: AI · INDEX(Vectorize) · DB(D1); secret BROKER_SECRET
├── migrations/
│   └── 0001_initial_schema.sql   # ← moves here at apply time (next to wrangler.jsonc)
└── src/
    ├── index.ts              # Hono app + route registration (fetch handler)
    ├── auth.ts               # X-Broker-Secret constant-time check (Hono middleware)
    ├── validation.ts         # shape/size + platform-limit guards (top_k≤20, ≤100 params, ≤1000 upsert…)
    └── routes/
        ├── prepare-rag.ts    # HOT PATH — fused read                                  [rag-chat]
        ├── search.ts / embed.ts / chunks.ts
        ├── llm-stream.ts     # receives max_tokens from FastAPI (never the 256 default) [rag-chat]
        ├── history.ts        # /history/read, /append (owner_email stamping)          [rag-chat]
        ├── ingest.ts         # /ingest/upsert, /delete (chunked)                      [library-sync]
        ├── sync-state.ts / sessions.ts  (/sessions/prune)
        └── users.ts          # /users/lookup|create|list|update|delete                [auth]

deploy/
├── docker-compose.yml        # Dokploy stack: frontend (Astro) :3000, backend (FastAPI) :8000 + Traefik labels
└── (Dockerfiles may instead live at each app root — convention choice)
```

**Capability → folder mapping:**

| Capability | Primary home | Also touches |
|------------|--------------|--------------|
| `rag-chat` | `apps/backend` (chat router, budget, deep_links, sessions) | `apps/frontend` (ChatIsland), `apps/broker` (prepare-rag, llm-stream, history) |
| `library-sync` | `apps/backend` (sync, chunks, jellyfin_client) | `apps/broker` (ingest, sync-state) |
| `broker` | `apps/broker` (all) | `migrations/` |
| `edge-security` | `deploy/` (Traefik/TLS), `apps/backend` (CORS), `apps/broker` (secret check) | cross-cutting |
| `auth` | `apps/backend` (auth router, admin/users, services/auth, deps) | `apps/broker` (users), `migrations` (users table), `apps/frontend` (Login, AdminUsers) |

**Notes:**
- **Migration file relocation:** at spec time the SQL lives at `openspec/changes/jellirag-mvp/migrations/0001_initial_schema.sql`; at apply time it moves to `apps/broker/migrations/` so `wrangler d1 migrations apply jellyrag --remote` finds it next to `wrangler.jsonc`.
- **`packages/` is likely empty.** FastAPI is Python (no TS sharing), and the frontend talks to FastAPI — not the broker — so there's no natural TS contract to share. Keep as a placeholder unless a shared schema (e.g., broker OpenAPI/JSON Schema as source of truth) is desired.
- **Dockerfile location** (per-app root vs. central `deploy/`) is a convention choice; either works as long as the Dokploy compose build contexts line up.

---

## 4. Technology Stack

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| Frontend foundation | **Astro 4** (SSR) | Zero-JS structural HTML; isolated hydration |
| Interactive UI | **Vue 3** (Composition API islands) | Reactive streaming chat state |
| RAG / ingestion backend | **FastAPI** (Python 3.11+) | Async; all RAG *decisions* live here |
| VPS proxy/deploy | **Dokploy → Traefik** | Auto-TLS, container routing (`:3000`, `:8000`) |
| Credentials broker | **Hono** on **Cloudflare Workers** (TS) — pnpm + Wrangler | Holds AI/Vectorize/D1 bindings; sole CF creds; fused reads |
| Inference | **Cloudflare Workers AI** — `@cf/baai/bge-small-en-v1.5` (384-d), `@cf/meta/llama-3.1-8b-instruct(-fast)` | GPU offload to edge |
| Vector index | **Cloudflare Vectorize** (cosine, 384-d) | Serverless semantic store |
| Relational store | **Cloudflare D1** (SQLite) | Chunks, conversation history, sessions, sync state (FTS5 available) |
| Overlay network | **Tailscale** (WireGuard + MagicDNS) | VPS↔homelab without port forwarding; human-readable deep-link base |
| Auth perimeter | **FastAPI JWT** (app-owned; argon2id password hashing) — D1 `users` table, two roles (`admin`/`member`) | Cloudflare Access dropped — family-scale (≤ ~3 users) does not warrant CF identities; re-layerable without code changes |

---

## 5. Process Flows

### 5.1 Conversational RAG — chat request (two broker calls)

```
 Browser                FastAPI                 broker               [CF bindings]
   │  POST /api/chat      │                         │                     │
   │  /stream             │                         │                     │
   ├─────────────────────▶│                         │                     │
   │                      │ 1. /prepare-rag         │                     │
   │                      │  {session,msg,topK,     │                     │
   │                      │   history_max_tokens}   │                     │
   │                      ├────────────────────────▶│ embed (AI) ─────────▶│
   │                      │                         │ Vectorize query ────▶│
   │                      │                         │ D1 chunks ──────────▶│
   │                      │                         │ D1 history window ──▶│
   │                      │◀ {matches,chunks,history}┤  (one round-trip)  │
   │                      │ ┌─ context-budget mgr ─┐│                     │
   │                      │ │ reconcile budget     ││                     │
   │                      │ │ (trim oldest history ││                     │
   │                      │ │  by actual chunk sz) ││                     │
   │                      │ │ assemble messages[]  ││                     │
   │                      │ └──────────────────────┘│                     │
   │                      │ 2. /llm-stream {messages}                     │
   │                      ├────────────────────────▶│ AI llama stream ───▶│
   │   SSE data:{token}   │◀══ token stream ════════┤                     │
   │◀═════════════════════│  (re-framed to data:)   │                     │
   │   data: [DONE]       │                         │                     │
   │◀─────────────────────┤                         │                     │
   │                      │ 3. /history/append user+assistant turns       │
   │                      ├────────────────────────▶│ D1 write ──────────▶│
   │                      │                         │ bump last_active_at │
```

> The hot path is **two broker calls** (`/prepare-rag` + `/llm-stream`) plus the append write after streaming completes. Retrieval reads fuse on the edge; budget reconciliation stays in FastAPI.

### 5.2 Incremental library sync (explicit two-way set difference)

```
 [cron / manual trigger]
        │
        ▼
  fetch Jellyfin /Items ───────▶ (over Tailscale 100.x.y.z:8096)
        │  fail-fast if unreachable
        ▼
  for each item: synthesize chunk_text → sha256(content_hash)
        │
        ▼
  jellyfin_ids  = { ids in Jellyfin response }
  known_ids     = { non-deleted jf_id in sync_state (D1) }
        │
        ▼
  ┌────────────── two-way diff ────────────────────┐
  │ to_add    = jellyfin_ids − known_ids           │
  │             → embed + upsert (Vectorize) + D1  │
  │ to_update = ∩, hash changed                    │
  │             → re-embed + upsert + update D1/state │
  │ unchanged = ∩, hash equal → skip              │
  │ to_remove = known_ids − jellyfin_ids  ◀── catches deletions
  │             → delete vector + D1 chunk; mark state │
  └──────────────────────────────────────────────────┘
        │  batched broker calls (bounded request count)
        ▼
  update sync_state hashes + timestamps
        │
        ▼
  return status summary
```

> `to_remove = known_ids − jellyfin_ids` is the step that catches media deleted from Jellyfin. Iterating only Jellyfin's response would silently miss deletions.

### 5.3 Credential boundary (every CF operation)

```
 FastAPI ──X-Broker-Secret──▶ broker ──binding──▶ [AI / Vectorize / D1]
   · NO api.cloudflare.com calls from VPS
   · NO CF token in VPS env/image/repo
   · VPS env: JELLYFIN_API_KEY + BROKER_SECRET (+ deeplink base, TTL)
```

---

## 6. Data Model

All tables in a single **Cloudflare D1** database.

### 6.1 `chunks` — full chunk text store
| column | type | notes |
|--------|------|-------|
| `jf_id` | TEXT PK | Jellyfin Item ID |
| `title` | TEXT | |
| `year` | INTEGER | |
| `genres` | TEXT | comma-joined |
| `cast` | TEXT | comma-joined, top-N actors |
| `overview` | TEXT | raw Jellyfin overview |
| `chunk_text` | TEXT | synthesized normalized chunk |
| `content_hash` | TEXT | sha256(chunk_text) |
| `updated_at` | TEXT | ISO timestamp |

### 6.2 Vectorize vector metadata (slim)
`{ jf_id, title, year, genre }` — filter-friendly; **no** full text (stays in D1).

### 6.3 `sessions` — conversation sessions
| column | type | notes |
|--------|------|-------|
| `session_id` | TEXT PK | conversation key |
| `owner_email` | TEXT | the user who owns this session; scopes every history read/append (taken from the caller's JWT, never the request body) |
| `created_at` | TEXT | |
| `last_active_at` | TEXT | bumped on every turn; drives TTL pruning |

### 6.4 `messages` — conversation history (append-only)
| column | type | notes |
|--------|------|-------|
| `session_id` | TEXT | FK→sessions |
| `seq` | INTEGER | monotonic per session |
| `role` | TEXT | system/user/assistant |
| `content` | TEXT | |
| `token_count` | INTEGER | char-heuristic estimate |
| `created_at` | TEXT | |

### 6.5 `sync_state` — incremental sync bookkeeping
| column | type | notes |
|--------|------|-------|
| `jf_id` | TEXT PK | |
| `content_hash` | TEXT | |
| `last_synced_at` | TEXT | |
| `jellyfin_updated_at` | TEXT | |
| `deleted_at` | TEXT | nullable |

### 6.6 `users` — app-owned accounts (RBAC)
| column | type | notes |
|--------|------|-------|
| `email` | TEXT PK | login identifier; also the `owner_email` on `sessions` |
| `role` | TEXT | `admin` or `member` |
| `pw_hash` | TEXT | argon2id (or bcrypt) hash — produced/verified in **FastAPI**, never in the broker |
| `created_at` | TEXT | |

> The broker stores and returns `pw_hash` as an opaque blob; it performs no password verification and no role interpretation. Two roles only — `admin` (sync trigger, session pruning, `/api/admin/users/*` account provisioning) and `member` (chat, own history). The first admin is seeded by a one-shot `ensure-bootstrap-admin` FastAPI startup hook; subsequent accounts are admin-provisioned.

> **Pruning:** a FastAPI scheduled job deletes `sessions` (cascading to `messages`) whose `last_active_at` exceeds `SESSION_TTL_DAYS` (default 30; `0` disables) by calling broker `POST /sessions/prune {older_than}` — the VPS cannot reach D1 directly. `last_active_at` is bumped by `/history/append` on each turn. Whole inactive sessions only — an active conversation's history is never touched mid-session. Pruning is admin-triggered (manual endpoint) or cron-driven; members cannot prune.
>
> **FTS5 note:** D1 supports SQLite FTS5 virtual tables. Not wired for MVP, but the schema leaves room for future hybrid (semantic + keyword) search. Caveat: FTS5 virtual tables must be dropped/recreated around D1 exports.

---

## 7. Security Model

### 7.1 Credential isolation (NFR-3)
- **Only** the `broker` Worker holds Cloudflare credentials (via Workers Secrets).
- The VPS holds **only** `JELLYFIN_API_KEY` + `BROKER_SECRET` (+ non-secret `JELLYFIN_DEEPLINK_BASE`, `SESSION_TTL_DAYS`), stored in **Dokploy encrypted env** (never in image/repo).
- **Blast radius of VPS compromise:** Jellyfin key (homelab-scoped, rotatable) + broker secret (rotatable, rotates no CF cred). The Cloudflare account is **not** reachable from the VPS.

### 7.2 Network isolation (NFR-2)
- VPS↔homelab over **Tailscale WireGuard** (`100.x.y.z` / MagicDNS); zero public homelab ports.
- Public ingress HTTPS-only (Traefik TLS; HTTP→HTTPS redirect).

### 7.3 Authentication & RBAC
- **App-owned authentication** (no Cloudflare Access for MVP): FastAPI verifies email/password against the D1 `users` table (argon2id) and issues a JWT `{sub, role, exp}` signed with `JWT_SECRET`. Every `/api/*` route (except `/api/auth/login`) requires a valid JWT.
- **Two roles**: `admin` (sync trigger, session pruning, account provisioning) and `member` (chat, own-history management). Enforced by FastAPI dependencies.
- **Private history**: `sessions.owner_email` scopes every history read/append to the caller (derived from the JWT, never the request body). Members cannot read each other's sessions; admins do not implicitly read member sessions in MVP.
- **Bootstrap**: the first admin is seeded by an idempotent FastAPI startup hook (`BOOTSTRAP_ADMIN_EMAIL` + `BOOTSTRAP_ADMIN_PASSWORD`, only when `users` is empty). Subsequent accounts are admin-provisioned.
- **Trade-off**: dropping Cloudflare Access loses its free MFA/abuse shielding. Accepted for ≤ ~3 trusted family users; mitigated by argon2id hashing, HTTPS-only ingress, strict CORS, and per-email login rate limiting. CF Access may be re-layered in front of the public hostname later without code changes.

### 7.4 Broker hardening
- Constant-time secret comparison; `401` on mismatch.
- Input validation + size limits on every endpoint; `400` on bad shape/oversize.
- No raw SQL / raw account operations exposed — domain ops only; fused reads perform **no** policy decisions.
- For `users/*`: the broker stores/returns the opaque `pw_hash` and performs **no** password verification or role interpretation — all such policy lives in FastAPI. `/users/list` never returns `pw_hash`. `/users/delete` cascades to that user's `sessions` and `messages`.

### 7.5 CORS
- Locked to the exact configured frontend origin(s); other origins rejected.

### 7.6 Fail-closed deep links (FR-3 ↔ NFR-2)
- Deep links are rooted at the Tailnet base (`http://jellyfin.<tailnet>.ts.net:8096/...` or raw `100.x.y.z`).
- Off the Tailnet the hostname does not resolve (NXDOMAIN), so the link is **inert** — no public homelab exposure is required to satisfy FR-3. The UI surfaces a "requires Tailscale" affordance.

---

## 8. Performance & Constraints

### 8.1 TTFT < 800ms (NFR-1) — monitored (risk reduced)
The fused `/prepare-rag` collapses retrieval (embed + Vectorize query + chunk fetch + history read) into **one** edge round-trip, making the hot path **two broker calls** (`/prepare-rag` + `/llm-stream`) instead of five. This is the biggest available lever on TTFT and is built into the MVP, not deferred.

**Residual mitigations:**
- Co-locate the VPS geographically near a major Cloudflare colo.
- Keep the broker Worker warm; pin the `-fast` Llama variant.
- **Measure early.** **Escape hatch (Option C):** move the read path *fully* into the broker (single call, budgeting on edge) *without changing RAG logic semantics* — only its location.

### 8.2 Ingestion < 1GB RAM @ 5,000 items (NFR-1)
- Streamed + batched processing; incremental sync means steady-state runs process a handful of items.
- Broker batch endpoints keep request count bounded regardless of library size.

---

## 9. Deployment & Operations

### 9.1 Provisioning sequence
1. `wrangler vectorize create jellyfin-index --dimensions=384 --metric=cosine`
2. `wrangler d1 create jellyrag` → apply migrations (`chunks`, `messages`, `sessions` w/ `last_active_at` + `owner_email`, `sync_state`, `users` w/ `role` + `pw_hash`)
3. `wrangler secret put BROKER_SECRET`; deploy `broker`
4. VPS: Dokploy stack (`frontend` (Astro) `:3000`, `backend` (FastAPI) `:8000`) behind Traefik; inject encrypted env (`JELLYFIN_API_KEY`, `BROKER_SECRET`, `JELLYFIN_DEEPLINK_BASE`, `SESSION_TTL_DAYS`, `JWT_SECRET`, `JWT_TTL_DAYS`, `BOOTSTRAP_ADMIN_EMAIL`, `BOOTSTRAP_ADMIN_PASSWORD`)
5. Tailscale on VPS; enable MagicDNS + name the Jellyfin host; verify `curl -I http://100.x.y.z:8096/web/index.html`
6. On FastAPI startup, the `ensure-bootstrap-admin` hook seeds the operator's admin account (only if `users` is empty). Operator logs in and provisions the ≤ ~2 family member accounts via `/api/admin/users/*`. (Cloudflare Access is **not** configured for MVP.)
7. Manual full sync (admin); chat + deep-link smoke test

### 9.2 Secret rotation
- Broker secret rotates independently: update Worker secret + Dokploy env. **No Cloudflare credential changes.**
- Jellyfin key rotates in Dokploy env only.
- `JWT_SECRET` rotates in Dokploy env + FastAPI restart; invalidates all outstanding JWTs immediately (users must log in again). Independent of broker secret and CF creds.

### 9.3 Rollback
- Disable public hostname / Access policy; broker and VPS containers are independently stoppable. No shared mutable state outside CF + D1 → clean rollback.

### 9.4 Verification checklist
- `wrangler vectorize info` shows 384 / cosine.
- Wrong broker secret → `401`.
- Missing / expired / tampered JWT on `/api/*` → `401`.
- `member` calling sync / prune / `/api/admin/users/*` → `403`.
- Cross-owner history access (member reading another member's `session_id`) → behaves as "new session" (no leak).
- `/users/lookup` returns a `pw_hash` but performs **no** verification; `/users/list` returns **no** `pw_hash`.
- Unreachable Jellyfin → fail-fast, no partial Vectorize/D1 mutation.
- Off-Tailnet deep link → NXDOMAIN (fail-closed).
- Chat hot path → exactly two broker calls (`/prepare-rag` + `/llm-stream`).

---

## 10. Out of Scope / Deferred

| Item | Reason | Revisit |
|------|--------|---------|
| **RAG decisions in the Worker** | Broker may batch reads, but budget/policy stay in FastAPI (user decision). | Escape hatch (Option C) if TTFT NFR proves infeasible (§8.1). |
| **Hybrid (semantic + FTS5) search** | FTS5 enabled in schema but not wired. | Future enhancement; no re-ingestion needed. |
| **Multi-user at scale / per-session Durable Objects** | MVP multi-user is right-sized to ≤ ~3 family users with app-owned accounts + `owner_email` on `sessions`. | When the user set grows beyond family scale, migrate conversation state D1 → Durable Object per session. |
| **Cloudflare Access** | Dropped for MVP — family user set does not warrant CF identities; app-owned JWT auth is the perimeter. | Re-layer CF Access in front of the public hostname later (no code changes) if MFA/abuse shielding becomes needed. |
| **Self-service signup / fine-grained permission matrices** | Not justified for ≤ ~3 trusted users; admin-provisioned accounts + two roles (`admin`/`member`) suffice. | If the audience broadens. |

> FR-3 (deep links) is **no longer deferred** — resolved via fail-closed Tailscale/MagicDNS addresses (§7.6). Multi-user RBAC is **no longer deferred** — resolved via app-owned accounts + JWT roles + per-owner history, right-sized for a family-scale deployment (§7.3).

---

*Detailed, testable requirements for each capability live in the OpenSpec change at `openspec/changes/jellirag-mvp/` — `specs/{rag-chat,library-sync,broker,edge-security}/spec.md`.*

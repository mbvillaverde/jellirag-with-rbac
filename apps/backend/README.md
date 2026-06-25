# JellieRAG Backend

FastAPI backend for JellieRAG — RAG orchestration, auth/RBAC, sync, session lifecycle.

## Architecture

- **FastAPI** (Python 3.12+) on local LXC with docker-compose
- **SQLite** with sqlite-vec extension for vector search
- **OpenAI-compatible AI providers** (default: Ollama on MacBook over Tailscale)
- **Async I/O throughout** using httpx.AsyncClient

## Dependencies

```bash
# Using uv for fast dependency management
uv sync

# Key dependencies:
# - fastapi[standard] >= 0.137.1
# - aiosqlite >= 0.20.0
# - sqlite-vec >= 0.1.6
# - httpx >= 0.28.1
# - pyjwt >= 2.10.0
# - apscheduler >= 3.11.0
# - argon2-cffi >= 25.1.0
```

## Environment Variables

See `.dev.env.example` for the complete environment schema.

### AI Provider Configuration

The backend uses OpenAI-compatible HTTP clients for LLM and embeddings:

```bash
# LLM Configuration
LLM_BASE_URL=http://<macbook-tailnet-ip>:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=qwen2.5:7b
LLM_TIMEOUT_SECONDS=5

# Embeddings Configuration
EMBED_BASE_URL=http://<macbook-tailnet-ip>:11434/v1
EMBED_API_KEY=ollama
EMBED_MODEL=nomic-embed-text
EMBED_DIM=768
SYNC_EMBED_CONCURRENCY=4
```

### Hybrid Configurations

LLM and embeddings can point to different providers:

```bash
# Local LLM + hosted embeddings
LLM_BASE_URL=http://<macbook>:11434/v1
EMBED_BASE_URL=https://api.openai.com/v1
EMBED_API_KEY=<your-openai-key>
```

### Swap Procedure

To swap providers (e.g., Ollama → Groq when MacBook is offline):

1. Edit `.env` file
2. Update provider URLs and API keys
3. Restart backend: `docker-compose restart backend`

```bash
# Swap to Groq (fast fallback)
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_API_KEY=<groq-api-key>
LLM_MODEL=llama-3.1-8b-instant
```

### Fallback Procedure

When the MacBook is unavailable:

1. Edit `.env` to point LLM to hosted provider
2. Keep embeddings local (if preferred) or also swap to hosted
3. Restart backend
4. Monitor logs for successful provider connection

```bash
# Recommended fallback configuration
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_API_KEY=<your-groq-api-key>
LLM_MODEL=llama-3.1-8b-instant

# Keep embeddings local (requires MacBook reachable)
EMBED_BASE_URL=http://<macbook>:11434/v1
EMBED_API_KEY=ollama
```

## Database Setup

The backend initializes SQLite automatically on startup:

- **Path**: Configured by `SQLITE_PATH` (default `/var/jellirag/jellyrag.db`)
- **Migration**: Idempotent — runs on first startup only
- **Extension**: sqlite-vec loaded on every connection
- **Foreign Keys**: Enabled via `PRAGMA foreign_keys = ON;`

### Schema

Tables: `chunks`, `vec_chunks` (virtual), `sync_state`, `users`, `sessions`, `messages`

- **vec_chunks**: sqlite-vec virtual table with `embedding float[768]` + `jf_id` partition key
- **Cascades**: `users → sessions → messages` (ON DELETE CASCADE)
- **Validation**: `EMBED_DIM` validated against existing vec_chunks on startup

## Services

### AI Provider Services (`services/ai_provider.py`)

- **LLMClient**: Streaming chat completions via `/v1/chat/completions`
  - Warmup request on backend startup
  - Exponential backoff on HTTP 429
  - Graceful `503` errors for provider unreachability

- **EmbeddingsClient**: Text embeddings via `/v1/embeddings`
  - Bounded concurrency (configurable, default 4)
  - Batch input support
  - Exponential backoff on rate limits

### Database Service (`services/db.py`)

- Connection pool with aiosqlite
- Automatic sqlite-vec extension loading
- Token-budgeted history reads
- Atomic chunk upserts/deletes with vectors
- User CRUD with cascade enforcement

Key operations:
- `vector_search(query_vec, top_k, jf_id_whitelist)` → vector KNN + chunk JOIN
- `chunk_upsert_with_vector(...)` → atomic write to chunks + vec_chunks
- `chunk_delete_with_vector(jf_id)` → atomic delete
- `history_read/history_append` → owner-scoped conversation history
- `sessions_prune(older_than)` → TTL-based cleanup
- `users_*` → user CRUD with role enforcement

## Development

```bash
# Install dependencies
uv sync

# Copy env example
cp .dev.env.example .env
# Edit .env with your configuration

# Run development server
uv run uvicorn app.main:app --reload --port 8000

# Health check
curl http://localhost:8000/healthz
```

## Lifecycle Events

### On Startup

1. Create shared httpx.AsyncClient
2. Initialize SQLite + run migrations
3. Construct LLM and embeddings clients
4. Validate EMBED_DIM compatibility
5. Issue LLM warmup request (best-effort)
6. Run bootstrap admin (if users table empty)
7. Start APScheduler jobs (sync + prune)

### On Shutdown

1. Shutdown scheduler
2. Close httpx.AsyncClient
3. Close database connections

## Scheduled Jobs

- **Library Sync**: Runs on `SYNC_CRON` (default `0 3 * * *` UTC)
- **Session Prune**: Runs daily at 04:15 UTC (configurable via `SESSION_TTL_DAYS`)

Both jobs are best-effort — failures are logged but don't crash the scheduler.

## Testing

```bash
# Run basic smoke test
uv run python -c "from app.main import app; print('ok')"

# Test database connection
uv run python -c "from app.services.db import Database; import asyncio; asyncio.run(Database('/tmp/test.db', 768).initialize())"

# Test AI provider connectivity
# (requires Ollama running)
curl http://localhost:11434/api/tags
```

## Error Handling

- **AIProviderError**: Typed error for AI provider failures (status + message)
- **HTTP 503**: Provider unreachable / erroring
- **HTTP 500**: Unexpected internal error
- **HTTP 400**: Bad request / validation error
- **HTTP 401**: Unauthorized (invalid/missing JWT)
- **HTTP 403**: Forbidden (RBAC violation)

## Performance Considerations

### sqlite-vec Scaling

- **≤10k chunks**: Single-digit-ms query latency (typical family library)
- **50–100k+ chunks**: Consider migration to Qdrant (documented in deploy/README.md)

### Concurrency

- Embedding calls bounded by `SYNC_EMBED_CONCURRENCY` (default 4)
- Database uses small connection pool
- Async I/O throughout (no blocking operations)

### Token Budgeting

- **CONTEXT_CAP**: 6000 tokens total
- **RESPONSE_HEADROOM**: 768 tokens
- **HISTORY_REQUEST_BUDGET**: 1500 tokens
- Budget manager trims oldest history first

## Troubleshooting

### Provider Connectivity

```bash
# Test Ollama from backend environment
curl http://<macbook-tailnet-ip>:11434/api/tags

# Test embeddings endpoint
curl -X POST http://<macbook-tailnet-ip>:11434/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{"model":"nomic-embed-text","input":"test"}'
```

### Database Issues

```bash
# Check SQLite locks
sqlite3 /var/jellirag/jellyrag.db ".timeout 5000" "SELECT * FROM chunks LIMIT 1"

# Validate schema
sqlite3 /var/jellirag/jellyrag.db ".schema"
```

### Migration Failures

Check `_migrations` table for applied migrations:

```bash
sqlite3 /var/jellirag/jellyrag.db "SELECT * FROM _migrations"
```

## Production Notes

- **Single-file state**: All data in one SQLite file (easy backup/restore)
- **No Cloudflare dependencies**: Fully local operation
- **Tailnet-only access**: No public ingress, auto Let's Encrypt via tailscale serve
- **Graceful degradation**: 503 errors when AI provider unavailable
- **Idempotent operations**: Bootstrap admin, migrations, and sync all safe to re-run
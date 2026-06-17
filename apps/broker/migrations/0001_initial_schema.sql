-- JellieRAG initial D1 schema.
-- Canonical migration referenced by task 2.3 (design Appendix).
-- Tables only — no seed data. The first admin is provisioned by the
-- FastAPI ensure-bootstrap-admin startup hook (design D11), keeping this
-- migration side-effect-free and re-runnable.
--
-- D1 enforces foreign keys by default (equivalent to PRAGMA foreign_keys = on
-- on every transaction) and it is NOT toggleable mid-query, so the
-- ON DELETE CASCADE declarations below take effect automatically — no
-- per-connection PRAGMA is needed in the broker.
--
-- Cascade policy: users ──(CASCADE)──> sessions ──(CASCADE)──> messages

-- =============================================================================
-- library-sync: full chunk text + content hash. Vectorize holds slim metadata only.
-- =============================================================================
CREATE TABLE IF NOT EXISTS chunks (
  jf_id         TEXT    PRIMARY KEY,
  title         TEXT    NOT NULL,
  year          INTEGER,
  genres        TEXT,   -- comma-joined, defensive parse on write
  cast          TEXT,   -- comma-joined actors (Type == "Actor"), capped
  overview      TEXT,
  chunk_text    TEXT    NOT NULL,
  content_hash  TEXT    NOT NULL,   -- sha256(chunk_text)
  updated_at    TEXT    NOT NULL    -- ISO-8601
);

-- =============================================================================
-- library-sync: per-item incremental-sync bookkeeping.
-- =============================================================================
CREATE TABLE IF NOT EXISTS sync_state (
  jf_id                TEXT    PRIMARY KEY,
  content_hash         TEXT,
  last_synced_at       TEXT,
  deleted_at           TEXT,   -- nullable; set when item removed from Jellyfin
  jellyfin_updated_at  TEXT    -- best-effort upstream "DateCreated" stamp
);

-- Supports the two-way diff: "known, non-deleted" set is everything with
-- deleted_at IS NULL.
CREATE INDEX IF NOT EXISTS idx_sync_state_active
  ON sync_state (jf_id)
  WHERE deleted_at IS NULL;

-- =============================================================================
-- auth: app-owned accounts. pw_hash is an opaque argon2id blob produced by
-- FastAPI; the broker stores/returns it verbatim and never verifies it.
-- =============================================================================
CREATE TABLE IF NOT EXISTS users (
  email       TEXT    PRIMARY KEY,
  role        TEXT    NOT NULL CHECK (role IN ('admin', 'member')),
  pw_hash     TEXT    NOT NULL,
  created_at  TEXT    NOT NULL
);

-- =============================================================================
-- rag-chat: session lifecycle. owner_email scopes every history read/append.
-- =============================================================================
CREATE TABLE IF NOT EXISTS sessions (
  session_id      TEXT    PRIMARY KEY,
  owner_email     TEXT    NOT NULL REFERENCES users (email) ON DELETE CASCADE,
  created_at      TEXT    NOT NULL,
  last_active_at  TEXT    NOT NULL    -- bumped on every turn (design D10)
);

CREATE INDEX IF NOT EXISTS idx_sessions_owner        ON sessions (owner_email);
CREATE INDEX IF NOT EXISTS idx_sessions_last_active  ON sessions (last_active_at);

-- =============================================================================
-- rag-chat: append-only conversation turns. seq orders a session's history.
-- =============================================================================
CREATE TABLE IF NOT EXISTS messages (
  session_id   TEXT    NOT NULL REFERENCES sessions (session_id) ON DELETE CASCADE,
  seq          INTEGER NOT NULL,
  role         TEXT    NOT NULL CHECK (role IN ('system', 'user', 'assistant')),
  content      TEXT    NOT NULL,
  token_count  INTEGER NOT NULL DEFAULT 0,
  created_at   TEXT    NOT NULL,
  PRIMARY KEY (session_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_messages_session_seq
  ON messages (session_id, seq);

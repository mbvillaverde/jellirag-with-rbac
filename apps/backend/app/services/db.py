"""SQLite database service with aiosqlite and sqlite-vec support.

Provides async access to relational data and vector embeddings in a single file.
PRAGMA foreign_keys = ON; and sqlite-vec extension loading on every connection.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import aiosqlite
import sqlite_vec

log = logging.getLogger(__name__)


class Database:
    def __init__(self, path: str, embed_dim: int) -> None:
        self._path = path
        self._embed_dim = embed_dim
        self._pool: list[aiosqlite.Connection] = []
        self._lock = asyncio.Lock()
        self._max_pool_size = 10

    async def get_connection(self) -> aiosqlite.Connection:
        """Get a connection from the pool or create a new one."""
        async with self._lock:
            if self._pool:
                return self._pool.pop()
            conn = await aiosqlite.connect(self._path)
            await _setup_connection(conn)
            return conn

    async def return_connection(self, conn: aiosqlite.Connection) -> None:
        """Return a connection to the pool or close it."""
        async with self._lock:
            if len(self._pool) < self._max_pool_size:
                self._pool.append(conn)
            else:
                await conn.close()

    async def close(self) -> None:
        """Close all connections in the pool."""
        async with self._lock:
            for conn in self._pool:
                await conn.close()
            self._pool.clear()

    async def __aenter__(self) -> Database:
        await self.initialize()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def initialize(self) -> None:
        conn = await self.get_connection()
        try:
            await self._run_migrations(conn)
            await self._validate_embed_dim(conn)
        finally:
            await self.return_connection(conn)

    async def _run_migrations(self, conn: aiosqlite.Connection) -> None:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS _migrations (
                name TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        
        migration_name = "0001_initial_schema"
        cursor = await conn.execute("SELECT 1 FROM _migrations WHERE name = ?", (migration_name,))
        if await cursor.fetchone():
            return  # already applied

        log.info("Running migration: %s", migration_name)
        
        # Create tables from MVP broker migration
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS chunks (
                jf_id TEXT PRIMARY KEY,
                chunk_text TEXT NOT NULL,
                title TEXT,
                year INTEGER,
                genres TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS sync_state (
                jf_id TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL,
                synced_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS users (
                email TEXT PRIMARY KEY,
                pw_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('admin', 'member')),
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                owner_email TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                last_active_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (owner_email) REFERENCES users(email) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                token_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
            );
        """)
        
        # Create vec_chunks virtual table
        await conn.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks 
            USING vec0(embedding float[{self._embed_dim}], jf_id text partition key)
        """)
        
        # Create indexes
        await conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_sync_state_synced_at ON sync_state(synced_at);
            CREATE INDEX IF NOT EXISTS idx_sessions_owner_email ON sessions(owner_email);
            CREATE INDEX IF NOT EXISTS idx_sessions_last_active_at ON sessions(last_active_at);
            CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id);
        """)
        
        # Mark migration as applied
        await conn.execute("INSERT INTO _migrations (name) VALUES (?)", (migration_name,))
        await conn.commit()
        log.info("Migration completed: %s", migration_name)

    async def _validate_embed_dim(self, conn: aiosqlite.Connection) -> None:
        cursor = await conn.execute("""
            SELECT sql FROM sqlite_master WHERE type='table' AND name='vec_chunks'
        """)
        row = await cursor.fetchone()
        if row is None:
            return  # vec_chunks doesn't exist yet, will be created by migration

        sql = row[0]
        if f"float[{self._embed_dim}]" in sql:
            return

        raise RuntimeError(
            f"Embedding dimension mismatch: config has {self._embed_dim} but vec_chunks has different dimension. "
            f"Drop vec_chunks table and re-sync the library to change models."
        )

    async def vector_search(
        self, query_vec: list[float], top_k: int, jf_id_whitelist: list[str] | None = None
    ) -> list[dict[str, Any]]:
        conn = await self.get_connection()
        try:
            vec_blob = sqlite_vec.serialize_float32(query_vec)
            query = """
                SELECT c.jf_id, v.distance, c.chunk_text, c.title, c.year, c.genres
                FROM vec_chunks v
                LEFT JOIN chunks c ON c.jf_id = v.jf_id
                WHERE v.embedding MATCH ? AND k = ?
            """
            params: list[Any] = [vec_blob, top_k]
            
            if jf_id_whitelist:
                placeholders = ",".join(["?"] * len(jf_id_whitelist))
                query += f" AND v.jf_id IN ({placeholders})"
                params.extend(jf_id_whitelist)
            
            query += " ORDER BY v.distance"
            
            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()
            return [
                {
                    "jf_id": row[0],
                    "distance": row[1],
                    "chunk_text": row[2],
                    "title": row[3],
                    "year": row[4],
                    "genres": row[5],
                }
                for row in rows
            ]
        finally:
            await self.return_connection(conn)

    async def chunk_upsert_with_vector(self, jf_id: str, chunk_text: str, embedding: list[float], **metadata: Any) -> None:
        conn = await self.get_connection()
        try:
            vec_blob = sqlite_vec.serialize_float32(embedding)
            await conn.execute("BEGIN")
            
            await conn.execute("""
                INSERT OR REPLACE INTO chunks (jf_id, chunk_text, title, year, genres)
                VALUES (?, ?, ?, ?, ?)
            """, (jf_id, chunk_text, metadata.get("title"), metadata.get("year"), metadata.get("genres")))
            
            await conn.execute("""
                INSERT OR REPLACE INTO vec_chunks (embedding, jf_id)
                VALUES (?, ?)
            """, (vec_blob, jf_id))
            
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
        finally:
            await self.return_connection(conn)

    async def chunk_delete_with_vector(self, jf_id: str) -> None:
        conn = await self.get_connection()
        try:
            await conn.execute("BEGIN")
            await conn.execute("DELETE FROM vec_chunks WHERE jf_id = ?", (jf_id,))
            await conn.execute("DELETE FROM chunks WHERE jf_id = ?", (jf_id,))
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
        finally:
            await self.return_connection(conn)

    async def history_read(self, session_id: str, owner_email: str, max_tokens: int) -> list[dict[str, Any]]:
        conn = await self.get_connection()
        try:
            cursor = await conn.execute("""
                SELECT role, content, token_count, m.created_at
                FROM messages m
                JOIN sessions s ON m.session_id = s.session_id
                WHERE s.session_id = ? AND s.owner_email = ?
                ORDER BY m.created_at ASC
            """, (session_id, owner_email))
            rows = await cursor.fetchall()
            
            # Enforce token budget
            history: list[dict[str, Any]] = []
            total = 0
            for row in reversed(rows):
                if total + row[2] > max_tokens:
                    break
                history.insert(0, {
                    "role": row[0],
                    "content": row[1],
                    "token_count": row[2],
                    "created_at": row[3],
                })
                total += row[2]
            
            return history
        finally:
            await self.return_connection(conn)

    async def history_append(
        self, session_id: str, owner_email: str, role: str, content: str, token_count: int
    ) -> None:
        conn = await self.get_connection()
        try:
            await conn.execute("BEGIN")
            
            # Ensure session exists and bump last_active_at
            await conn.execute("""
                INSERT OR REPLACE INTO sessions (session_id, owner_email, created_at, last_active_at)
                VALUES (?, ?, COALESCE((SELECT created_at FROM sessions WHERE session_id = ?), datetime('now')), datetime('now'))
            """, (session_id, owner_email, session_id))
            
            # Append message
            await conn.execute("""
                INSERT INTO messages (session_id, role, content, token_count)
                VALUES (?, ?, ?, ?)
            """, (session_id, role, content, token_count))
            
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
        finally:
            await self.return_connection(conn)

    async def sessions_prune(self, older_than: str) -> dict[str, int]:
        conn = await self.get_connection()
        try:
            await conn.execute("BEGIN")
            
            cursor = await conn.execute("SELECT COUNT(*) FROM sessions WHERE last_active_at < ?", (older_than,))
            deleted_sessions = (await cursor.fetchone())[0]
            
            await conn.execute("DELETE FROM sessions WHERE last_active_at < ?", (older_than,))
            
            cursor = await conn.execute("SELECT changes()")
            deleted_messages = (await cursor.fetchone())[0]
            
            await conn.commit()
            return {"deleted_sessions": deleted_sessions, "deleted_messages": deleted_messages}
        except Exception:
            await conn.rollback()
            raise
        finally:
            await self.return_connection(conn)

    async def users_lookup(self, email: str) -> dict[str, Any] | None:
        conn = await self.get_connection()
        try:
            cursor = await conn.execute("SELECT email, pw_hash, role, created_at FROM users WHERE email = ?", (email,))
            row = await cursor.fetchone()
            if row is None:
                return None
            return {
                "email": row[0],
                "pw_hash": row[1],
                "role": row[2],
                "created_at": row[3],
            }
        finally:
            await self.return_connection(conn)

    async def users_create(self, email: str, role: str, pw_hash: str) -> None:
        conn = await self.get_connection()
        try:
            await conn.execute("""
                INSERT INTO users (email, pw_hash, role)
                VALUES (?, ?, ?)
            """, (email, pw_hash, role))
            await conn.commit()
        finally:
            await self.return_connection(conn)

    async def users_list(self) -> list[dict[str, Any]]:
        conn = await self.get_connection()
        try:
            cursor = await conn.execute("SELECT email, role, created_at FROM users ORDER BY email")
            rows = await cursor.fetchall()
            return [
                {
                    "email": row[0],
                    "role": row[1],
                    "created_at": row[2],
                }
                for row in rows
            ]
        finally:
            await self.return_connection(conn)

    async def users_update(self, email: str, role: str | None = None, pw_hash: str | None = None) -> None:
        conn = await self.get_connection()
        try:
            if role is not None and pw_hash is not None:
                await conn.execute("""
                    UPDATE users SET role = ?, pw_hash = ? WHERE email = ?
                """, (role, pw_hash, email))
            elif role is not None:
                await conn.execute("UPDATE users SET role = ? WHERE email = ?", (role, email))
            elif pw_hash is not None:
                await conn.execute("UPDATE users SET pw_hash = ? WHERE email = ?", (pw_hash, email))
            await conn.commit()
        finally:
            await self.return_connection(conn)

    async def users_delete(self, email: str) -> dict[str, int]:
        conn = await self.get_connection()
        try:
            await conn.execute("BEGIN")
            
            cursor = await conn.execute("SELECT COUNT(*) FROM sessions WHERE owner_email = ?", (email,))
            deleted_sessions = (await cursor.fetchone())[0]
            
            await conn.execute("DELETE FROM users WHERE email = ?", (email,))
            
            cursor = await conn.execute("SELECT changes()")
            deleted_messages = (await cursor.fetchone())[0]
            
            await conn.commit()
            return {"deleted_users": 1, "deleted_sessions": deleted_sessions, "deleted_messages": deleted_messages}
        except Exception:
            await conn.rollback()
            raise
        finally:
            await self.return_connection(conn)


async def _setup_connection(conn: aiosqlite.Connection) -> None:
    await conn.execute("PRAGMA foreign_keys = ON;")
    await conn.enable_load_extension(True)
    await conn.load_extension(sqlite_vec.loadable_path())
    await conn.enable_load_extension(False)
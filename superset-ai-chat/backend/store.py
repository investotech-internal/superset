# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""Per-user conversation persistence backed by SQLite.

Each conversation belongs to exactly one Superset user (keyed by username from
the session token). Users can only ever list, read, or delete their own
conversations -- ownership is enforced on every query by filtering on username.
The whole message array is stored as a JSON blob, which is sufficient for a
chat transcript and avoids a second table.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

DB_PATH = os.environ.get("CHAT_DB_PATH", "/app/data/chat.db")
_MAX_TITLE = 80


def _connect() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT 'New chat',
                messages TEXT NOT NULL DEFAULT '[]',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_conv_user "
            "ON conversations(username, updated_at DESC)"
        )


_init()


def _row_summary(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "updated_at": row["updated_at"],
    }


# --- synchronous implementations (run in a thread) ------------------------
def _list(username: str) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, title, updated_at FROM conversations "
            "WHERE username = ? ORDER BY updated_at DESC",
            (username,),
        ).fetchall()
    return [_row_summary(r) for r in rows]


def _get(username: str, conv_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, title, messages, updated_at FROM conversations "
            "WHERE id = ? AND username = ?",
            (conv_id, username),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "title": row["title"],
        "messages": json.loads(row["messages"]),
        "updated_at": row["updated_at"],
    }


def _create(username: str, title: str) -> dict[str, Any]:
    conv_id = str(uuid.uuid4())
    now = time.time()
    title = (title or "New chat").strip()[:_MAX_TITLE] or "New chat"
    with _connect() as conn:
        conn.execute(
            "INSERT INTO conversations "
            "(id, username, title, messages, created_at, updated_at) "
            "VALUES (?, ?, ?, '[]', ?, ?)",
            (conv_id, username, title, now, now),
        )
    return {"id": conv_id, "title": title, "updated_at": now}


def _save(
    username: str, conv_id: str, title: str | None, messages: list[Any]
) -> bool:
    now = time.time()
    payload = json.dumps(messages)
    with _connect() as conn:
        if title is not None:
            title = title.strip()[:_MAX_TITLE] or "New chat"
            cur = conn.execute(
                "UPDATE conversations SET messages = ?, title = ?, updated_at = ? "
                "WHERE id = ? AND username = ?",
                (payload, title, now, conv_id, username),
            )
        else:
            cur = conn.execute(
                "UPDATE conversations SET messages = ?, updated_at = ? "
                "WHERE id = ? AND username = ?",
                (payload, now, conv_id, username),
            )
        return cur.rowcount > 0


def _delete(username: str, conv_id: str) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM conversations WHERE id = ? AND username = ?",
            (conv_id, username),
        )
        return cur.rowcount > 0


def _delete_all(username: str) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM conversations WHERE username = ?", (username,)
        )
        return cur.rowcount


# --- async wrappers -------------------------------------------------------
async def list_conversations(username: str) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_list, username)


async def get_conversation(username: str, conv_id: str) -> dict[str, Any] | None:
    return await asyncio.to_thread(_get, username, conv_id)


async def create_conversation(username: str, title: str) -> dict[str, Any]:
    return await asyncio.to_thread(_create, username, title)


async def save_conversation(
    username: str, conv_id: str, title: str | None, messages: list[Any]
) -> bool:
    return await asyncio.to_thread(_save, username, conv_id, title, messages)


async def delete_conversation(username: str, conv_id: str) -> bool:
    return await asyncio.to_thread(_delete, username, conv_id)


async def delete_all_conversations(username: str) -> int:
    return await asyncio.to_thread(_delete_all, username)

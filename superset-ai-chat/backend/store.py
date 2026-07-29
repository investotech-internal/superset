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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_tasks (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                username TEXT NOT NULL,
                status TEXT NOT NULL,
                assistant_text TEXT NOT NULL DEFAULT '',
                error TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_tasks_conversation "
            "ON chat_tasks(conversation_id, username, updated_at DESC)"
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


def _append_message(username: str, conv_id: str, message: dict[str, Any]) -> bool:
    now = time.time()
    with _connect() as conn:
        row = conn.execute(
            "SELECT messages FROM conversations WHERE id = ? AND username = ?",
            (conv_id, username),
        ).fetchone()
        if row is None:
            return False
        messages = json.loads(row["messages"])
        messages.append(message)
        conn.execute(
            "UPDATE conversations SET messages = ?, updated_at = ? "
            "WHERE id = ? AND username = ?",
            (json.dumps(messages), now, conv_id, username),
        )
    return True


def _create_chat_task(username: str, conv_id: str) -> dict[str, Any] | None:
    now = time.time()
    task_id = str(uuid.uuid4())
    with _connect() as conn:
        conversation = conn.execute(
            "SELECT 1 FROM conversations WHERE id = ? AND username = ?",
            (conv_id, username),
        ).fetchone()
        if conversation is None:
            return None
        active = conn.execute(
            "SELECT id FROM chat_tasks WHERE conversation_id = ? AND username = ? "
            "AND status = 'running' ORDER BY created_at DESC LIMIT 1",
            (conv_id, username),
        ).fetchone()
        if active is not None:
            return {"id": active["id"], "status": "running", "created": False}
        conn.execute(
            "INSERT INTO chat_tasks "
            "(id, conversation_id, username, status, created_at, updated_at) "
            "VALUES (?, ?, ?, 'running', ?, ?)",
            (task_id, conv_id, username, now, now),
        )
    return {"id": task_id, "status": "running", "created": True}


def _get_chat_task(username: str, task_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, conversation_id, status, assistant_text, error "
            "FROM chat_tasks WHERE id = ? AND username = ?",
            (task_id, username),
        ).fetchone()
    return dict(row) if row is not None else None


def _get_active_chat_task(username: str, conv_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, status FROM chat_tasks WHERE conversation_id = ? "
            "AND username = ? AND status = 'running' "
            "ORDER BY created_at DESC LIMIT 1",
            (conv_id, username),
        ).fetchone()
    return dict(row) if row is not None else None


def _complete_chat_task(
    username: str, task_id: str, assistant_text: str, error: str | None
) -> None:
    now = time.time()
    status = "failed" if error else "complete"
    with _connect() as conn:
        task = conn.execute(
            "SELECT conversation_id FROM chat_tasks WHERE id = ? AND username = ?",
            (task_id, username),
        ).fetchone()
        if task is None:
            return
        if assistant_text:
            conversation = conn.execute(
                "SELECT messages FROM conversations WHERE id = ? AND username = ?",
                (task["conversation_id"], username),
            ).fetchone()
            if conversation is not None:
                messages = json.loads(conversation["messages"])
                messages.append({"role": "assistant", "content": assistant_text})
                conn.execute(
                    "UPDATE conversations SET messages = ?, updated_at = ? "
                    "WHERE id = ? AND username = ?",
                    (
                        json.dumps(messages),
                        now,
                        task["conversation_id"],
                        username,
                    ),
                )
        conn.execute(
            "UPDATE chat_tasks SET status = ?, assistant_text = ?, error = ?, "
            "updated_at = ? WHERE id = ? AND username = ?",
            (status, assistant_text, error, now, task_id, username),
        )


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


async def append_message(
    username: str, conv_id: str, message: dict[str, Any]
) -> bool:
    return await asyncio.to_thread(_append_message, username, conv_id, message)


async def create_chat_task(username: str, conv_id: str) -> dict[str, Any] | None:
    return await asyncio.to_thread(_create_chat_task, username, conv_id)


async def get_chat_task(username: str, task_id: str) -> dict[str, Any] | None:
    return await asyncio.to_thread(_get_chat_task, username, task_id)


async def get_active_chat_task(
    username: str, conv_id: str
) -> dict[str, Any] | None:
    return await asyncio.to_thread(_get_active_chat_task, username, conv_id)


async def complete_chat_task(
    username: str, task_id: str, assistant_text: str, error: str | None
) -> None:
    await asyncio.to_thread(_complete_chat_task, username, task_id, assistant_text, error)


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

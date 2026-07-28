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

"""FastAPI application for the Superset AI Chat app.

Serves a single-page chat UI and exposes:
  POST   /api/login              -> validate Superset credentials, set cookie
  POST   /api/logout             -> clear session
  GET    /api/me                 -> current session info
  POST   /api/chat               -> stream an agent turn over Server-Sent Events
  GET    /api/conversations      -> list the current user's saved chats
  POST   /api/conversations      -> create a new chat
  GET    /api/conversations/{id} -> load a saved chat
  PUT    /api/conversations/{id} -> save a chat's messages/title
  DELETE /api/conversations/{id} -> delete a chat
  DELETE /api/conversations      -> delete all of the user's chats
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import Cookie, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, config, store
from .agent import run_agent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("superset_ai_chat")

app = FastAPI(title="Superset AI Chat", docs_url=None, redoc_url=None)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------
class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=1024)


class ChatMessage(BaseModel):
    role: str
    content: Any


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(default_factory=list)


class CreateConversationRequest(BaseModel):
    title: str = Field(default="New chat", max_length=200)


class SaveConversationRequest(BaseModel):
    title: Optional[str] = Field(default=None, max_length=200)
    messages: list[ChatMessage] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Session helpers
# --------------------------------------------------------------------------
def _require_session(token: Optional[str]) -> dict[str, Any]:
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    claims = auth.decode_session_token(token)
    if not claims:
        raise HTTPException(status_code=401, detail="Session expired")
    return claims


# --------------------------------------------------------------------------
# Auth routes
# --------------------------------------------------------------------------
@app.post("/api/login")
async def login(req: LoginRequest, response: Response) -> dict[str, Any]:
    try:
        user = await auth.verify_superset_credentials(req.username, req.password)
    except auth.AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    token = auth.issue_session_token(user)
    response.set_cookie(
        key=config.COOKIE_NAME,
        value=token,
        max_age=config.SESSION_TTL_SECONDS,
        httponly=True,
        secure=config.COOKIE_SECURE,
        samesite="lax",
    )
    return {"username": user["username"], "display_name": user["display_name"]}


@app.post("/api/logout")
async def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(config.COOKIE_NAME)
    return {"ok": True}


@app.get("/api/me")
async def me(
    session: Optional[str] = Cookie(default=None, alias=config.COOKIE_NAME),
) -> dict[str, Any]:
    claims = _require_session(session)
    return {"username": claims["sub"], "display_name": claims.get("name")}


# --------------------------------------------------------------------------
# Chat route (SSE streaming)
# --------------------------------------------------------------------------
@app.post("/api/chat")
async def chat(
    req: ChatRequest,
    session: Optional[str] = Cookie(default=None, alias=config.COOKIE_NAME),
) -> StreamingResponse:
    _require_session(session)

    # Convert to plain Anthropic-format messages.
    messages = [{"role": m.role, "content": m.content} for m in req.messages]
    if not messages:
        raise HTTPException(status_code=400, detail="No messages provided")

    async def event_stream() -> Any:
        try:
            async for event in run_agent(messages):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as exc:  # noqa: BLE001
            logger.exception("Chat stream failed")
            err = {"type": "error", "message": str(exc)}
            yield f"data: {json.dumps(err)}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# --------------------------------------------------------------------------
# Conversation persistence (per user)
# --------------------------------------------------------------------------
@app.get("/api/conversations")
async def list_conversations(
    session: Optional[str] = Cookie(default=None, alias=config.COOKIE_NAME),
) -> list[dict[str, Any]]:
    claims = _require_session(session)
    return await store.list_conversations(claims["sub"])


@app.post("/api/conversations")
async def create_conversation(
    req: CreateConversationRequest,
    session: Optional[str] = Cookie(default=None, alias=config.COOKIE_NAME),
) -> dict[str, Any]:
    claims = _require_session(session)
    return await store.create_conversation(claims["sub"], req.title)


@app.get("/api/conversations/{conv_id}")
async def get_conversation(
    conv_id: str,
    session: Optional[str] = Cookie(default=None, alias=config.COOKIE_NAME),
) -> dict[str, Any]:
    claims = _require_session(session)
    conv = await store.get_conversation(claims["sub"], conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@app.put("/api/conversations/{conv_id}")
async def save_conversation(
    conv_id: str,
    req: SaveConversationRequest,
    session: Optional[str] = Cookie(default=None, alias=config.COOKIE_NAME),
) -> dict[str, bool]:
    claims = _require_session(session)
    messages = [{"role": m.role, "content": m.content} for m in req.messages]
    ok = await store.save_conversation(
        claims["sub"], conv_id, req.title, messages
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"ok": True}


@app.delete("/api/conversations/{conv_id}")
async def delete_conversation(
    conv_id: str,
    session: Optional[str] = Cookie(default=None, alias=config.COOKIE_NAME),
) -> dict[str, bool]:
    claims = _require_session(session)
    ok = await store.delete_conversation(claims["sub"], conv_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"ok": True}


@app.delete("/api/conversations")
async def delete_all_conversations(
    session: Optional[str] = Cookie(default=None, alias=config.COOKIE_NAME),
) -> dict[str, int]:
    claims = _require_session(session)
    deleted = await store.delete_all_conversations(claims["sub"])
    return {"deleted": deleted}


# --------------------------------------------------------------------------
# Static frontend
# --------------------------------------------------------------------------
@app.get("/")
async def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="static")

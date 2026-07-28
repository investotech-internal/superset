<!--
  Licensed to the Apache Software Foundation (ASF) under one
  or more contributor license agreements.  See the NOTICE file
  distributed with this work for additional information
  regarding copyright ownership.  The ASF licenses this file
  to you under the Apache License, Version 2.0 (the
  "License"); you may not use this file except in compliance
  with the License.  You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

  Unless required by applicable law or agreed to in writing,
  software distributed under the License is distributed on an
  "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
  KIND, either express or implied.  See the License for the
  specific language governing permissions and limitations
  under the License.
-->

# Superset AI Chat

A ChatGPT/Claude-style web app that lets users **chat with their data** and
**build charts and full dashboards** in Apache Superset through natural language.

Users sign in with their **Superset credentials**. The backend runs an LLM
(via any Anthropic-compatible endpoint — configured here for z.ai's GLM Coding
Plan) in a tool-use loop against the **Superset MCP server**, so the model can
list datasets, run SQL, and create/modify charts and dashboards for real.

```
Browser ──► superset-chat (FastAPI + SPA)
                 │  login  ──► superset       (validates credentials)
                 │  LLM    ──► z.ai / Anthropic (GLM tool-use)
                 └─ tools  ──► superset-mcp    (list_datasets, generate_chart,
                                                generate_dashboard, execute_sql…)
```

## Run it

The service is wired into the repo's `docker-compose.yml`. From the repo root:

1. Put your z.ai key in `docker/.env-local`:

   ```env
   ANTHROPIC_AUTH_TOKEN=your_zai_api_key   # https://z.ai/manage-apikey/apikey-list
   CHAT_MODEL=glm-4.6                       # or glm-4.5 / glm-5.2
   ```

2. Bring the stack up:

   ```bash
   docker compose up -d
   ```

3. Open the chat app at **http://localhost:8090** and sign in with any
   Superset account (e.g. the local dev `admin` / `admin`).

## How it works

- **Auth** (`backend/auth.py`) — validates username/password against Superset's
  `/api/v1/security/login`, then issues a short-lived signed JWT session cookie.
  The Superset password is never stored.
- **Agent** (`backend/agent.py`) — connects to the MCP server over
  streamable-HTTP, exposes its tools to the LLM, and runs the tool-use loop,
  streaming text and tool activity back to the browser via Server-Sent Events.
- **API/UI** (`backend/main.py`, `frontend/`) — FastAPI serves the SPA and the
  `/api/login`, `/api/chat` (SSE), `/api/me`, `/api/logout` endpoints.

## Configuration

All settings are environment variables (see `backend/config.py`):

| Variable | Default | Purpose |
| --- | --- | --- |
| `ANTHROPIC_AUTH_TOKEN` | _(required)_ | LLM API key (z.ai / Anthropic). |
| `ANTHROPIC_BASE_URL` | `https://api.z.ai/api/anthropic` | LLM endpoint. |
| `CHAT_MODEL` | `glm-4.6` | Model name. |
| `SUPERSET_BASE_URL` | `http://superset:8088` | Superset (for login). |
| `MCP_URL` | `http://superset-mcp:5008/mcp` | Superset MCP tool endpoint. |
| `CHAT_SECRET_KEY` | random | Session-signing secret. |
| `CHAT_PORT` | `8090` | Exposed port. |

## Security notes (dev)

- The bundled MCP server runs **unauthenticated in dev mode** and executes all
  tool calls as `MCP_DEV_USERNAME` (default `admin`). Every chat user therefore
  acts with that user's Superset permissions. This is intended for local
  development only — do **not** expose this app publicly as configured.
- For production you would enable MCP auth and forward each user's identity.

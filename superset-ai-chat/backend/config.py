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

"""Runtime configuration for the Superset AI Chat backend.

All values are read from environment variables so the service can be
configured entirely through docker-compose / .env-local without code changes.
"""

import os
import secrets


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


# --- Superset (used for user authentication) -----------------------------
# Inside the docker-compose network Superset is reachable by service name.
SUPERSET_BASE_URL: str = _env("SUPERSET_BASE_URL", "http://superset:8088").rstrip("/")
SUPERSET_AUTH_PROVIDER: str = _env("SUPERSET_AUTH_PROVIDER", "db")

# --- MCP server (the Superset "AI agent" tool endpoint) ------------------
MCP_URL: str = _env("MCP_URL", "http://superset-mcp:5008/mcp")

# --- LLM (Anthropic-compatible endpoint, e.g. z.ai GLM Coding Plan) -------
ANTHROPIC_BASE_URL: str = _env(
    "ANTHROPIC_BASE_URL", "https://api.z.ai/api/anthropic"
).rstrip("/")
ANTHROPIC_AUTH_TOKEN: str = _env("ANTHROPIC_AUTH_TOKEN", "")
CHAT_MODEL: str = _env("CHAT_MODEL", "glm-4.6")
MAX_TOKENS: int = int(_env("CHAT_MAX_TOKENS", "8192"))
# Hard cap on agent tool-call iterations per user turn (prevents runaway loops).
MAX_TOOL_ITERATIONS: int = int(_env("CHAT_MAX_TOOL_ITERATIONS", "20"))

# --- Session / auth cookie ------------------------------------------------
# A stable secret keeps sessions valid across restarts; if unset we generate
# an ephemeral one (sessions then reset on every restart, which is fine for dev).
SECRET_KEY: str = _env("CHAT_SECRET_KEY") or secrets.token_urlsafe(48)
SESSION_TTL_SECONDS: int = int(_env("CHAT_SESSION_TTL_SECONDS", str(60 * 60 * 12)))
COOKIE_NAME: str = "superset_ai_chat_session"
# Set to "true" only when served over HTTPS.
COOKIE_SECURE: bool = _env("CHAT_COOKIE_SECURE", "false").lower() == "true"

# --- Server ---------------------------------------------------------------
HOST: str = _env("CHAT_HOST", "0.0.0.0")
PORT: int = int(_env("CHAT_PORT", "8090"))

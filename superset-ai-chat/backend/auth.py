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

"""User authentication against Superset + signed session tokens.

Login credentials are validated by calling Superset's REST login endpoint.
On success we mint a short-lived JWT stored in an httpOnly cookie -- the
chat backend never stores or reuses the user's Superset password.
"""

from __future__ import annotations

import time
from typing import Any, Optional

import httpx
import jwt

from . import config


class AuthError(Exception):
    """Raised when credentials are invalid or Superset is unreachable."""


async def verify_superset_credentials(username: str, password: str) -> dict[str, Any]:
    """Validate credentials against Superset's login API.

    Returns a small dict of user info on success. Raises AuthError otherwise.
    The user's Superset access token is used only to fetch profile info and is
    then discarded -- it is never persisted or returned to the browser.
    """
    login_url = f"{config.SUPERSET_BASE_URL}/api/v1/security/login"
    payload = {
        "username": username,
        "password": password,
        "provider": config.SUPERSET_AUTH_PROVIDER,
        "refresh": False,
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(login_url, json=payload)
            if resp.status_code == 401:
                raise AuthError("Invalid username or password.")
            if resp.status_code >= 400:
                raise AuthError(
                    f"Superset login failed (HTTP {resp.status_code})."
                )
            access_token = resp.json().get("access_token")
            if not access_token:
                raise AuthError("Superset did not return an access token.")

            # Fetch profile info (best-effort; login success already proves identity).
            display_name = username
            try:
                me = await client.get(
                    f"{config.SUPERSET_BASE_URL}/api/v1/me/",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if me.status_code == 200:
                    result = me.json().get("result", {})
                    first = result.get("first_name", "")
                    last = result.get("last_name", "")
                    full = f"{first} {last}".strip()
                    if full:
                        display_name = full
            except httpx.HTTPError:
                pass
    except httpx.HTTPError as exc:
        raise AuthError(f"Could not reach Superset: {exc}") from exc

    return {"username": username, "display_name": display_name}


def issue_session_token(user: dict[str, Any]) -> str:
    """Create a signed JWT session token for an authenticated user."""
    now = int(time.time())
    claims = {
        "sub": user["username"],
        "name": user.get("display_name", user["username"]),
        "iat": now,
        "exp": now + config.SESSION_TTL_SECONDS,
    }
    return jwt.encode(claims, config.SECRET_KEY, algorithm="HS256")


def decode_session_token(token: str) -> Optional[dict[str, Any]]:
    """Return the claims for a valid, unexpired token, else None."""
    try:
        return jwt.decode(token, config.SECRET_KEY, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None

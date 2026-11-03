"""
auth.py - Who is calling the API?

The frontend sends its Supabase access token as `Authorization: Bearer <token>`.
We never trust a user id sent by the client. Instead we ask Supabase Auth to verify
the token (GET /auth/v1/user) and use the user id Supabase returns.

Why verify with Supabase instead of checking the JWT signature ourselves:
it works with any project signing-key setup, needs no secret on the server (only the
public anon key), and rejects tokens from signed-out or deleted users.
Verified tokens are cached for 60 seconds so we don't call Supabase on every request.
"""

import hashlib
import os
import time
from dataclasses import dataclass

import requests
from dotenv import load_dotenv
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
CACHE_SECONDS = 60

bearer = HTTPBearer(auto_error=False)
_cache: dict = {}  # sha256(token) -> (expires_at, User)


@dataclass(frozen=True)
class User:
    id: str      # Supabase Auth user id, taken from Supabase's response
    email: str | None
    token: str   # the user's own access token (used so database RLS still applies)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def get_current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> User:
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        raise HTTPException(status_code=500, detail="Server auth is not configured")
    if creds is None or creds.scheme.lower() != "bearer" or not creds.credentials:
        raise _unauthorized("Not authenticated")

    token = creds.credentials
    key = hashlib.sha256(token.encode()).hexdigest()
    hit = _cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]

    try:
        r = requests.get(
            f"{SUPABASE_URL}/auth/v1/user",
            headers={"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {token}"},
            timeout=10,
        )
    except requests.RequestException:
        raise HTTPException(status_code=503, detail="Could not reach the auth service")

    if r.status_code != 200:
        raise _unauthorized("Invalid or expired token")
    data = r.json()
    if not data.get("id"):
        raise _unauthorized("Invalid or expired token")

    user = User(id=data["id"], email=data.get("email"), token=token)
    if len(_cache) > 1000:
        _cache.clear()
    _cache[key] = (time.time() + CACHE_SECONDS, user)
    return user

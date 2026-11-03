
"""
auth.py - Who is calling the API?

The frontend sends its Supabase access token as Authorization: Bearer <token>.
Supabase Auth verifies the token and returns the authenticated user's ID.
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
_cache: dict = {}


@dataclass(frozen=True)
class User:
    id: str
    email: str | None
    token: str


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=401,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"}
    )


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer)
) -> User:

    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        raise HTTPException(
            status_code=500,
            detail="Server auth is not configured"
        )

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
            headers={
                "apikey": SUPABASE_ANON_KEY,
                "Authorization": f"Bearer {token}"
            },
            timeout=10
        )

    except requests.RequestException as e:
        print("Supabase Auth connection error:", str(e))
        raise HTTPException(
            status_code=503,
            detail="Could not reach the auth service"
        )

    # Debug information: does not print the access token or API key.
    if r.status_code != 200:
        print("Supabase Auth status:", r.status_code)
        print("Supabase Auth response:", r.text[:500])
        raise _unauthorized("Invalid or expired token")

    data = r.json()

    if not data.get("id"):
        print("Supabase Auth response missing user ID")
        raise _unauthorized("Invalid or expired token")

    user = User(
        id=data["id"],
        email=data.get("email"),
        token=token
    )

    if len(_cache) > 1000:
        _cache.clear()

    _cache[key] = (time.time() + CACHE_SECONDS, user)

    return user

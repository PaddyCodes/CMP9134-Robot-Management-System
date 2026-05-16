"""
auth.py
-------
JWT authentication scaffolding for the Ground Control Station backend.

⚠  TEMPORARY SCAFFOLDING — coursework only
   Users are stored in a plain in-memory dictionary with temporary
   coursework-only plaintext credentials below.
   Before any real deployment this must be replaced with a proper
   database-backed user store (SQLAlchemy + SQLite/PostgreSQL) and
   full Role-Based Access Control (RBAC) enforcement per route.

Libraries used
~~~~~~~~~~~~~~
* python-jose  — JWT creation and verification (HMAC-SHA256).
* FastAPI      — OAuth2PasswordBearer provides the token extraction
                 dependency used by protected route handlers.

Add to requirements.txt if not already present::

    python-jose[cryptography]
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

logger = logging.getLogger(__name__)

# ── JWT configuration ──────────────────────────────────────────────────────
# SECRET_KEY signs every token.  In production this must come from an
# environment variable (e.g. generated with: openssl rand -hex 32).
# The fallback value here is safe for local development only — never
# commit a real secret key to version control.
SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "change-me-in-production-please")
ALGORITHM: str = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES: int = int(
    os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60")
)

# ── OAuth2 token extractor ─────────────────────────────────────────────────
# OAuth2PasswordBearer tells FastAPI where clients send their token.
# When a route declares Depends(oauth2_scheme), FastAPI automatically
# extracts the Bearer token from the Authorization header and passes it in.
# auto_error=True (the default) means FastAPI returns 401 automatically if
# the header is missing, so protected routes never see unauthenticated calls.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/token")


# ── Demo user store ────────────────────────────────────────────────────────
# ⚠  SCAFFOLDING — replace with a real database before production.
#
# Passwords are stored as temporary coursework-only plaintext credentials.
# In a production system these must be replaced with a secure hashing
# scheme (e.g. bcrypt) and stored in a database — never in source code.
#
# Roles defined here:
#   "commander" — can view telemetry AND send move / reset commands.
#   "viewer"    — can view telemetry only (read-only dashboard access).
DEMO_USERS: dict[str, dict[str, Any]] = {
    "commander": {
        "username": "commander",
        "password": "commander123",
        "role": "commander",
        "disabled": False,
    },
    "viewer": {
        "username": "viewer",
        "password": "viewer123",
        "role": "viewer",
        "disabled": False,
    },
}


# ── Helper functions ───────────────────────────────────────────────────────

def verify_password(plain_password: str, stored_password: str) -> bool:
    """Return True if *plain_password* matches *stored_password*.

    ⚠  TEMPORARY SCAFFOLDING — uses temporary coursework-only plaintext
    credentials via direct string comparison.
    In production this must be replaced with a constant-time comparison
    against a securely hashed value (e.g. bcrypt) to prevent both password
    exposure and timing-based side-channel attacks.

    Args:
        plain_password:  The raw password supplied by the user at login.
        stored_password: The plaintext password stored in the demo user record.

    Returns:
        True if the passwords match, False otherwise.
    """
    return plain_password == stored_password


def authenticate_user(username: str, password: str) -> dict[str, Any] | None:
    """Look up *username* in the demo store and verify *password*.

    Returns the user dict on success, or ``None`` if the username does not
    exist, the password is wrong, or the account is disabled.

    Callers should treat all three failure modes identically in their
    HTTP responses to avoid leaking whether a particular username exists
    (a common security mistake known as username enumeration).

    Args:
        username: Username submitted via the login form.
        password: Plain-text password submitted via the login form.

    Returns:
        The matching user dict, or ``None`` on any failure.
    """
    user = DEMO_USERS.get(username)
    if not user:
        return None
    if not verify_password(password, user["password"]):
        return None
    if user.get("disabled"):
        return None
    return user


def create_access_token(data: dict[str, Any]) -> str:
    """Create a signed JWT containing *data* as the payload claims.

    Automatically adds an ``exp`` (expiration) claim based on
    ``ACCESS_TOKEN_EXPIRE_MINUTES`` so tokens cannot be used indefinitely.
    The token is signed with ``SECRET_KEY`` using the ``HS256`` algorithm.

    Args:
        data: Claims to embed in the token payload.  Typically::

                {"sub": username, "role": role}

              ``sub`` (subject) is the standard JWT claim for the user
              identifier.

    Returns:
        A URL-safe JWT string ready to be returned to the client.
    """
    payload = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload["exp"] = expire
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


async def get_current_user(token: str = Depends(oauth2_scheme)) -> dict[str, Any]:
    """FastAPI dependency — decode and validate the Bearer JWT.

    Designed to be injected into protected route handlers via
    ``Depends(get_current_user)``.  FastAPI calls this automatically
    before the route handler runs; if it raises, the handler is never reached.

    Raises:
        HTTPException 401: If the token is missing, expired, tampered with,
            or the ``sub`` claim does not match a known user.

    Returns:
        The user dict from ``DEMO_USERS`` for the authenticated user.

    Example usage in a route::

        @app.get("/api/me")
        async def me(user = Depends(get_current_user)):
            return {"username": user["username"], "role": user["role"]}
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str | None = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        logger.warning("JWT decode failed — token may be expired or tampered with")
        raise credentials_exception

    user = DEMO_USERS.get(username)
    if user is None or user.get("disabled"):
        raise credentials_exception

    return user

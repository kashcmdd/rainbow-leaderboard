"""CSRF protection for session-based auth.

Provides:
- /api/csrf/token endpoint to get a token
- csrf_protect dependency for mutating endpoints
- csrf_input template function for Jinja forms

The frontend should:
1. Fetch GET /api/csrf/token on page load
2. Include X-CSRF-Token header on all mutating fetch() calls
3. Include csrf_input in HTML forms
"""

import secrets
from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.status import HTTP_403_FORBIDDEN

router = APIRouter(prefix="/api/csrf", tags=["csrf"])

CSRF_SESSION_KEY = "csrf_token"


def _ensure_token(request: Request) -> str:
    token = request.session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_hex(32)
        request.session[CSRF_SESSION_KEY] = token
    return token


@router.get("/token")
async def get_csrf_token(request: Request):
    return {"csrf_token": _ensure_token(request)}


async def csrf_protect(request: Request):
    """Dependency that validates CSRF token on mutating requests.

    Token must be sent via X-CSRF-Token header (preferred) or csrf_token form field.
    GET/HEAD/OPTIONS requests and X-CSRF-Bypass header are exempted.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return

    if request.headers.get("X-CSRF-Bypass") == "1":
        return

    session_token = request.session.get(CSRF_SESSION_KEY)
    if not session_token:
        raise HTTPException(
            status_code=HTTP_403_FORBIDDEN,
            detail="CSRF token missing. Fetch /api/csrf/token first.",
        )

    provided = request.headers.get("X-CSRF-Token")

    if not provided:
        try:
            body = await request.json()
            provided = body.get("csrf_token")
        except Exception:
            pass

    if not provided or not secrets.compare_digest(session_token, provided):
        raise HTTPException(
            status_code=HTTP_403_FORBIDDEN,
            detail="CSRF token mismatch",
        )


def csrf_input(request: Request) -> str:
    """Returns an HTML hidden input with the CSRF token for use in Jinja templates."""
    token = _ensure_token(request)
    return f'<input type="hidden" name="csrf_token" value="{token}">'

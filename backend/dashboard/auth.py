"""
backend/dashboard/auth.py

Shared-credential auth for the dashboard. Scan findings reveal an app's actual
vulnerabilities, so the API is not public by default.

Two modes (see DashboardSettings.auth_mode):
  - "token": Authorization: Bearer <GUARDOPS_DASHBOARD_TOKEN>
  - "basic": HTTP Basic against GUARDOPS_DASHBOARD_USER / _PASSWORD
  - "none":  no auth (explicit opt-out)

Auth is enforced only when a credential is actually configured (settings.auth_enabled).
That keeps local `guardops dashboard` frictionless, while the in-cluster Secret makes
it mandatory in production. Comparisons use secrets.compare_digest to avoid timing
side channels.
"""

import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBasic,
    HTTPBasicCredentials,
    HTTPBearer,
)

from backend.dashboard.settings import DashboardSettings


def make_auth_dependency(settings: DashboardSettings):
    """Return a FastAPI dependency enforcing the configured auth mode.

    When no credential is configured the dependency is a no-op (open) so the
    dashboard still runs locally; /meta reports auth_enabled=false so this is visible.
    """
    mode = settings.auth_mode

    if not settings.auth_enabled:
        async def _open() -> None:
            return None
        return _open

    if mode == "basic":
        basic = HTTPBasic(auto_error=True)

        def _check_basic(creds: HTTPBasicCredentials = Depends(basic)) -> None:
            ok_user = secrets.compare_digest(creds.username, settings.basic_user)
            ok_pass = secrets.compare_digest(creds.password, settings.basic_password)
            if not (ok_user and ok_pass):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid credentials",
                    headers={"WWW-Authenticate": "Basic"},
                )

        return _check_basic

    # default: bearer token
    bearer = HTTPBearer(auto_error=True)

    def _check_token(creds: HTTPAuthorizationCredentials = Depends(bearer)) -> None:
        if not secrets.compare_digest(creds.credentials, settings.auth_token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing token",
            )

    return _check_token

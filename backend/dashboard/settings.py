"""
backend/dashboard/settings.py

Resolves the dashboard's runtime configuration from .guardops.yaml (when present)
overlaid with environment variables. The in-cluster pod has no .guardops.yaml, so
every setting must also be configurable purely by env — the Deployment sets them
from a ConfigMap/Secret.

Precedence for each value: explicit env var > .guardops.yaml > built-in default.

The full `config` dict is kept on the settings object because the metadata store
factory (backend.metadata.factory.get_store) and the ArgoCD helpers consume it
directly — env overrides for those are merged back into `config` here so a single
get_store(settings.config) call sees them.
"""

import copy
import os
from dataclasses import dataclass, field

# In-cluster service DNS defaults (used when nothing else is configured). These
# resolve inside EKS; for local `guardops dashboard` set the GUARDOPS_*_URL envs
# or rely on graceful "unavailable" degradation.
DEFAULT_PROMETHEUS_URL = "http://kube-prometheus-stack-prometheus.monitoring.svc.cluster.local:9090"
DEFAULT_LOKI_URL = "http://loki.monitoring.svc.cluster.local:3100"

# The dashboard SPA (Vite/React) is served from a different origin than this API
# (e.g. https://guardops.live -> https://app.guardops.live), so cross-origin
# requests need CORS. These defaults cover the production SPA + the Vite dev/preview
# servers; override with GUARDOPS_DASHBOARD_CORS_ORIGINS (comma-separated) or
# dashboard.cors_origins. Local dev via the Vite proxy is same-origin and unaffected.
DEFAULT_CORS_ORIGINS = [
    "https://dashboard.guardops.live",  # the Vercel SPA (primary frontend origin)
    "https://guardops.live",
    "https://www.guardops.live",
    "http://localhost:5173",
    "http://localhost:4173",
]


def _env(*names: str, default: str = "") -> str:
    """First non-empty value among the given environment variable names."""
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return default


@dataclass
class DashboardSettings:
    project_name: str
    config: dict
    prometheus_url: str
    loki_url: str
    argocd_url: str
    argocd_token: str
    auth_mode: str            # "token" | "basic" | "none"
    auth_token: str
    basic_user: str
    basic_password: str
    cors_origins: list[str] = field(default_factory=list)
    cors_origin_regex: str = ""

    @property
    def auth_enabled(self) -> bool:
        """Auth is only enforced when a credential is actually configured. This keeps
        local `guardops dashboard` frictionless while the in-cluster Secret enforces it."""
        if self.auth_mode == "none":
            return False
        if self.auth_mode == "basic":
            return bool(self.basic_user and self.basic_password)
        return bool(self.auth_token)


def load_settings(config: dict | None = None) -> DashboardSettings:
    """Build DashboardSettings from config + environment.

    `config` is loaded from .guardops.yaml when present; otherwise the built-in
    defaults are used (the in-cluster case). Never exits — unlike load_config().
    """
    if config is None:
        config = _load_config_or_defaults()
    else:
        config = copy.deepcopy(config)

    dash = config.get("dashboard", {}) or {}
    monitoring = config.get("monitoring", {}) or {}
    argocd = config.get("argocd", {}) or {}

    # ── Metadata backend env overrides (so get_store sees them in-cluster) ──────
    meta = config.setdefault("metadata", {})
    if os.environ.get("GUARDOPS_METADATA_BACKEND"):
        meta["backend"] = os.environ["GUARDOPS_METADATA_BACKEND"]
    if os.environ.get("GUARDOPS_S3_BUCKET"):
        meta["s3_bucket"] = os.environ["GUARDOPS_S3_BUCKET"]
    if os.environ.get("GUARDOPS_S3_PREFIX"):
        meta["s3_prefix"] = os.environ["GUARDOPS_S3_PREFIX"]
    if os.environ.get("GUARDOPS_AWS_REGION"):
        meta["region"] = os.environ["GUARDOPS_AWS_REGION"]

    # ── ArgoCD token: env var named by argocd.token_env_var, then explicit env ──
    token_env_var = argocd.get("token_env_var", "ARGOCD_TOKEN")
    argocd_token = _env("GUARDOPS_ARGOCD_TOKEN", token_env_var)

    cors = dash.get("cors_origins") or list(DEFAULT_CORS_ORIGINS)
    if os.environ.get("GUARDOPS_DASHBOARD_CORS_ORIGINS"):
        cors = [o.strip() for o in os.environ["GUARDOPS_DASHBOARD_CORS_ORIGINS"].split(",") if o.strip()]
    cors_regex = _env("GUARDOPS_DASHBOARD_CORS_ORIGIN_REGEX", default=dash.get("cors_origin_regex", ""))

    return DashboardSettings(
        project_name=_env(
            "GUARDOPS_PROJECT",
            default=(config.get("project", {}) or {}).get("name", "") or "guardops-app",
        ),
        config=config,
        prometheus_url=_env("GUARDOPS_PROMETHEUS_URL", default=monitoring.get("prometheus_url") or DEFAULT_PROMETHEUS_URL),
        loki_url=_env("GUARDOPS_LOKI_URL", default=monitoring.get("loki_url") or DEFAULT_LOKI_URL),
        argocd_url=_env("GUARDOPS_ARGOCD_URL", default=argocd.get("url", "")),
        argocd_token=argocd_token,
        auth_mode=_env("GUARDOPS_DASHBOARD_AUTH_MODE", default=dash.get("auth_mode", "token")),
        auth_token=_env("GUARDOPS_DASHBOARD_TOKEN"),
        basic_user=_env("GUARDOPS_DASHBOARD_USER"),
        basic_password=_env("GUARDOPS_DASHBOARD_PASSWORD"),
        cors_origins=cors,
        cors_origin_regex=cors_regex,
    )


def _load_config_or_defaults() -> dict:
    """Load .guardops.yaml if it exists, else a deep copy of DEFAULT_CONFIG.
    Avoids load_config()'s sys.exit so the in-cluster pod (no config file) starts."""
    from cli.utils.config import DEFAULT_CONFIG, config_exists, load_config
    if config_exists():
        return load_config()
    return copy.deepcopy(DEFAULT_CONFIG)

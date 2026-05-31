"""
cli/utils/config.py

Manages reading and writing the .guardops.yaml project config file.

WHY YAML: Human-readable, supports comments, widely used in DevOps
(Kubernetes, Helm, Docker Compose all use YAML). JSON doesn't support
comments, which hurts developer experience.

The config file (.guardops.yaml) is the "memory" of a GuardOps project.
It stores the project name, cloud settings, K8s namespace, etc.
Every command reads this file to know how to behave.

Phase 7 changes:
  - Added runtime_security section to DEFAULT_CONFIG
  - New keys: enabled, falco_alert_window, alert_fail_on, namespaces_to_watch

Phase 9 changes:
  - Added environments section to DEFAULT_CONFIG (staging + prod overrides)
  - New helper: get_env_config(config, env) — merges base config with env overrides
  - New helper: resolve_namespace(config, env) — convenience wrapper
  - New helper: resolve_image_tag(base_tag, env, config) — adds env prefix (staging-<sha>)

Phase 10 changes:
  - Added domain field to each environment block in DEFAULT_CONFIG
      staging -> staging.guardops.live
      prod    -> guardops.live
  - Added argocd section to DEFAULT_CONFIG
      url, app_name_staging, app_name_prod, token_env_var
  - New helper: resolve_domain(config, env) — returns the public domain for an env
  - New helper: get_argocd_app_name(config, env) — returns the ArgoCD Application name
"""

import copy
import sys
from pathlib import Path
from typing import Any

import yaml

from cli.utils.output import error

# The config file is always in the current working directory.
CONFIG_FILENAME = ".guardops.yaml"


# ─── Default config structure ─────────────────────────────────────────────────
DEFAULT_CONFIG: dict[str, Any] = {
    "project": {
        "name": "",           # Project name (e.g., "my-api")
        "description": "",    # Optional description
        "cloud": "local",     # "local" | "aws" | "gcp" | "azure"
    },
    "docker": {
        "dockerfile": "Dockerfile",
        "context": ".",
        "registry": "",
        "image_name": "",
    },
    "kubernetes": {
        "namespace": "default",
        "cluster_context": "",
        "deployment_name": "",
    },
    "security": {
        "fail_on_severity": "HIGH",
        "tools": {
            "semgrep": True,
            "bandit": True,
            "trivy": True,
            "sonarqube": False,
            "owasp_zap": False,
        },
    },
    "monitoring": {
        "grafana_url": "",
        "prometheus_url": "",
        "loki_url": "",
    },
    # ── Phase 7: Runtime Security ─────────────────────────────────────────────
    "runtime_security": {
        "enabled": False,
        "falco_alert_window": "1h",
        "alert_fail_on": "CRITICAL",
        "namespaces_to_watch": [],
    },
    # ── Phase 8: Self-Healing ─────────────────────────────────────────────────
    "self_healing": {
        "enabled": False,
        "webhook_url": "",
    },
    # ── Phase 9: Multi-Environment ────────────────────────────────────────────
    # Each environment block is a PARTIAL config that is deep-merged on top of
    # the base config when that environment is active. You only need to specify
    # keys that differ from the base; everything else inherits from the top level.
    #
    # Phase 10 addition: each env block gains a "domain" key so commands like
    # `guardops sync-status` and the DAST step can resolve the live URL without
    # requiring security.zap_target_url to be set manually.
    #
    # Usage in .guardops.yaml:
    #
    #   environments:
    #     staging:
    #       domain: staging.guardops.live   # override if using a different domain
    #       kubernetes:
    #         namespace: staging
    #     prod:
    #       domain: guardops.live
    #
    "environments": {
        "staging": {
            "kubernetes": {
                # Staging pods live in their own namespace so they can never
                # share NetworkPolicies, RBAC, or quarantine state with prod.
                "namespace": "staging",
            },
            "docker": {
                # Image tags are prefixed so ECR makes it obvious which images
                # are staging builds: staging-abc1234 vs plain abc1234 for prod.
                "image_tag_prefix": "staging",
            },
            "security": {
                # Staging uses the same SAST gate as prod — we still want to
                # catch HIGH findings before they reach prod.
                "fail_on_severity": "HIGH",
                "tools": {
                    # DAST is disabled for staging: the staging cluster has no
                    # stable public URL and the ZAP passive scan is already run
                    # against prod on every deploy.
                    "owasp_zap": False,
                },
            },
            "helm": {
                # Appended to the Helm release name so staging and prod releases
                # can coexist in separate namespaces without name collisions.
                "release_suffix": "-staging",
            },
            # ── Phase 10: Public domain for this environment ───────────────────
            # Used by resolve_domain() so deploy_cmd and sync_cmd can construct
            # the live URL without manual config. Override in .guardops.yaml if
            # your domain differs from the default guardops.live setup.
            "domain": "staging.guardops.live",
        },
        "prod": {
            "kubernetes": {
                "namespace": "default",
            },
            "docker": {
                # Prod images carry no prefix — the plain SHA is unambiguous.
                "image_tag_prefix": "",
            },
            "security": {
                "fail_on_severity": "HIGH",
                "tools": {
                    "owasp_zap": True,
                },
            },
            "helm": {
                "release_suffix": "",
            },
            # ── Phase 10 ──────────────────────────────────────────────────────
            "domain": "guardops.live",
        },
        # "local" environment inherits everything from the base config.
        # No overrides needed — k3d handles its own quirks via the env flag in
        # deploy_cmd (imagePullPolicy=Never, no ECR push, no ZAP).
        "local": {
            "docker": {
                "image_tag_prefix": "",
            },
            "helm": {
                "release_suffix": "",
            },
            "domain": "test-app.local",
        },
    },
    # ── Phase 10: ArgoCD GitOps ───────────────────────────────────────────────
    #
    # Connection settings for the ArgoCD REST API, used by:
    #   - guardops sync-status   (query Application health and sync state)
    #   - backend/pipeline/gitops_writer.py (trigger sync + poll until healthy)
    #   - .github/workflows/ci.yaml  sync-gate job
    #
    # The ArgoCD API token is NEVER stored here — it is always read from the
    # environment variable named by token_env_var. Store it as a GitHub secret
    # (ARGOCD_TOKEN) or in your shell environment for local use.
    #
    # After applying the argocd Terraform module, run:
    #   terraform output argocd_server_url
    # and set argocd.url to that value in .guardops.yaml.
    #
    # ArgoCD Application names follow the pattern guardops-app-<env>
    # and are created by the argocd Terraform module (argocd/main.tf).
    #
    "argocd": {
        "url": "https://argocd.guardops.live",  # ArgoCD server URL (Phase 10)
        "app_name_staging": "guardops-app-staging",
        "app_name_prod":    "guardops-app-prod",
        # Name of the environment variable that holds the ArgoCD API token.
        # Never put the actual token here — only the env var name.
        "token_env_var":    "ARGOCD_TOKEN",
    },
}


def get_config_path() -> Path:
    """Returns the absolute path to .guardops.yaml in the current directory."""
    return Path.cwd() / CONFIG_FILENAME


def config_exists() -> bool:
    """Returns True if .guardops.yaml exists in current directory."""
    return get_config_path().exists()


def load_config() -> dict[str, Any]:
    """
    Reads .guardops.yaml and returns it as a Python dictionary.

    Exits with a helpful error if the file doesn't exist.
    yaml.safe_load() is safer than yaml.load() — it doesn't execute
    arbitrary Python objects embedded in YAML.

    Returns:
        Dict containing the project configuration.
    """
    config_path = get_config_path()

    if not config_path.exists():
        error(
            f"No [cyan]{CONFIG_FILENAME}[/cyan] found in [bold]{Path.cwd()}[/bold].\n"
            f"   Run [bold green]guardops init[/bold green] first to set up your project."
        )
        sys.exit(1)

    try:
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        error(f"{CONFIG_FILENAME} contains invalid YAML: {e}")
        sys.exit(1)

    if not config:
        error(f"{CONFIG_FILENAME} is empty. Run [bold]guardops init[/bold] to regenerate it.")
        sys.exit(1)

    return config


def save_config(config: dict[str, Any]) -> None:
    """
    Writes a config dict to .guardops.yaml.

    yaml.dump() converts a Python dict to YAML string.
    default_flow_style=False → block style, not inline {a: b} style.
    sort_keys=False          → keeps keys in the order we defined them.
    """
    config_path = get_config_path()

    with open(config_path, "w") as f:
        yaml.dump(
            config,
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
            indent=2,
        )


def get_project_name(config: dict) -> str:
    """Safely retrieves project name from config."""
    return config.get("project", {}).get("name", "unknown")


def merge_with_defaults(user_config: dict) -> dict:
    """
    Merges user config with defaults so missing keys don't cause KeyError.

    Performs a two-level deep merge: top-level sections (docker, kubernetes,
    security …) are merged key-by-key so a user can omit any individual key
    and the default fills in silently.

    Example:
        defaults  = {"a": {"x": 1, "y": 2}}
        user      = {"a": {"x": 99}}
        result    = {"a": {"x": 99, "y": 2}}   <- y filled from defaults
    """
    # deepcopy so sections the user omits don't alias (and later mutate) the
    # module-global DEFAULT_CONFIG — a plain .copy() shares nested dicts.
    result = copy.deepcopy(DEFAULT_CONFIG)
    for key, value in user_config.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = {**result[key], **value}
        else:
            result[key] = value
    return result


# ── Phase 9: Environment helpers ──────────────────────────────────────────────

def get_env_config(config: dict, env: str) -> dict:
    """
    Returns a merged config dict with environment-specific overrides applied.

    The base config (from .guardops.yaml) is the foundation. The environment
    block (config["environments"][env]) is deep-merged on top. This means:
      - Keys present in the env block override the base.
      - Keys absent from the env block keep the base value.
      - Nested sections (kubernetes, security.tools …) are merged one level deep.

    The returned dict is a fresh copy — the original config is never mutated.

    Args:
        config: Full config dict from load_config().
        env:    "local" | "staging" | "prod"

    Returns:
        Merged config dict with env overrides applied.

    Example:
        Base config has  kubernetes.namespace = "default"
        Staging env has  kubernetes.namespace = "staging"
        get_env_config(config, "staging")["kubernetes"]["namespace"] == "staging"
    """
    merged = {k: v for k, v in config.items()}

    user_envs    = config.get("environments", {})
    default_envs = DEFAULT_CONFIG.get("environments", {})

    env_overrides: dict = {}
    if env in default_envs:
        env_overrides = {**default_envs[env]}
    if env in user_envs:
        for section_key, section_val in user_envs[env].items():
            if section_key in env_overrides and isinstance(env_overrides[section_key], dict) and isinstance(section_val, dict):
                env_overrides[section_key] = {**env_overrides[section_key], **section_val}
            else:
                env_overrides[section_key] = section_val

    for section_key, override_val in env_overrides.items():
        if section_key in merged and isinstance(merged[section_key], dict) and isinstance(override_val, dict):
            merged_section = {**merged[section_key]}
            for k, v in override_val.items():
                if k in merged_section and isinstance(merged_section[k], dict) and isinstance(v, dict):
                    merged_section[k] = {**merged_section[k], **v}
                else:
                    merged_section[k] = v
            merged[section_key] = merged_section
        else:
            merged[section_key] = override_val

    return merged


def resolve_namespace(config: dict, env: str | None = None) -> str:
    """
    Returns the Kubernetes namespace for the given environment.

    Resolution order:
      1. config["environments"][env]["kubernetes"]["namespace"]  (env-specific)
      2. config["kubernetes"]["namespace"]                       (base config)
      3. "default"                                               (hard fallback)

    Args:
        config: Full config dict from load_config().
        env:    "local" | "staging" | "prod" — pass None to skip env lookup.

    Returns:
        Namespace string, e.g. "staging" or "default".
    """
    if env:
        env_cfg = get_env_config(config, env)
        ns = env_cfg.get("kubernetes", {}).get("namespace", "")
        if ns:
            return ns
    return config.get("kubernetes", {}).get("namespace", "default")


def resolve_image_tag(base_tag: str, env: str, config: dict) -> str:
    """
    Returns the image tag for a given environment.

    For staging builds the tag is prefixed so ECR listings make the
    environment of each image obvious at a glance:
      staging -> "staging-abc1234"
      prod    -> "abc1234"
      local   -> "abc1234"

    The prefix is configurable via environments.<env>.docker.image_tag_prefix
    in .guardops.yaml. An empty string means no prefix (prod / local default).

    Args:
        base_tag: The raw git SHA short tag (e.g. "abc1234") or "latest".
        env:      "local" | "staging" | "prod"
        config:   Full config dict from load_config().

    Returns:
        Final image tag string.
    """
    env_cfg = get_env_config(config, env)
    prefix = env_cfg.get("docker", {}).get("image_tag_prefix", "")
    if prefix:
        return f"{prefix}-{base_tag}"
    return base_tag


def resolve_helm_release_name(
    project_name: str,
    env: str,
    config: dict,
    slot: str | None = None,
) -> str:
    """
    Returns the Helm release name for a given environment and optional blue-green slot.

    Release name format:
      local:                  {project_name}                     e.g. guardops-app
      staging (no slot):      {project_name}-staging             e.g. guardops-app-staging
      prod (no slot):         {project_name}                     e.g. guardops-app
      staging + slot=blue:    {project_name}-staging-blue        e.g. guardops-app-staging-blue
      staging + slot=green:   {project_name}-staging-green       e.g. guardops-app-staging-green

    Keeping prod at {project_name} preserves backward compatibility with the
    existing prod Helm release — no rename migration needed.

    Args:
        project_name: From config["project"]["name"], e.g. "guardops-app".
        env:          "local" | "staging" | "prod"
        config:       Full config dict.
        slot:         "blue" | "green" | None

    Returns:
        Sanitised Helm release name string.
    """
    env_cfg = get_env_config(config, env)
    suffix = env_cfg.get("helm", {}).get("release_suffix", "")

    release_name = f"{project_name}{suffix}"
    if slot:
        release_name = f"{release_name}-{slot}"

    release_name = release_name.lower().replace("_", "-")
    if len(release_name) > 53:
        release_name = release_name[:53].rstrip("-")

    return release_name


# ── Phase 10: Domain + ArgoCD helpers ────────────────────────────────────────

def resolve_domain(config: dict, env: str) -> str:
    """
    Returns the public-facing domain for the given environment.

    Resolution order:
      1. config["environments"][env]["domain"]   (user override in .guardops.yaml)
      2. DEFAULT_CONFIG["environments"][env]["domain"]  (built-in default)
      3. ""  (empty string — caller must handle the missing-domain case)

    This is used by:
      - deploy_cmd.py  Step 5 DAST: resolves the ZAP target URL automatically
        when security.zap_target_url is not explicitly set.
      - sync_cmd.py:  constructs the ArgoCD UI deep-link for the terminal output.

    Args:
        config: Full config dict from load_config().
        env:    "local" | "staging" | "prod"

    Returns:
        Domain string, e.g. "guardops.live" or "staging.guardops.live".
        Returns "" if not configured — callers should warn and fall back.

    Example:
        resolve_domain(config, "prod")    -> "guardops.live"
        resolve_domain(config, "staging") -> "staging.guardops.live"
        resolve_domain(config, "local")   -> "test-app.local"
    """
    # User-defined env block wins
    user_domain = (
        config.get("environments", {})
              .get(env, {})
              .get("domain", "")
    )
    if user_domain:
        return user_domain

    # Fall back to DEFAULT_CONFIG built-ins
    return (
        DEFAULT_CONFIG.get("environments", {})
                      .get(env, {})
                      .get("domain", "")
    )


def get_argocd_app_name(config: dict, env: str) -> str:
    """
    Returns the ArgoCD Application name for the given environment.

    Reads from config["argocd"]["app_name_<env>"] (set in .guardops.yaml
    or filled from DEFAULT_CONFIG). Falls back to "guardops-app-<env>" if
    the argocd section is absent — matches the names created by the
    argocd Terraform module.

    Args:
        config: Full config dict from load_config().
        env:    "staging" | "prod"

    Returns:
        ArgoCD Application name string.

    Example:
        get_argocd_app_name(config, "prod")    -> "guardops-app-prod"
        get_argocd_app_name(config, "staging") -> "guardops-app-staging"
    """
    argocd_cfg = config.get("argocd", DEFAULT_CONFIG["argocd"])
    key = f"app_name_{env}"
    return argocd_cfg.get(key, f"guardops-app-{env}")


def get_argocd_url(config: dict) -> str:
    """
    Returns the ArgoCD server URL from config.

    Returns "" if not set — callers (sync_cmd) handle the missing case
    with a clear error message pointing to the setup docs.

    Args:
        config: Full config dict from load_config().

    Returns:
        URL string, e.g. "https://argocd.guardops.live", or "".
    """
    return config.get("argocd", {}).get("url", "")


def get_argocd_token_env_var(config: dict) -> str:
    """
    Returns the name of the env var that holds the ArgoCD API token.

    Defaults to "ARGOCD_TOKEN" — the GitHub secret name used in ci.yaml.

    Args:
        config: Full config dict from load_config().

    Returns:
        Environment variable name string.
    """
    return (
        config.get("argocd", {})
              .get("token_env_var", DEFAULT_CONFIG["argocd"]["token_env_var"])
    )

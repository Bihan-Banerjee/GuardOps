"""
backend/dashboard/sources/sync.py

ArgoCD Application sync + health, reusing backend.pipeline.gitops_writer.get_app_status
(the same call `guardops sync-status` makes). Missing URL/token or an unreachable
ArgoCD API → available:False.
"""

from backend.pipeline.gitops_writer import get_app_status


def sync_status(settings, env: str = "prod") -> dict:
    if not settings.argocd_url:
        return {"available": False, "reason": "argocd_url not configured"}
    if not settings.argocd_token:
        return {"available": False, "reason": "ArgoCD token not configured"}

    from cli.utils.config import get_argocd_app_name
    app_name = get_argocd_app_name(settings.config, env)

    result = get_app_status(app_name, settings.argocd_url, settings.argocd_token)
    if not result.success:
        return {"available": False, "reason": result.error_message, "app": app_name, "env": env}

    return {
        "available": True,
        "env": env,
        "app": app_name,
        "sync_status": result.sync_status,
        "health_status": result.health_status,
        "revision": result.revision,
    }

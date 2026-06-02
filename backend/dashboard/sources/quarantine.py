"""
backend/dashboard/sources/quarantine.py

Self-healing state: pods currently isolated by the Phase 8 NetworkPolicy
quarantine, plus the managed quarantine policies. Reads via kubectl JSON.

NOTE: this deliberately does NOT use cli.utils.system.run_command — that helper
calls sys.exit() when kubectl is missing or times out, which would take down the
web server. Here every failure returns {"available": False, "reason": ...}.
"""

import json
import shutil
import subprocess

_MANAGED_SELECTOR = "guardops.io/managed-by=guardops"
_QUARANTINE_SELECTOR = "guardops.io/quarantine=true"


def _kubectl_json(args: list[str], timeout: int = 15) -> dict:
    proc = subprocess.run(
        ["kubectl", *args],
        capture_output=True, text=True, encoding="utf-8", timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "kubectl failed").strip())
    return json.loads(proc.stdout or "{}")


def quarantine_status(namespace=None, all_namespaces: bool = False) -> dict:
    if shutil.which("kubectl") is None:
        return {"available": False, "reason": "kubectl not found on PATH"}

    scope = ["-A"] if all_namespaces else (["-n", namespace] if namespace else [])
    try:
        netpols = _kubectl_json(
            ["get", "networkpolicy", "-l", _MANAGED_SELECTOR, "-o", "json", *scope]
        )
        pods = _kubectl_json(
            ["get", "pods", "-l", _QUARANTINE_SELECTOR, "-o", "json", *scope]
        )
    except Exception as e:  # noqa: BLE001 - cluster down / no perms / kubectl error
        return {"available": False, "reason": str(e)}

    policies = [
        {
            "name": p.get("metadata", {}).get("name", ""),
            "namespace": p.get("metadata", {}).get("namespace", ""),
            "created": p.get("metadata", {}).get("creationTimestamp", ""),
        }
        for p in netpols.get("items", [])
    ]
    quarantined = [
        {
            "name": p.get("metadata", {}).get("name", ""),
            "namespace": p.get("metadata", {}).get("namespace", ""),
            "phase": p.get("status", {}).get("phase", ""),
            "node": p.get("spec", {}).get("nodeName", ""),
        }
        for p in pods.get("items", [])
    ]
    return {"available": True, "policies": policies, "pods": quarantined}

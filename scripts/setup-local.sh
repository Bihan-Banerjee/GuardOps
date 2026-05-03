#!/usr/bin/env bash
# scripts/setup-local.sh
#
# One-command local environment bootstrap for Phase 3.
#
# What this does:
#   1. Recreates k3d cluster with proper port mappings (fixes Windows curl issue)
#   2. Installs Nginx Ingress Controller
#   3. Installs cert-manager (TLS in prod — skipped locally)
#   4. Adds test-app.local to your hosts file
#   5. Verifies everything is ready
#
# Usage:
#   bash scripts/setup-local.sh
#
# Prerequisites: k3d, kubectl, helm must be on PATH

set -euo pipefail

CLUSTER_NAME="guardops-local"
INGRESS_NAMESPACE="ingress-nginx"
APP_HOST="test-app.local"

echo ""
echo "========================================"
echo "  GuardOps — Local Environment Setup"
echo "========================================"
echo ""

# ── Step 1: Recreate k3d cluster with port mappings ────────────────────────
echo "[1/5] Setting up k3d cluster: ${CLUSTER_NAME}"

if k3d cluster list | grep -q "${CLUSTER_NAME}"; then
  echo "  Deleting existing cluster..."
  k3d cluster delete "${CLUSTER_NAME}"
fi

echo "  Creating cluster with port mappings..."
k3d cluster create "${CLUSTER_NAME}" \
  --port "80:80@loadbalancer" \
  --port "443:443@loadbalancer" \
  --port "30000-32767:30000-32767@server:0" \
  --wait

echo "  Cluster created."
kubectl get nodes

# ── Step 2: Install Nginx Ingress Controller ────────────────────────────────
echo ""
echo "[2/5] Installing Nginx Ingress Controller..."

helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx 2>/dev/null || true
helm repo update

helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx \
  --namespace "${INGRESS_NAMESPACE}" \
  --create-namespace \
  --set controller.service.type=LoadBalancer \
  --set controller.hostPort.enabled=true \
  --wait \
  --timeout 3m

echo "  Nginx Ingress ready."

# ── Step 3: Install cert-manager (optional, for TLS) ───────────────────────
echo ""
echo "[3/5] Installing cert-manager..."

helm repo add jetstack https://charts.jetstack.io 2>/dev/null || true
helm repo update

helm upgrade --install cert-manager jetstack/cert-manager \
  --namespace cert-manager \
  --create-namespace \
  --set installCRDs=true \
  --wait \
  --timeout 3m

echo "  cert-manager ready."

# ── Step 4: Add hosts file entry ────────────────────────────────────────────
echo ""
echo "[4/5] Hosts file setup"

HOSTS_LINE="127.0.0.1  ${APP_HOST}"

if grep -q "${APP_HOST}" /etc/hosts 2>/dev/null; then
  echo "  ${APP_HOST} already in /etc/hosts — skipping"
else
  echo ""
  echo "  Add this line to your hosts file:"
  echo "    ${HOSTS_LINE}"
  echo ""
  if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" || -n "${WINDIR:-}" ]]; then
    echo "  Windows: edit C:\\Windows\\System32\\drivers\\etc\\hosts as Administrator"
  else
    echo "  Run: echo '${HOSTS_LINE}' | sudo tee -a /etc/hosts"
  fi
fi

# ── Step 5: Verify ──────────────────────────────────────────────────────────
echo ""
echo "[5/5] Verifying setup..."

kubectl get nodes
kubectl get pods -n "${INGRESS_NAMESPACE}"
kubectl get pods -n cert-manager

echo ""
echo "========================================"
echo "  Setup complete!"
echo ""
echo "  Next steps:"
echo "    1. Add '127.0.0.1 ${APP_HOST}' to your hosts file"
echo "    2. Run: guardops deploy"
echo "    3. Open: http://${APP_HOST}"
echo "========================================"
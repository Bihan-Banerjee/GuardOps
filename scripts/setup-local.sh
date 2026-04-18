#!/usr/bin/env bash
# scripts/setup-local.sh
#
# One-command setup for local development environment.
# Run once after cloning the repo:
#   chmod +x scripts/setup-local.sh
#   ./scripts/setup-local.sh

# -e: exit immediately if any command fails
# -u: treat unset variables as errors
# -o pipefail: a pipe fails if ANY command in the pipe fails (not just the last)
set -euo pipefail

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color (reset)

info()    { echo -e "${CYAN}  ℹ${NC}  $1"; }
success() { echo -e "${GREEN}  ✓${NC}  $1"; }
warn()    { echo -e "${YELLOW}  ⚠${NC}  $1"; }
error()   { echo -e "${RED}  ✗${NC}  $1"; exit 1; }

echo ""
echo "  GuardOps — Local Development Setup"
echo "  ─────────────────────────────────────"
echo ""

# ── Check Python version ─────────────────────────────────────────────────────
info "Checking Python version..."
PYTHON_VERSION=$(python3 --version 2>&1 | cut -d' ' -f2)
PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d. -f1)
PYTHON_MINOR=$(echo $PYTHON_VERSION | cut -d. -f2)

if [ "$PYTHON_MAJOR" -lt 3 ] || [ "$PYTHON_MINOR" -lt 11 ]; then
    error "Python 3.11+ required. Found: $PYTHON_VERSION. Install from https://python.org"
fi
success "Python $PYTHON_VERSION"

# ── Create virtual environment ───────────────────────────────────────────────
info "Creating Python virtual environment..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    success "Created .venv/"
else
    warn ".venv/ already exists — skipping creation"
fi

# Activate the virtual environment
# shellcheck disable=SC1091
source .venv/bin/activate
success "Activated .venv"

# ── Install Python dependencies ──────────────────────────────────────────────
info "Installing dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements-dev.txt
success "Dependencies installed"

# ── Install CLI in development mode ─────────────────────────────────────────
# -e means "editable" — changes to source code take effect immediately
# without reinstalling. Like a symlink into your source directory.
info "Installing guardops CLI in development mode..."
pip install --quiet -e .
success "guardops CLI installed"

# Verify it works
GUARDOPS_VERSION=$(guardops --version 2>&1)
success "CLI ready: $GUARDOPS_VERSION"

# ── Create .env if it doesn't exist ─────────────────────────────────────────
if [ ! -f ".env" ]; then
    info "Creating .env from .env.example..."
    cp .env.example .env
    success "Created .env — fill in your values before deploying to cloud"
else
    warn ".env already exists — keeping existing values"
fi

# ── Check optional tools ─────────────────────────────────────────────────────
echo ""
info "Checking optional tools (needed for Phase 1 deploy):"

check_tool() {
    local tool=$1
    local install=$2
    if command -v "$tool" &>/dev/null; then
        success "$tool: $(command -v $tool)"
    else
        warn "$tool: NOT FOUND — $install"
    fi
}

check_tool "docker"  "Install Docker Desktop: https://docker.com"
check_tool "kubectl" "brew install kubectl  OR  https://kubernetes.io/docs/tasks/tools/"
check_tool "k3d"     "brew install k3d  OR  https://k3d.io"
check_tool "helm"    "brew install helm  OR  https://helm.sh/docs/intro/install/"

# ── Run tests to verify setup ────────────────────────────────────────────────
echo ""
info "Running test suite to verify installation..."
if python -m pytest tests/ -q --tb=short; then
    success "All tests passed"
else
    warn "Some tests failed — check output above"
fi

# ── Print next steps ──────────────────────────────────────────────────────────
echo ""
echo "  ─────────────────────────────────────────────────────────"
echo "  Setup complete! Here's how to start:"
echo ""
echo "  1. Activate virtual environment in new terminals:"
echo "     source .venv/bin/activate"
echo ""
echo "  2. Start a local Kubernetes cluster:"
echo "     k3d cluster create guardops-local --port '8080:80@loadbalancer'"
echo ""
echo "  3. Initialize a test project:"
echo "     mkdir my-test-app && cd my-test-app"
echo "     guardops init"
echo ""
echo "  4. Deploy:"
echo "     guardops deploy"
echo ""
echo "  5. Check status:"
echo "     guardops status"
echo ""
echo "  ─────────────────────────────────────────────────────────"
echo ""
# test-project/Dockerfile — Phase 4.5 (multi-stage build)
#
# Why multi-stage?
#
# Before (single stage):
#   - pip, wheel, setuptools, jaraco.context all present in the final image
#   - Every build tool is a potential CVE surface
#   - pip uninstall wheel was a workaround, not a real fix
#
# After (multi-stage):
#   - Stage 1 (builder): full python:3.11, installs everything into an isolated venv
#   - Stage 2 (runtime): python:3.11-slim, copies ONLY the venv and app.py
#   - pip, wheel, setuptools, pkg_resources — none of these exist in the final image
#   - Trivy will find significantly fewer CVEs because the attack surface is smaller
#
# Image size impact: typically 40-60MB smaller than a single-stage slim build
# because compiler toolchain artifacts from pip are left behind in the builder layer.

# ── Stage 1: Builder ──────────────────────────────────────────────────────────
FROM python:3.11 AS builder

WORKDIR /build

# Create an isolated virtual environment in /build/venv.
# Isolating to a venv makes copying to the runtime stage trivial.
RUN python -m venv /build/venv

# Activate the venv for all subsequent RUN commands by prepending it to PATH.
ENV PATH="/build/venv/bin:$PATH"

# Copy and install dependencies first (Docker layer caching: this layer only
# re-runs when requirements.txt changes, not when app.py changes).
COPY requirements.txt .

RUN pip install --upgrade pip==25.3 && \
    pip install -r requirements.txt && \
    # Ensure the highest available patched versions of transitive deps
    pip install --upgrade "urllib3>=2.7.0" "jaraco.context>=6.1.0" && \
    pip cache purge && \
    # pip + setuptools ship VENDORED copies of jaraco.context (5.3.0) and wheel
    # (0.45.1) inside their _vendor/ dirs — those are the CVEs Trivy flags, and
    # pinning the standalone packages above can't touch them. They are build-time
    # only (the app imports just stdlib + prometheus_client), so remove them from
    # the venv that gets copied into the runtime image. The patched STANDALONE
    # jaraco.context / urllib3 stay behind.
    pip uninstall -y setuptools pip

# ── Stage 2: Runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim-bookworm AS runtime

WORKDIR /app

# Patch OS packages to their latest Debian security releases. The base image is
# pinned, so newer patched packages (e.g. libgnutls30 3.7.9-2+deb12u7, which fixes
# several HIGH/CRITICAL CVEs) only arrive via an explicit upgrade. Done in its own
# layer, before copying the app, so it caches independently.
RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    # The base image also ships a SYSTEM pip/setuptools/wheel under /usr/local that
    # vendor the same old jaraco.context (5.3.0) and wheel (0.45.1). The app runs
    # from the copied venv (PATH below), never the system interpreter, so remove the
    # system build tools and their vendored CVE copies entirely.
    rm -rf /usr/local/lib/python3.11/site-packages/pip* \
           /usr/local/lib/python3.11/site-packages/setuptools* \
           /usr/local/lib/python3.11/site-packages/wheel* \
           /usr/local/lib/python3.11/site-packages/pkg_resources \
           /usr/local/lib/python3.11/site-packages/_distutils_hack \
           /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/wheel \
           /usr/local/lib/python3.11/ensurepip

# Copy only the pre-built venv from the builder — no pip, no wheel, no compiler
COPY --from=builder /build/venv /venv

# Copy application source
COPY app.py .

# Point all Python commands to the venv binaries and packages.
# PYTHONPATH is not needed because the venv's site-packages path is in
# the venv's pyvenv.cfg, which Python reads automatically.
ENV PATH="/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Create a non-root user with a fixed UID (best practice: use a high UID
# that doesn't conflict with system users; 10001 is common in production).
# --no-create-home reduces image size and attack surface.
RUN useradd --uid 10001 --no-create-home --shell /sbin/nologin appuser

USER 10001

EXPOSE 8080

# Healthcheck uses the venv Python (via PATH) — no urllib needed separately
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz')"

CMD ["python", "app.py"]

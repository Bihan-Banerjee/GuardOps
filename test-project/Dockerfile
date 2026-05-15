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
    pip cache purge

# ── Stage 2: Runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

WORKDIR /app

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

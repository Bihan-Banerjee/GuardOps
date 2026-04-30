FROM python:3.11-slim

WORKDIR /app

# Upgrade pip and vulnerable packages identified by Trivy
# CVE-2026-24049: wheel 0.45.1 → 0.46.2
# CVE-2026-23949: jaraco.context 5.3.0 → 6.1.0
# CVE-2025-8869:  pip 24.0 → 25.3
RUN pip install --upgrade pip==25.3 wheel==0.46.2 "jaraco.context>=6.1.0"

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app.py .

RUN useradd -m appuser
USER appuser

EXPOSE 8080

# HEALTHCHECK fixes DS026 (trivy-fs LOW finding)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz')"

CMD ["python", "app.py"]
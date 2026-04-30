FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --upgrade pip==25.3 && \
    pip install -r requirements.txt && \
    # Upgrade vulnerable build tools
    pip install --upgrade "wheel>=0.46.2" "jaraco.context>=6.1.0" && \
    # Remove build tools not needed at runtime — eliminates the CVEs entirely
    # wheel and jaraco.context are only needed during pip install, not to run app.py
    pip uninstall -y wheel && \
    pip cache purge

COPY app.py .

RUN useradd -m appuser
USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz')"

CMD ["python", "app.py"]
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

# Upgrade pip first, then install deps, then force-upgrade vulnerable transitive deps
# This order matters — upgrading AFTER requirements ensures transitive deps don't
# get pinned back down by something else in the dependency tree
RUN pip install --upgrade pip==25.3 && \
    pip install -r requirements.txt && \
    pip install --upgrade "wheel>=0.46.2" "jaraco.context>=6.1.0"

COPY app.py .

RUN useradd -m appuser
USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz')"

CMD ["python", "app.py"]
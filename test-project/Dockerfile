FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt 2>/dev/null || true

COPY . .

RUN useradd --no-create-home --shell /bin/false appuser
USER appuser

EXPOSE 8080

CMD ["python", "app.py"]

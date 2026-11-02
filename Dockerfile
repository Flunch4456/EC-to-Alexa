FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY account_server.py application.py ecole_directe.py oauth_server.py web.py /app/
COPY README.md installation.md configuration.md CHECKLIST.md /app/
COPY .env.example /app/.env.example
RUN useradd --create-home appuser && mkdir /data && chown appuser:appuser /data
USER appuser
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["python", "account_server.py"]

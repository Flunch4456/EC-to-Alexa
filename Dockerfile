FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
# Code et tests uniquement (voir .dockerignore) : update.sh exécute les tests dans l'image.
COPY *.py ./
RUN useradd --create-home appuser && mkdir /data && chown appuser:appuser /data
USER appuser
ENV PYTHONUNBUFFERED=1 TZ=Europe/Paris
EXPOSE 8000 8001
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["python", "account_server.py"]

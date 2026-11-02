FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY application.py account_server.py oauth_server.py web.py ecole_directe.py ./
RUN useradd --create-home appuser && mkdir /data && chown appuser:appuser /data
USER appuser
ENV PYTHONUNBUFFERED=1
EXPOSE 8000 8001
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["python", "account_server.py"]

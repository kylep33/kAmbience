FROM python:3.12-slim
# slim (glibc) rather than alpine: the anthropic SDK's dependencies ship arm64 wheels for glibc only
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    KAMBIENCE_DIR=/app/kambience DATA=/data LIBRARY=/library INGEST=/ingest SOUNDS=/sounds PORT=8095
RUN pip install --no-cache-dir anthropic==2.8.0
WORKDIR /app
COPY kambience/kambience.py kambience/
COPY web/app.py web/
COPY web/static web/static
USER 1000:1000
EXPOSE 8095
HEALTHCHECK --interval=30s --timeout=3s CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8095/api/settings', timeout=2)"
CMD ["python3", "web/app.py"]

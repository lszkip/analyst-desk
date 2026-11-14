FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY exporter.py /app/exporter.py
USER 10001:10001
EXPOSE 8085
HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=12 \
  CMD ["python", "-c", "import json,urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8085/health',timeout=2); assert r.status==200 and json.load(r)['status']=='ok'"]
CMD ["python", "/app/exporter.py", "--data-dir", "/data", "--host", "0.0.0.0", "--port", "8085"]

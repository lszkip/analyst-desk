# An explicit source list keeps credentials and downloaded SEC data out of images.
# Pin the multi-platform manifest so rebuilds use the same base image.
FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements-ci.txt requirements-docker.txt ./
RUN python -m pip install --no-cache-dir -r requirements-docker.txt \
    && python -m pip check

RUN groupadd --gid 10001 analyst \
    && useradd --uid 10001 --gid analyst --no-create-home --home-dir /tmp analyst \
    && mkdir -p /app/data/raw

COPY main.py company_registry.py companies.json financial_contracts.py \
    financial_data.py financial_metrics.py output_guardrails.py \
    request_guardrails.py sec_periods.py sec_fiscal_years.py ./

USER 10001:10001
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD ["python", "-c", "import json, urllib.request; response = urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2); assert response.status == 200 and json.load(response) == {'status': 'ok'}"]

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

# CI runs this target with networking disabled and temporary writable storage.
# The test modules supply their own fake key, responses and financial fixtures.
FROM app AS tests
COPY download_company_facts.py check_financial_coverage.py \
    test_api.py test_financial_data.py ./
HEALTHCHECK NONE
CMD ["python", "-m", "unittest", "-v", "test_api.py", "test_financial_data.py"]

# The default image contains the API, not the test modules or download commands.
FROM app AS runtime

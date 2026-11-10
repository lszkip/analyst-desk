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

COPY main.py edgar_client.py company_registry.py companies.json financial_contracts.py \
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
    test_api.py test_financial_data.py filing_corpus.py prepare_filing_corpus.py test_filing_corpus.py \
    filing_passages.py prepare_filing_passages.py test_filing_passages.py \
    filing_supplements.py prepare_filing_supplements.py test_filing_supplements.py ./
HEALTHCHECK NONE
CMD ["python", "-m", "unittest", "-v", "test_api.py", "test_financial_data.py", "test_filing_corpus.py", "test_filing_passages.py", "test_filing_supplements.py"]

# Both the API client and EDGAR server use the same pinned MCP dependencies.
FROM app AS mcp-base
ENV FASTMCP_HOME=/tmp/fastmcp \
    XDG_DATA_HOME=/tmp/share
USER root
COPY requirements-mcp.txt ./
RUN python -m pip install --no-cache-dir -r requirements-mcp.txt \
    && python -m pip check
USER 10001:10001

# Only the EDGAR runtime includes the server entry point.
FROM mcp-base AS edgar
COPY edgar_mcp.py check_edgar_mcp.py ./
EXPOSE 8001
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD ["python", "-c", "import json, urllib.request; response = urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=2); assert response.status == 200 and json.load(response) == {'status': 'ok', 'service': 'edgar-mcp'}"]
CMD ["python", "edgar_mcp.py", "--transport", "http", "--host", "0.0.0.0", "--port", "8001"]

FROM edgar AS mcp-tests
COPY download_company_facts.py check_financial_coverage.py \
    test_api.py test_financial_data.py test_edgar_mcp.py test_api_mcp.py \
    filing_corpus.py prepare_filing_corpus.py test_filing_corpus.py \
    filing_passages.py prepare_filing_passages.py test_filing_passages.py \
    filing_supplements.py prepare_filing_supplements.py test_filing_supplements.py ./
HEALTHCHECK NONE
CMD ["python", "-m", "unittest", "-v", "test_api.py", "test_financial_data.py", "test_edgar_mcp.py", "test_api_mcp.py", "test_filing_corpus.py", "test_filing_passages.py", "test_filing_supplements.py"]

# The default target remains the existing API runtime.
FROM mcp-base AS runtime

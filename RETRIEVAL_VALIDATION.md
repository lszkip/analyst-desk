# Retrieval update validation

## Software checks

- Python 3.12, clean retrieval virtual environment: **361 tests passed**.
- Existing 297 tests preserved; 47 new retrieval/storage/evaluation tests and
  17 new KB/EDGAR-section MCP tests.
- Dependency check: no broken requirements.
- Real HTTP MCP transport exercised in the offline suite with invented fixtures.
- Compose/workflow YAML parsed; every Docker COPY source checked against the
  explicit build allowlist. Existing CI jobs received non-secret Qdrant placeholders
  for Compose validation.
- User-supplied DockerOut(1).txt on 11 November 2026 confirms the laptop image
  built and **all 361 tests passed in 14.813 seconds**. Host dependencies installed
  successfully and both real models downloaded and passed fingerprint checks.
- Docker itself remains unavailable in the authoring environment. The user's
  laptop logs now verify the corrected gateway and full-corpus runtime below.
  GitHub Actions for the retrieval feature branch remains pending.

## Local Docker connectivity correction (v2)

The first laptop Qdrant startup log displayed only `6333/tcp`, with no host port
mapping. The host indexer then failed with ResponseHandlingException. The v1
Compose file incorrectly tried to publish ports from an internal-only network.

The correction adds an NGINX gateway with fixed Qdrant and KB upstreams. Only the
gateway joins both an ordinary bridge and the internal network. Qdrant/KB retain
internal-only networking; source/model directories and credential environment
variables are not mounted or injected into the gateway. Upstream authentication
headers still pass through it, so it is part of the trusted local transport path.
Its only host bindings are 127.0.0.1:6333 and 127.0.0.1:8002. Dynamic Docker DNS
allows the gateway to start before KB exists and survive upstream replacement.
MCP request/response buffering is disabled; long-running responses are supported.

Official NGINX stable-alpine image resolved to NGINX 1.30.6 and is pinned to
`sha256:583b7f2fce1cdb8da5ad5c39ebd3c33b98b87d493190e548bdee1fa8c619c311`.
The pinned image's amd64 NGINX binary and libraries were downloaded with registry
digest verification and run locally as UID 1000. Validation passed:

- The original shipped NGINX configuration passed `nginx -t`.
- Qdrant health and authentication worked through the native gateway while KB
  was absent. Missing/invalid keys were rejected; an administrator inserted a
  64-vector batch, the read-only key read its count, and a read-only write got 403.
- An SSE chunk reached the client before its upstream response completed.
- All five actual filings-kb MCP tools were discovered and called through NGINX
  using fictional fixtures and Streamable HTTP/SSE.
- Compose/workflow YAML parsed; Qdrant/KB remain internal-only, only the gateway
  publishes loopback ports, and the gateway mounts only its configuration.

Native checks substituted loopback backend addresses for Docker service names.
They do not establish Docker DNS, bridge isolation or port publishing on the
laptop. No Python source or Dockerfile changed in this correction, so the existing
361-test result still describes the same Python test suite.
The later laptop logs below establish successful Docker startup, full-corpus
index publication, vector verification and live KB calls through the gateway.

Reference: https://github.com/moby/moby/discussions/53256

## Earlier native IBM integration

This is an integration smoke check, not the required labelled retrieval evaluation.

- Official Qdrant **1.20.0** Linux binary verified against its published asset
  SHA-256 `2361f3df0dedfef31f5444f9f15bd15d6b4a6cba6ad952daecc5fbe3867c5ee6`.
- Real local BGE-small-en-v1.5 embeddings: 384 dimensions, normalized vectors.
- Real local MiniLM-L6 cross-encoder reranking. Exact revisions and all model
  file hashes are in retrieval_config.json; no fake-vector fallback is present.
- Public IBM FY2023, FY2024, FY2025 filings plus their verified Exhibit 13 material
  were replay-verified from actual cached SEC source bytes.
- **1,517 source passages**, including **459 tables**, produced **2,378 search
  windows**. Every source passage had at least one indexed window.
- Every vector and Qdrant metadata record matched the saved embedding batches.
- Qdrant was terminated and restarted; the persisted index then verified again.
- An empty upsert using the read-only API key was rejected with HTTP 403.
- All five KB tools were called over actual HTTP MCP against that index.

Three development smoke questions exercised cyber risks, an income statement,
and hybrid-cloud/AI strategy. The income-statement query returned the consolidated
income statement within its top five, but ranked comprehensive income above it.
That is an example of why rankings must be evaluated rather than assumed correct.
Observed hybrid/reranker query times in this run were approximately 3.18, 5.83 and
3.52 seconds with models/index already loaded. They are not laptop performance
guarantees or held-out latency estimates.

The initial table view included too many blank layout cells. The final version
uses compact nonempty cell text plus available table headings/header rows; every
cell ID and the complete original structured table remain accessible. On this
IBM subset, that reduced search windows from 4,470 to 2,378. No Recall improvement
is claimed from these three smoke questions.

## Full-corpus laptop evidence: 11 November 2026

Reviewed user-supplied logs, not a rerun in the authoring environment:

| Check | Observed result |
|---|---|
| IndexOut.txt | status ok; 24,607 windows embedded, zero reused |
| IndexVerifyOut.txt | status ok; all 24,607 search windows verified |
| Coverage | 15 companies, two sectors, FY2023-2025: all 45 company-years |
| Source passages | 16,235 total: 10,901 text and 5,334 structured tables |
| Coverage limits | 239 unassigned passages retained; no reported coverage warnings |
| SearchOut.txt | MSFT FY2025 Item 1A query returned cybersecurity risk passages; five unique hits; 3.683 seconds |
| TableSearchOut.txt | IBM FY2025 consolidated income statement ranked first; five unique table hits; 7.1173 seconds |
| SearchPlanOut.txt | Exactly 12 branches: MSFT/GOOGL, FY2023-2025, financial/risk facets |
| MCPStartOut.txt | Qdrant, gateway, EDGAR and filings-kb reported healthy |
| MCPRetrievalOut.txt | All five KB tools passed; read_only_write_rejected=true |
| ReviewExportOut.txt | 40 questions, 5,109 source passages, zero labelled queries |

Collection: `ad_filings_8e98ead7e1521844de88c28b903e0cdf`

Generation:
`8e98ead7e1521844de88c28b903e0cdf21b817a95d644d212d293367566231bc`

Passage manifest SHA-256:
`504e3c7b667b3b7daa5ff6046d46fa639cf787d42630bfdeaf0cac66c38daae9`

The KB runtime checker called plan_search, search_filings, get_passage,
get_table_rows and get_coverage. The source review ZIP passed integrity checks;
its 5,109 passage IDs are unique, and all 40 scopes match the company, year,
Item and kind filters in their questions. Search smoke tests and source replay
do not establish Recall@5 or semantic completeness. Single-query timings are
observations, not benchmark latency estimates. No labelled quality pass is claimed.

## Evidence still required

1. Feature-branch GitHub Actions, including the Docker checks.
2. Full-corpus HTTP verification of the added EDGAR get_section tool; healthy
   EDGAR startup and passing section unit tests do not replace that call.
3. Establish 40 reviewed relevance labels after checking the development query
   scope. The supplied JSONL remains unlabelled. The current 5,109-passage review
   workload is a consequence of these scopes, not a count prescribed by the brief.
4. Measure Recall@5 before/after reranking and record any regression or failure.
5. FinanceBench external check and full table-aware/plain-text ablation.
6. Remaining agent, control, memory, evaluation, security and operations work in
   CAPSTONE_REQUIREMENTS.md.

This is a verified retrieval implementation checkpoint, with quality evaluation
still pending. The next implementation stage is the sandbox and research portal,
followed by the complete research graph. No data or model refresh is needed for
this documentation-only evidence update.

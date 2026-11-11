# Research tools checkpoint

Base: `7207082`, the supplied merge log for PR #10. Retrieval source remains the
previously merged implementation. This patch adds the two remaining named MCP
services; it does not complete the research agent or evaluation requirements.

## Execution and evidence

- Linux x86_64 / Python 3.12 authoring environment: full regression suite passes.
  Updated full run: 414 tests passed in 37.109 seconds (361 existing plus 53
  research-tool tests). Tests execute real
  sandbox children and real local authenticated HTTP MCP transports.
- `pip check`: no broken requirements. MCP 4.1.6 and NumPy 2.5.4 are retained;
  pandas 3.1.1, python-dateutil 2.9.0.post0 and six 1.17.0 are pinned.
- Both actual CLI server entrypoints started and passed the shipped operator
  checker through live authenticated HTTP, including kernel probes and blocked
  unapproved publication. Host/Origin protection remains enabled in those runs.
- The shipped operator checker is exercised through both live HTTP services.
  Human CLI approval/cancellation is tested with simulated terminal input and
  isolated synthetic databases; this is not evidence of a real human review.
- Compose and GitHub workflow YAML parse; structural checks confirm isolated
  networks, credential separation, no sandbox data mount and one portal mount.
- Supplied laptop logs: 409 Docker tests passed in 24.549 seconds; dependency
  installation and setup passed. The sandbox, portal and gateway started healthy.
  Live HTTP probes returned 25.00 and confirmed kernel denials plus blocked
  unapproved publication, read-only publication and agent approval.
- The supplied terminal screenshots show a successful human approval and
  publication of the synthetic test note. Its proposal is
  `proposal_35883a73872af678192e7b5756d06ef0`; its content hash is
  `927aae5b74957cd06485ec43c0bf2b86c687c58d60a962d66e60665609e723f2`.
  Repeating the old CLI exposed a user-interface bug: it requested approval for
  an already-published proposal, then correctly received a closed-proposal error.
- Retry fix: get_proposal now includes the saved note ID for published records.
  The CLI checks closed state before prompting and reports already_published
  with that existing ID. Read-only repeat runs leave the database unchanged.
  Rejected proposals also receive explicit status; edit/reject cannot change a
  published note. Five added regression tests cover these cases and inconsistent
  publication records.
- Updated laptop validation: `40.3.1 PortalRetryFixOut - Fix already published.txt`
  reports all 414 Docker tests passing in 25.853 seconds. The rebuilt research
  portal became healthy. Repeating the human review command returned status ok,
  action already_published and note ID
  `note_b0d478219c8edb57a358dd38c677fb7a`, with the same proposal/content hash.
  The operation was read-only: no new approval or publication was performed.
- `40.3.2 ResearchToolsGitOut - GIT.txt` confirms branch
  `feature/research-tools`, the expected patch files, no staged files and no
  whitespace-check output. The editor folder is the only unrelated untracked
  entry shown. GitHub CI results remain pending until the branch is pushed.
- Docker daemon execution remains unavailable in the authoring environment;
  the Docker results above come from the supplied laptop logs. This checkpoint
  changes only documentation and manifest metadata after the tested code.

## Service contract

| Service | Tools | Access |
|---|---|---|
| python-sandbox | execute_python, get_sandbox_policy | Dedicated quant credential |
| research-portal | list_notes, get_note, get_proposal | Read scope shared with writer/publisher credentials |
| research-portal | prepare_note | Draft credential; creates only a pending proposal |
| research-portal | publish_note | Separate publisher credential plus exact-note approval capability |
| human review HTTP route | approve, edit, reject | Separate human credential; not an MCP tool or agent credential |

All tools have typed schemas, structured results/errors and read/write
annotations. Authorization is checked on every call, including in-process MCP
calls. Auth-free STDIO is not offered by the new services. Discovery may list
tools a credential cannot execute; calls are independently denied. Per-agent
tool visibility/allowlists will be enforced in the graph dispatcher as well.

The mock portal is a local single-user service. Published notes are immutable,
carry the approved content hash and reviewer, and are read as untrusted text.
SQL is parameterized. Write transactions serialize competing approvals and
publications. An append-only-by-application audit table records transitions,
without raw credentials/capabilities. It is not an externally tamper-proof log.
Draft content must fit within 64 KiB; notes require SEC archive citation fields.
Citation metadata is validated syntactically, not replay-verified by this portal.
The graph's numerical/support checks remain required before proposing real notes.

## Sandbox boundary

Each execution starts a fresh `python -I -B` child, with an environment allowlist
and closed inherited descriptors. Trusted bootstrap loads pandas, math and
Decimal, applies resource limits, closes extra descriptors and installs a
Linux seccomp filter before executing validated code. The filter checks the
x86_64 audit architecture and defaults to EPERM for all unlisted syscalls.
Alternate syscall ABIs are not allowed. File open, network sockets, clone/fork,
exec, ptrace, namespaces, io_uring and filesystem mutation are not allowlisted.
Trusted probes bypass the AST and confirm that socket/openat/clone are denied.
Failure to install/probe the policy prevents the submitted code from running.

The AST restricts imports, dunder access, I/O helpers, indirect calls and
unsupported pandas methods. It is defence in depth, not the security boundary.
Resource limits: 3 CPU seconds including bootstrap, 10 wall seconds, 768 MiB
address space, 64 KiB combined output, 64 KiB JSON input, 16 KiB code, two active
jobs. Parent I/O is bounded and nonblocking; timeout kills the child's process
group. Decimal results are finite strings; other results must be finite JSON.
DataFrames/Series require explicit `to_dict()`/`tolist()` conversion.

The Linux policy is intentionally x86_64-only in this version. It does not
claim to support Windows native Python or ARM. pandas methods needing lazy
filesystem access after bootstrap fail closed. Container hardening supplies
additional isolation: non-root, read-only root, no data or Docker socket mount,
dropped capabilities, process/memory limits, internal network. The trusted MCP
parent needs HTTP connectivity; the calculation child cannot use networking.
This is an educational local compute service, not a claim of formally verified
or adversarial multi-tenant hosting. Kernel/library vulnerabilities remain in
the trusted computing base.

## Approval boundary

Canonical note JSON is hashed. A human decision is checked against its current
hash inside a database transaction. Approve issues a random, ten-minute
capability stored only as a digest. Edit changes revision/content, clears all
approval state and returns to pending. Reject closes the proposal. Publish
checks publisher scope, exact hash, capability and state/expiry atomically.
Concurrent or repeated successful calls return one existing note. Raw approval
tokens are never returned by read tools. The human credential cannot authenticate
to the MCP endpoint. Origin-bearing calls to the review route are rejected;
the operator CLI uses a local authenticated request without browser credentials.

The CLI displays the complete note/citations/hash, neutralizes terminal controls
and requires an exact typed confirmation for a pending review. A repeated approve
command for a published proposal returns its existing note ID before prompting;
no new approval or publication occurs. It refuses noninteractive stdin.
The live check creates a clearly labelled synthetic draft but does not approve
or publish it. Unit tests simulate approval only in isolated temporary stores.

When wiring LangGraph, the trusted backend must own the human credential and
resume the graph only after actual approve/edit/reject input. A payload change
must trigger fresh numeric/support checking and approval. Do not include human
tokens, bearer headers or approval capabilities in prompts, state summaries,
debug logs or tracing exports; trace scrubbers are still pending graph work.

## Remaining capstone evidence

Role-specific graph tool grants, budget enforcement, persistent stop/resume,
LangGraph interrupt(), visible plan editing, critic revisions, real financial
source/cell calculations, long-term memory, monitoring and evaluation are still
open. Security unit tests do not substitute for the frozen 40-attack study.
Retrieval labels/Recall@5, FinanceBench, 80 research cases, 30 human judge labels,
12 company-year risk labels, ablations and final deliverables remain tracked.

## Implementation references

- Linux kernel seccomp filter contract and limitations:
  https://kernel.org/doc/html/latest/userspace-api/seccomp_filter.html
- Linux x86_64 syscall numbers: `asm/unistd_64.h` and the kernel UAPI.
- FastMCP token verification:
  https://gofastmcp.com/docs/servers/auth/token-verification
- pandas release/dependency metadata: https://pypi.org/project/pandas/3.1.1/

These references guide implementation. Passing behavior is established by the
tests and deployment logs, not by the existence of a documentation link.

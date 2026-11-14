# Analyst Desk threat model

## Assets and trust boundaries

Assets are service/model credentials, human approval capabilities, research
evidence and its identifiers, the publication record, and the local host.
Source filings, retrieved passages and model-produced text are untrusted data.
They may contain instructions or forged claims of authority. Application code,
the authenticated operator and the local source/database administrator are
trusted for this model. Host compromise and direct database tampering require
a separate operational assessment.

The important boundaries are source text into agent context; agent actions into
the tool broker; tool replies into the evidence registry; drafts into output
validation; and a proposed note into human-approved publication. The computation
sandbox is a separate process and kernel-policy boundary.

| Threat | Enforced control and tested outcome | Evidence limit |
|---|---|---|
| Filing text requests publication, human review, shell/file access or arbitrary computation | Model-facing tools are explicitly allowed per role; scripted forbidden actions stop before backend dispatch. Source markers remain data. | Scripted agent responses test enforcement, not actual model susceptibility. |
| An action adds a URL, credential scope, code field or a different company/year | Exact argument fields and approved scope are checked before dispatch. | The offline suite does not exercise deployed HTTP authentication or every possible payload. |
| A tool returns a different passage or filing section | Requested identifiers and scope must match before registration; mismatches fail. Existing saved evidence cannot be silently replaced. | Source hashes identify bytes, not semantic truth. Trusted-host database edits are outside this boundary. |
| An injected draft asks the reader to trade a covered company's shares | Literal advice guard covers prepared-company tickers, names and aliases; allowed business prose is tested. | Paraphrases, obfuscation and general investment-advice detection require further evaluation. |
| Untrusted text claims approval or replays another proposal's capability | Typed schemas reject authority fields; publication requires the capability for the exact proposal/content. Reapproval invalidates the previous token. | The operator's judgement and a compromised review credential are outside these tests. |
| Errors or configuration expose credentials or redirect requests | Human-review/model keys are excluded from the broker; scoped auth fails closed; plain loopback endpoints and sanitised failure paths are tested with fake canaries. | No real credentials are used. This is not a comprehensive logging or dependency audit. |
| Computation reads files, opens a network connection, spawns processes or exhausts resources | Existing syntax controls, clean child environment, seccomp probes and time/memory/output limits are exercised by real subprocess tests. | Kernel tests validate the current Linux environment, not universal sandbox escape resistance. |

## Test execution and remaining evidence

The runner verifies and copies only known source files. Test databases and
simulated approvals are disposable. The child environment omits real service
credentials and tracing configuration; Python network operations are denied
before imports. The application sandbox has its own separately tested OS policy.
The Python audit hook is an accidental-network guard, not an OS security boundary
against arbitrary trusted test code.

This is an AI-assisted development regression suite. It is not the final
held-out red-team evaluation. Remaining work includes a frozen attack/clean-case
set with explicit outcomes, budgeted real-model evaluation, attack-success and
false-block measurements, deployed service checks, and independent review of
the resulting evidence. Future memory/watchlist features need their own access,
deletion and exfiltration tests. No external alert delivery or cloud security
claim is made by this local suite.

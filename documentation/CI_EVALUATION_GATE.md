# Saved experiment evaluation gate

This gate checks **saved observed outcomes**, without calling a model. Passing offline tests proves only that the verifier works on its test fixtures. It does not establish agent task quality, human judge agreement, completed held-out experiments, or a successful GitHub Actions run.

## Inputs and command

Keep these three public files from the same actual experiment together:

- `Experiment_Summary.json` (it may be renamed `Final_Experiment_Summary.json` for CI).
- `Experiment_Manifest.json` with its original fingerprint and `software_test_mode: false`.
- `Execution_Results.json` containing the saved execution records, outputs and post-run numeric evaluations.

The operator's `Pilot_Results.zip` includes these under `pilot/`. Gold answers, the private case bundle, API keys, budget credentials and evaluator-private folders are not CI inputs. Preserve the original private run traces separately for audit. Review public files for any unintended private content before committing them.

Run from the installed repository root, with thresholds chosen **before** inspecting the comparison being gated:

```bash
python -m evaluation.final.quality_gate \
  --summary /path/to/pilot/Experiment_Summary.json \
  --min-completed 40 \
  --min-numeric-success 0.95 \
  --max-policy-violations 0 \
  --max-errors 0 \
  --report Evaluation_Gate_Report.json
```

Each threshold applies to **each variant separately**. The example demands at least 40 completed executions per variant and a numeric pass fraction of at least 0.95. A two-case development pilot cannot satisfy that coverage target. Set different explicit thresholds for a declared pilot check; a pilot pass must remain labelled as a pilot, not final held-out completion. If the sidecars live elsewhere, supply `--manifest PATH` and `--results PATH` explicitly.

`min_numeric_success` is a fraction from 0 to 1. Its denominator is every frozen planned execution, including failures. `min_completed` must be positive. There is no zero-denominator pass. Unrun or skipped work prevents a performance pass even if a loose numeric threshold would otherwise pass. Completed outputs without numeric adjudication also fail coverage. Error limits count `error`, `timeout` and `interrupted`; blocked outcomes remain in the denominator and do not count as completed.

## What is checked

The gate verifies the experiment manifest fingerprint, binds every result to the frozen execution IDs and variant/split/repeat, and recomputes summary counts from the saved result rows. It checks that completed records contain a final note, citations, tool-call counts and elapsed-time observations. It rejects synthetic, dry-run and software-test markers, expected-label/gold records, changed denominators, missing evidence and inconsistent summaries. Policy limits refer to the harness's observed `policy_violation_attempts` count, not an assertion that every possible policy violation was detected.

This is a numeric outcome gate. Risk semantic success and pending human labels remain unadjudicated; critic approval, fabricated expected labels and offline test counts never replace actual outcomes. The report states this scope even when numeric thresholds pass.

These checks establish consistency of supplied saved artifacts, **not authenticity against a dishonest operator who rewrites all files and hashes**. Retain source fingerprints, journal records, model response receipts, provider accounting and independent review to support an audit. Do not call a gate pass independent verification that paid calls happened.

## Exit codes

| Exit | Meaning |
|---:|---|
| 0 | Actual supplied numeric evidence meets every explicit threshold |
| 2 | Invalid or missing command arguments/thresholds |
| 10 | Missing saved summary, manifest or execution results |
| 11 | Invalid/inconsistent saved evidence or changed manifest |
| 12 | Software-test, synthetic, dry-run or expected-label evidence |
| 13 | Empty queue, omitted jobs, unrun/skipped work or inadequate completed/scored coverage |
| 14 | Numeric passes / all planned executions below threshold |
| 15 | Observed policy violation attempts above threshold |
| 16 | Errors, timeouts or interrupted executions above threshold |
| 20 | Deliberately requested controlled negative check |

All failures are retained in the JSON report. When several limits fail, the smallest applicable failure code is returned; its value does not hide the other reported failures.

## GitHub workflow and honest coverage

`.github/workflows/evaluation.yml` reuses the checkout and Python setup commit pins from the existing test workflow. It uses the Python standard library and makes no paid/model calls.

On push or pull request, it runs verifier self-tests. If no actual evidence is present at `evaluation/evidence/Final_Experiment_Summary.json` or either sibling sidecar, it writes **NOT COVERED**, with `performance_claim: false`, in the logs and job summary. A green run in this situation means the offline checks passed; performance was not assessed. If any of the evidence files is present, the real gate runs and missing/inconsistent companions fail.

On manual dispatch with `negative_control: false`, the actual gate always runs; a missing report fails. The dispatch inputs expose the report path and all four thresholds. For repository evidence, keep these together:

```text
evaluation/evidence/Final_Experiment_Summary.json
evaluation/evidence/Experiment_Manifest.json
evaluation/evidence/Execution_Results.json
```

The workflow writes `Evaluation_Gate_Report.json` and copies its complete content into the job summary and logs, including on failure. It does not add an unverified artifact-upload action. Download or retain the workflow log/job summary; a separate artifact upload can be added with an independently approved action pin.

## Controlled negative CI demonstration

To verify the failure path locally:

```bash
python -m evaluation.final.quality_gate \
  --negative-control \
  --report Controlled_Negative_Gate_Report.json
```

This deliberately exits **20** and reports `controlled_negative_failure`, `negative_control: true` and `performance_claim: false`. It does not fabricate a bad model run or a failed performance experiment.

For a real CI demonstration later, push the workflow, choose **Run workflow**, and set `negative_control: true`. Save the actual GitHub Actions run URL and its log after it runs. The deliberate failing step must make that job red; the final always-run step preserves the explanation in the job summary. No GitHub run is claimed merely because this workflow file or a local negative-control report exists.

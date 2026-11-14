# Actual controlled failed CI demonstration

The [hosted GitHub job](https://github.com/lszkip/analyst-desk/actions/runs/43625938338/job/132541633135) is visibly failed at **Deliberately fail controlled negative CI check**. The user-supplied screenshot shows this command:

```bash
python -m evaluation.final.quality_gate --negative-control --report Evaluation_Gate_Report.json
```

The displayed report has `status=controlled_negative_failure`, `exit_code=20`, `negative_control=true`, `gate_passed=false`, `thresholds=null` and `performance_claim=false`. Its explicit explanation says the failure is deliberate and no experiment performance was assessed.

The offline verifier tests and saved-evidence availability steps passed. The actual numeric gate was skipped. The job-summary/log preservation step passed. This establishes an actual hosted failed CI run with an explanation, not an observed below-threshold research result or a passed research-quality gate.

The screenshot does not show this run's exact commit SHA. It is not assumed to be the same commit as the separate successful clean-install run. [Text observation and provenance](evidence/deployment_actual/Controlled_Negative_CI_Observed.json) retain the job https://github.com/lszkip/analyst-desk/actions/runs/43625938338/job/132541633135, source filename and SHA-256; no screenshot pixels are bundled.

Source image SHA-256: `140c1234dd5c4ef30436501ca140ff4433fc939a1aea69134ba8c3af85398e8e`.

Together with [actual fresh-runner installation](ACTUAL_CLEAN_INSTALLATION.md), both requested L16 final-review evidence items are now covered within these stated scopes. Held-out performance, semantic safety and documentation-package installation remain separate.

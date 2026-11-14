# Record checkpoint resume and approval invalidation

This is a controlled live software demonstration, using the production LangGraph workflow, SQLite checkpointer and real portal HTTP/MCP server. Its planner response and note are explicitly synthetic. It demonstrates control behavior, not financial accuracy or autonomous research quality. A complete human-approved run of this demonstration must be performed by the operator; AI-generated test output does not replace it.

## Three-minute recording

Start screen recording with the terminal visible. Keep your normal stack running; the demonstration uses a separate local port and new private databases.

```bash
python -m deployment.live_control_demo
```

1. **Persistent checkpoint (about 45 seconds).** Explain that the real research graph has paused at `plan_review` after one scripted planner response. Show the full plan hash and checkpoint ID. The first Python child has exited. Type **REJECT** when prompted. Show the different process ID, identical checkpoint ID/hash, terminal `rejected` state, and zero repeated planner responses. The graph resumed from its persistent SQLite checkpoint and followed the production rejection branch without starting research.
2. **Exact-content approval (about 45 seconds).** Read the displayed synthetic revision 1 and its full SHA-256. The note explicitly says it is synthetic and its citation is a non-existent fixture. Enter your own name or initials. Type **APPROVE** only if you approve this displayed content for the controlled demonstration. The separate human-review HTTP endpoint grants a private capability; it is never displayed or exported. No publication is attempted while this approval is valid.
3. **Edit revocation (about 45 seconds).** Read revision 2 and its different full hash. Type **EDIT** to apply that exact replacement. The production portal resets the proposal to `pending`. The launcher then makes two negative publication attempts using the old capability: first against the old hash, then against the new hash. Both must return `approval_required`; the published-note count must remain **0**.
4. **Evidence (about 30 seconds).** Show `Result: passed` and the printed ZIP path. Keep the video and upload only that results ZIP. It contains the sanitized report, hashes, process/checkpoint identifiers and observed rejection results. It excludes credentials, approval capabilities, private databases and your reviewer name. If the run is cancelled or fails, its report says so and is not completed evidence.

The three confirmation words are plain **REJECT**, **APPROVE**, and **EDIT**. No code suffix is required. Piped input is refused. Only the temporary portal process created by this launcher is stopped. Existing notes, saved Sequence 25 evidence, pilot budgets, research databases and running containers are untouched. Every run has a unique output folder and ZIP under `LSOutput`; no prior result is overwritten.

The private database may retain the operator-entered name and audit history locally. Do not upload the `private` directory or commit any `LSOutput` contents wholesale. The report states that the reviewer identity was entered by the operator and was not independently verified.

## Optional recording of the real research UI

Use this only for a **new development run** if time and the existing model budget permit. It is separate from the no-cost controlled demonstration above. Do not restart the successful F002 evaluation case, reuse its execution ID, or alter its frozen evaluation records just to obtain a video.

1. Open the existing research UI at `http://localhost:8080`. Unlock it privately with your normal UI token; do not include that token in the recording. Choose Microsoft and Alphabet, FY2025, financial comparison only, with risks disabled. Use a new development run ID such as `demo_financial_resume_<current-time>`.
2. Show the displayed model/budget and explicitly accept the model costs. Start the run. When the plan approval gate appears, show the plan and pending state. This interrupt is already a persisted mid-run pause.
3. Reload the browser and select the same saved run. Show that its request, plan and pending review survived. This proves browser/session resume only; the controlled terminal demonstration above provides the separate-process checkpoint evidence. Do not describe a browser reload as a server restart.
4. If you want to continue real research, approve the exact plan yourself and let it advance. Otherwise reject it. Do not approve a financial note you have not reviewed. Publication is unnecessary for the checkpoint demonstration.

For the final presentation, state the distinction plainly: “The production graph and portal controls ran live in isolation with synthetic inputs. A human entered the three decisions shown in the recording. The financial research examples and evaluation results are separate evidence.”

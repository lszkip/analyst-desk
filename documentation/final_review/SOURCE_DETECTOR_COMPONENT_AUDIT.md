# Source detector component audit

This is a deterministic component measurement on the known development attack corpus, with no model calls, production graph execution, service dispatch or publication. The source detector is the only toggled component.

| Corpus | Cases | Detector off: source rejections | Detector on: source rejections |
|---|---:|---:|---:|
| Attack passages | 40 | 0 | 35 (87.5%) |
| Clean passages | 20 | 0 | 0 (0%) |

The five undetected attack passages remain in the denominator. All 16 writer attack passages were detected; 19 of 24 researcher-targeted passages were detected when scanned in isolation. Production researcher nodes do not apply this quarantine support gate, so the latter scan is not evidence of researcher-arm treatment.

These are source-rejection rates and component false positives. They are not model attack-success or whole-system legitimate-work false-block rates. The source detector can reject an entire passage containing useful facts, and the known development corpus is implementation-informed rather than held out. Indirect, encoded, multilingual, cross-passage and unrecognised-syntax attacks remain limitations.

The [exact component report](evidence/security/Offline_Source_Detector_Audit.json) includes every case, source hash, rule code and denominator. Its model attack-success and whole-task false-block fields remain null. No source text or private payload is reproduced here.

The [historical live counts](evidence/security/Historical_Live_Security_Counts.json) independently extract S30, S31 and S32 from their original reports, preserving archive/report hashes and planned, attempted and evaluable counts. S31 recorded all 60 cases, 70 model calls and USD 0.0601892 known actual cost. It recorded zero unauthorized dispatches and zero accepted structural violations. This does not imply zero semantic attack success: 38 attack cases and 20 clean cases required semantic review, and the saved writer/critic failure is preserved. S30 and S31 changed harness behavior, so their difference is not a defenses-off/on causal estimate. S32 remains a targeted two-draft diagnostic.

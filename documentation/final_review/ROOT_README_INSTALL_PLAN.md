# Safe repository root README installation

The public delivery includes a tested installer. Extract `Analyst_Desk_Submission` inside the project, then run from the repository root:

```bash
python Analyst_Desk_Submission/Install_Submission.py
```

The installer verifies the exact public file manifest and installs documents under `documentation/final_review`. It prepends a clearly marked quickstart and review links to root `README.md`, preserving the existing body byte for byte. Originals are backed up under a uniquely named `LSOutput/Submission_Backup_*` directory. If the README is absent, it creates the marked navigation.

Existing user modifications to managed text or installed documents, and unrecognized destination collisions, cause a stop before writes. A recognized unchanged prior package can be upgraded, and an unchanged repeated install makes no changes. The installer does not change application source, databases, budgets, Docker state or Git; it makes no model calls. `source_reference` contains documentary historical snapshots only.

The original installer passed eight isolated tests and a 114-file payload installation/idempotence check. Each newer payload is separately validated when packaged. No actual user installation of this documentation package has yet been received.

The supplied source snapshot omitted the user's root README; that omission was not evidence that it did not exist. [ROOT_README_PROPOSAL.md](ROOT_README_PROPOSAL.md) remains a reference draft, while the installer preserves the real target content. After installation, inspect the diff and stage only intended README/documentation files. Startup on a prepared project and clean-machine installation remain separate evidence requirements.

### Verified fresh-runner installation

[Actual GitHub clean installation](ACTUAL_CLEAN_INSTALLATION.md) now passed at commit `3b1b96a72ca78dd3cd9296517fbd27fb9e1a6cbd`: 12 containers running, nine HTTP 200 endpoints, protocol/UI/auth/SSE checks and all 11 commands passed; unique-project cleanup completed. Synthetic fixtures, zero LLM calls and no executed research workflow. L16 clean installation is evidenced. The controlled failed CI demonstration is now observed with exit code 20 and no performance claim; documentation-package installation remains unverified.

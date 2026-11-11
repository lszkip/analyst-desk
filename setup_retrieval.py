"""Create local Qdrant credentials/directories without displaying any secrets."""
import os
from pathlib import Path
import secrets
from dotenv import dotenv_values
from download_company_facts import _atomic_write


def setup(root=Path(__file__).resolve().parent):
    root = Path(root)
    path = root / ".env"
    if path.is_symlink():
        raise ValueError("Refusing to replace a symlinked .env.")
    original = path.read_bytes() if path.exists() else b""
    existing = dotenv_values(path) if path.exists() else {}
    additions = []
    for key in ("QDRANT_API_KEY", "QDRANT_READ_ONLY_API_KEY"):
        if key in existing and (not existing[key] or len(existing[key]) < 32):
            raise ValueError(f"Existing {key} must have at least 32 characters. Update it without sharing the value.")
        if key not in existing:
            additions.append(f"{key}={secrets.token_urlsafe(32)}")
    if existing.get("QDRANT_API_KEY") and existing.get("QDRANT_API_KEY") == existing.get("QDRANT_READ_ONLY_API_KEY"):
        raise ValueError("Qdrant administrator and read-only keys must differ.")
    if additions:
        raw = original + (b"\n" if original and not original.endswith(b"\n") else b"") + "\n".join(additions).encode() + b"\n"
        _atomic_write(path, raw)
        path.chmod(0o600)
    for name in ("data/qdrant_storage", "data/qdrant_snapshots", "data/models", "data/retrieval", "LSOutput"):
        (root / name).mkdir(parents=True, exist_ok=True)
    exclude = root / ".git" / "info" / "exclude"
    if exclude.parent.is_dir():
        prior = exclude.read_text() if exclude.exists() else ""
        patterns = ("/.env", "/.venv-retrieval/", "/LSOutput/", "/data/models/",
                    "/data/qdrant_storage/", "/data/qdrant_snapshots/", "/data/retrieval*/")
        missing = [pattern for pattern in patterns if pattern not in prior.splitlines()]
        if missing:
            _atomic_write(exclude, (prior.rstrip("\n") + "\n" + "\n".join(missing) + "\n").encode())
    print("Local retrieval directories and separate Qdrant keys are ready. No credentials were printed.")


if __name__ == "__main__":
    setup()

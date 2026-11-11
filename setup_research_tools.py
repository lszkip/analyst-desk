"""Set up local research-tool secrets without printing or replacing existing values."""
import os
from pathlib import Path
import secrets
from dotenv import dotenv_values
from download_company_facts import _atomic_write

KEYS = ("SANDBOX_API_TOKEN", "PORTAL_READ_TOKEN", "PORTAL_DRAFT_TOKEN", "PORTAL_PUBLISH_TOKEN", "PORTAL_REVIEW_TOKEN")


def setup(root=Path(__file__).resolve().parent):
    root = Path(root)
    path = root / ".env"
    if path.is_symlink():
        raise ValueError("Refusing a symlinked .env.")
    raw = path.read_bytes() if path.exists() else b""
    prior = dotenv_values(path) if path.exists() else {}
    values = {}
    additions = []
    for key in KEYS:
        value = prior.get(key) if key in prior else secrets.token_urlsafe(32)
        if not value or not value.isascii() or len(value) < 32:
            raise ValueError(f"Existing {key} must contain at least 32 ASCII characters; its value was not printed.")
        values[key] = value
        if key not in prior:
            additions.append(f"{key}={value}")
    if len(set(values.values())) != len(KEYS):
        raise ValueError("Research-tool credentials must all differ.")
    if additions:
        _atomic_write(path, raw + (b"\n" if raw and not raw.endswith(b"\n") else b"") + "\n".join(additions).encode() + b"\n")
    path.chmod(0o600)
    data = root / "data" / "research_portal"
    if data.is_symlink():
        raise ValueError("Refusing a symlinked portal directory.")
    data.mkdir(parents=True, exist_ok=True)
    data.chmod(0o700)
    exclude = root / ".git" / "info" / "exclude"
    if exclude.parent.is_dir():
        original = exclude.read_text() if exclude.exists() else ""
        missing = [p for p in ("/.env", "/data/research_portal/", "/LSOutput/") if p not in original.splitlines()]
        if missing:
            _atomic_write(exclude, (original.rstrip("\n") + "\n" + "\n".join(missing) + "\n").encode())
    print("Five distinct research-tool credentials and the private portal directory are ready. No secrets were printed.")


if __name__ == "__main__":
    setup()

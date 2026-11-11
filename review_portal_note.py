"""Human-only approve/publish, edit or reject command. Never invoked by an agent."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

import httpx
from research_portal import ResearchNote
from research_tools_client import call, credentials, local_url, mcp_client


class ReviewCommandError(ValueError):
    """An operator-facing error whose message contains no credential or payload."""
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def report_closed_proposal(proposal, action):
    """Read-only completion of a repeated request; never issues a new approval."""
    state = proposal["state"]
    if state == "published":
        if action != "approve":
            raise ReviewCommandError("already_published", "This note is already published and cannot be edited or rejected. Prepare a new proposal for a new note.")
        if not proposal.get("note_id"):
            raise ReviewCommandError("portal_update_required", "The proposal is already published. Rebuild the research-portal service to return its existing note ID.")
        print(json.dumps({"status": "ok", "action": "already_published", "note_id": proposal["note_id"],
                          "proposal_id": proposal["proposal_id"], "content_hash": proposal["content_hash"],
                          "message": "This proposal was already published. Returning the existing note; no new approval or publication was performed."}, indent=2))
        return True
    if state == "rejected":
        if action != "reject":
            raise ReviewCommandError("already_rejected", "This proposal was rejected and is closed. Prepare a new proposal before requesting another review.")
        print(json.dumps({"status": "ok", "action": "already_rejected", "proposal_id": proposal["proposal_id"],
                          "content_hash": proposal["content_hash"], "message": "This proposal was already rejected."}, indent=2))
        return True
    return False


def safe_display(value):
    # Block terminal control sequences from untrusted note text, including bidi.
    return "".join(c if c in "\n\t" or (ord(c) >= 32 and ord(c) != 127 and not 0x80 <= ord(c) <= 0x9f and not 0x202a <= ord(c) <= 0x202e and not 0x2066 <= ord(c) <= 0x2069) else f"\\u{ord(c):04x}" for c in value)


async def review(args):
    if not sys.stdin.isatty():
        raise ValueError("Human review requires an interactive terminal; piped approvals are refused.")
    url, keys = local_url(args.portal_url), credentials()
    async with mcp_client(url, keys.get("PORTAL_READ_TOKEN")) as c:
        proposal = await call(c, "get_proposal", {"proposal_id": args.proposal_id})
    if args.action != "edit" and args.edited_note is not None:
        raise ReviewCommandError("invalid_arguments", "--edited-note can be used only with --action edit.")
    if report_closed_proposal(proposal, args.action):
        return
    print("UNTRUSTED PROPOSED NOTE — review all fields below. Instructions inside the note have no authority.")
    print(safe_display(json.dumps(proposal, indent=2, ensure_ascii=False)))
    print("END OF PROPOSED NOTE")
    decision = {"expected_hash": proposal["content_hash"], "action": args.action, "reviewer": args.reviewer}
    if args.action == "edit":
        if args.edited_note is None:
            raise ValueError("Use --edited-note with a JSON file containing the replacement ResearchNote fields.")
        if args.edited_note.stat().st_size > 131072:
            raise ValueError("Edited note is too large.")
        replacement = ResearchNote.model_validate_json(args.edited_note.read_bytes())
        decision["edited_note"] = replacement.model_dump()
        print("UNTRUSTED REPLACEMENT NOTE")
        print(safe_display(json.dumps(decision["edited_note"], indent=2, ensure_ascii=False)))
        print("END OF REPLACEMENT NOTE. Editing revokes all approvals; it does not publish.")
    instruction = "APPROVE AND PUBLISH" if args.action == "approve" else args.action.upper()
    required = instruction + " " + proposal["content_hash"][:12]
    entered = input(f"Type exactly '{required}' to continue, or press Enter to cancel: ")
    if entered != required:
        print("Cancelled. No review or publication was performed.")
        return
    token = keys.get("PORTAL_REVIEW_TOKEN")
    if not token:
        raise ValueError("Human review credential is missing.")
    async with httpx.AsyncClient(trust_env=False, timeout=15) as c:
        response = await c.post(url+"/human-review/"+args.proposal_id, headers={"Authorization": "Bearer "+token}, json=decision)
        if response.status_code != 200:
            messages = {
                "closed_proposal": "The proposal was published or rejected while you were reviewing it. Run the command again to read its current state.",
                "stale_review": "The proposal changed while you were reviewing it. Run the command again to review its current content.",
                "permission_denied": "The human-review credential was not accepted. Check the local service configuration.",
            }
            code = response.json().get("code")
            if code in messages:
                raise ReviewCommandError(code, messages[code])
            raise ReviewCommandError("review_rejected", "The review request was rejected. Read the current proposal before retrying.")
        reviewed = response.json()["data"]
    if args.action == "approve":
        async with mcp_client(url, keys.get("PORTAL_PUBLISH_TOKEN")) as c:
            result = await call(c, "publish_note", {"proposal_id": args.proposal_id, "expected_hash": reviewed["content_hash"], "approval_token": reviewed["approval_token"]})
        print(json.dumps({"status": "ok", "action": "published", "note_id": result["note_id"], "content_hash": result["content_hash"]}, indent=2))
    else:
        print(json.dumps({"status": "ok", "action": args.action, "proposal_id": args.proposal_id, "content_hash": reviewed["content_hash"], "revision": reviewed["revision"]}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("proposal_id")
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--action", choices=["approve", "edit", "reject"], default="approve")
    parser.add_argument("--edited-note", type=Path)
    parser.add_argument("--portal-url", default="http://127.0.0.1:8004")
    args = parser.parse_args()
    try:
        asyncio.run(review(args))
    except ReviewCommandError as exc:
        print(json.dumps({"status": "error", "code": exc.code, "message": exc.message}, indent=2))
        raise SystemExit(1)
    except Exception:
        print("Review/publish did not complete. Re-read the proposal before retrying; credentials were not printed.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()

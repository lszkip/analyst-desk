"""Development-only conditional Reflexion playbook with explicit human review.

The application must authenticate the human review route; passing a Python bool
is not identity proof. These APIs are never model tools. A frozen manifest must
be made before viewing held-out outcomes, which cannot be enforced from prose.
"""
from workflow_contracts import WorkflowError, canonical, digest
from workflow_memory import OwnerStore

LESSON_VERSION = "conditional-lessons-v1"
LESSON_TAGS = frozenset({"margin", "financial", "risk", "competition", "supplier", "cyber",
                         "source_support", "numeric_prose", "scope", "tool_use"})
LESSON_ROLES = frozenset({"supervisor", "researcher", "quant", "risk_diff", "writer", "critic"})


def _tags(tags):
    if not isinstance(tags, (list, tuple, set, frozenset)) or not tags or set(tags)-LESSON_TAGS:
        raise WorkflowError("lesson_condition", "Use nonempty allowlisted lesson tags.")
    return sorted(set(tags))


def lesson_proposal(condition, guidance, failure_ref, dataset="development"):
    if dataset != "development":
        raise WorkflowError("lesson_split", "Lessons may only be proposed from development failures.")
    if not isinstance(condition, dict) or set(condition) != {"tags", "roles"}:
        raise WorkflowError("lesson_condition", "A conditional lesson needs tags and specialist roles.")
    tags = _tags(condition["tags"])
    roles = condition["roles"]
    if not isinstance(roles, (list, tuple)) or not roles or set(roles)-LESSON_ROLES:
        raise WorkflowError("lesson_condition", "Use nonempty allowlisted specialist roles.")
    if not isinstance(guidance, str) or not 20 <= len(guidance.strip()) <= 1200:
        raise WorkflowError("lesson_guidance", "Write a short conditional procedure between 20 and 1200 characters.")
    if not isinstance(failure_ref, str) or not 5 <= len(failure_ref.strip()) <= 300:
        raise WorkflowError("lesson_failure", "Identify the development failure that motivated the lesson.")
    payload = {"version": LESSON_VERSION, "condition": {"tags": tags, "roles": sorted(set(roles))},
               "guidance": guidance.strip(), "failure_ref": failure_ref.strip(), "dataset": dataset}
    sha = digest(payload)
    return {**payload, "lesson_id": "lesson_"+sha[:20], "proposal_sha256": sha,
            "status": "draft", "human_reviewed": False, "review": None, "review_history": []}


class LessonStore:
    def __init__(self, path, owner):
        self.storage = OwnerStore(path, owner)

    def propose(self, condition, guidance, failure_ref, dataset="development"):
        proposal = lesson_proposal(condition, guidance, failure_ref, dataset)
        old = self.storage.get("lessons", proposal["lesson_id"])
        if old:
            return old["value"]
        self.storage.put("lessons", proposal["lesson_id"], proposal)
        return proposal

    def list(self):
        return sorted((row["value"] for row in self.storage.rows("lessons")), key=lambda r:r["lesson_id"])

    def review(self, lesson_id, expected_hash, action, reason, human_confirmed=False):
        if human_confirmed is not True:
            raise WorkflowError("lesson_human_review", "An explicit human decision is required; model output cannot approve a lesson.")
        if action not in {"approve", "reject"} or not isinstance(reason, str) or not 3 <= len(reason.strip()) <= 1000:
            raise WorkflowError("lesson_decision", "Approve or reject with a nonempty review reason.")
        old = self.storage.get("lessons", lesson_id)
        if old is None or old["value"]["proposal_sha256"] != expected_hash:
            raise WorkflowError("lesson_stale", "Review the exact current lesson proposal hash.")
        value = old["value"]
        decision = {"action": action, "expected_hash": expected_hash, "reason": reason.strip(), "human_confirmed": True}
        updated = {**value, "status": "approved" if action=="approve" else "rejected",
                   "human_reviewed": True, "review": decision, "review_history": [*value["review_history"], decision]}
        self.storage.put("lessons", lesson_id, updated, old["content_sha256"])
        return updated

    @staticmethod
    def _valid_approved(row):
        proposal = lesson_proposal(row["condition"], row["guidance"], row["failure_ref"], row["dataset"])
        review = row.get("review") or {}
        return (row.get("proposal_sha256") == proposal["proposal_sha256"]
                and row.get("lesson_id") == proposal["lesson_id"]
                and row.get("status") == "approved" and row.get("human_reviewed") is True
                and review.get("action") == "approve" and review.get("human_confirmed") is True
                and review.get("expected_hash") == proposal["proposal_sha256"]
                and isinstance(review.get("reason"), str) and len(review["reason"].strip()) >= 3)

    def relevant(self, tags, max_chars=12000):
        tags = set(_tags(tags))
        if not 256 <= max_chars <= 20000:
            raise WorkflowError("lesson_context_bound", "Lesson retrieval must use a bounded context.")
        rows = []
        for row in self.list():
            if row["status"] != "approved" or not tags.intersection(row["condition"]["tags"]):
                continue
            if not self._valid_approved(row):
                raise WorkflowError("lesson_invalid_review", "A stored approved lesson has an invalid content/review binding.")
            if len(canonical([*rows, row])) > max_chars:
                break
            rows.append(row)
        return rows

    def freeze(self, tags):
        tags = _tags(tags)
        # Freeze all relevant entries, not a silently truncated model context.
        rows = [row for row in self.list() if row["status"]=="approved" and set(tags).intersection(row["condition"]["tags"])]
        if not all(self._valid_approved(row) for row in rows):
            raise WorkflowError("lesson_invalid_review", "Every frozen lesson needs valid exact-hash human review.")
        manifest = {"version": LESSON_VERSION, "owner_scope": self.storage.owner_scope, "tags": tags, "lessons": rows}
        return {**manifest, "manifest_sha256": digest(manifest)}

    def verify_freeze(self, manifest):
        if not isinstance(manifest, dict) or set(manifest) != {"version", "owner_scope", "tags", "lessons", "manifest_sha256"}:
            raise WorkflowError("lesson_freeze", "Invalid playbook freeze manifest.")
        content = {key:value for key,value in manifest.items() if key!="manifest_sha256"}
        if digest(content) != manifest["manifest_sha256"] or manifest != self.freeze(manifest["tags"]):
            raise WorkflowError("lesson_freeze", "Frozen playbook changed, is from another owner, or lacks current local approval records.")
        return manifest

    def context(self, tags, role, manifest=None, max_chars=2000):
        tags = set(_tags(tags))
        if role not in LESSON_ROLES or not 256 <= max_chars <= 6000:
            raise WorkflowError("lesson_context_bound", "Use a known role and bounded lesson context.")
        if manifest is not None:
            self.verify_freeze(manifest)
            if not tags.issubset(set(manifest["tags"])):
                raise WorkflowError("lesson_freeze", "Requested tags were not included in the frozen playbook.")
            available = manifest["lessons"]
        else:
            available = self.relevant(tags)
        result = {"version": LESSON_VERSION, "role": role, "lessons": [], "truncated": False,
                  "scope": "Human-reviewed conditional procedures; never factual evidence or permission overrides."}
        for row in available:
            if role not in row["condition"]["roles"] or not tags.intersection(row["condition"]["tags"]):
                continue
            view = {"lesson_id": row["lesson_id"], "proposal_sha256": row["proposal_sha256"],
                    "condition": row["condition"], "guidance": row["guidance"]}
            if len(canonical({**result, "lessons": [*result["lessons"], view]})) > max_chars:
                result["truncated"] = True
                break
            result["lessons"].append(view)
        return result


def reflection_proposal(failure, guidance, condition):
    """A reflection step prepares a draft; it NEVER invents a human decision.

    The development evaluator supplies its recorded case ID, outcome, and split.
    Guidance may be authored locally or proposed by a separate model, whose
    generation cost must be recorded by the caller if one is used.
    """
    if not isinstance(failure, dict) or failure.get("split") != "development" or failure.get("passed") is not False:
        raise WorkflowError("lesson_failure", "Reflection requires a recorded failed development case.")
    return lesson_proposal(condition, guidance, str(failure.get("case_id", "")), "development")

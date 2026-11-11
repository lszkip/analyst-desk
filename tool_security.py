"""Local service credentials and fail-closed, per-call authorization."""
import hashlib
import secrets
from typing import Literal

from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.server.dependencies import get_access_token
from pydantic import BaseModel, ConfigDict, JsonValue

READ = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
WRITE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}


class Reply(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["ok", "error"]
    data: dict[str, JsonValue] | None = None
    code: str | None = None
    message: str | None = None


class ToolError(ValueError):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def credential_digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


class LocalTokenVerifier(TokenVerifier):
    """Opaque, distinct local credentials; never accept a caller-supplied scope."""
    def __init__(self, grants):
        super().__init__(required_scopes=["mcp"])
        tokens = [item[0] for item in grants]
        if any(not t or len(t) < 32 or not t.isascii() for t in tokens) or len(set(tokens)) != len(tokens):
            raise ValueError("Service credentials must be distinct ASCII strings of at least 32 characters.")
        self.grants = [(credential_digest(t), subject, scopes) for t, subject, scopes in grants]

    async def verify_token(self, token):
        digest = credential_digest(token)
        for expected, subject, scopes in self.grants:
            if secrets.compare_digest(digest, expected):
                return AccessToken(token=token, client_id=subject, subject=subject, scopes=["mcp", *scopes])
        return None


def require_scope(scope):
    token = get_access_token()
    if token is None or scope not in token.scopes:
        raise ToolError("permission_denied", "This credential does not permit the requested operation.")


def perform(scope, operation):
    try:
        require_scope(scope)
        return Reply(status="ok", data=operation())
    except ToolError as exc:
        return Reply(status="error", code=exc.code, message=exc.message)
    except Exception:
        return Reply(status="error", code="operation_failed", message="The operation failed; no confidential diagnostic was returned.")

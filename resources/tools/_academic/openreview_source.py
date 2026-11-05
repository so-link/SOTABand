"""OpenReview API V2 access through the official openreview-py client."""

from __future__ import annotations

from contextlib import contextmanager

import requests

from .common import ToolError
from .configuration import missing, resolve
from .network import transport_error

BASE_URL = "https://api2.openreview.net"


def credentials():
    values, source = resolve("openreview")
    token = values.get("token", "").removeprefix("Bearer ").strip()
    username = values.get("username", "").strip()
    password = values.get("password", "")
    if token:
        return {"token": token}, source, "token"
    missing_fields = [
        name for name, value in (("username", username), ("password", password)) if not value
    ]
    if missing_fields:
        raise missing("openreview", missing_fields, "AUTH_REQUIRED")
    return {"username": username, "password": password}, source, "username_password"


def create_client(client_class=None):
    values, _, _ = credentials()
    if client_class is None:
        try:
            import openreview
        except ImportError:
            raise ToolError(
                "Install the official openreview-py dependency",
                "DEPENDENCY_MISSING",
                details={"package": "openreview-py==2.6.2"},
            ) from None
        client_class = openreview.api.OpenReviewClient
    try:
        return client_class(baseurl=BASE_URL, **values)
    except Exception as exc:  # noqa: BLE001 - Translate the official SDK's public exception contract.
        raise translate_error(exc, "login") from None


def _error_payload(exc):
    if exc.args and isinstance(exc.args[0], dict):
        return exc.args[0]
    return {}


def translate_error(exc, operation):
    if isinstance(exc, ToolError):
        return exc
    if isinstance(exc, requests.RequestException):
        return transport_error(exc, BASE_URL)
    payload = _error_payload(exc)
    name = str(payload.get("name", type(exc).__name__))
    status = payload.get("status")
    evidence = {
        "host": "api2.openreview.net",
        "sdk": "openreview-py",
        "operation": operation,
        "error_name": name,
    }
    if isinstance(status, int):
        evidence["http_status"] = status
    if name == "ChallengeRequiredError":
        evidence.update(
            verification="OpenReview ChallengeRequiredError",
            verification_url="https://openreview.net/challenge",
            action="Complete OpenReview's official verification or contact OpenReview support; credentials were supplied through the official client",
        )
        return ToolError(
            "OpenReview site verification required", "CHALLENGE_REQUIRED", details=evidence
        )
    if name == "MfaRequiredError" or type(exc).__name__ == "MfaRequiredException":
        methods = getattr(exc, "mfa_methods", None)
        if methods:
            evidence["available_methods"] = list(methods)
        evidence["action"] = (
            "Run the local interactive configuration/diagnostic command in a TTY to complete OpenReview MFA"
        )
        return ToolError(
            "OpenReview multi-factor authentication is required", "MFA_REQUIRED", details=evidence
        )
    if status == 429:
        evidence["action"] = "Wait for the OpenReview quota reset and retry"
        return ToolError("OpenReview rate limit reached", "RATE_LIMITED", True, evidence)
    if (
        status == 401
        or name in ("AuthenticationError", "LoginError")
        or (operation == "login" and status == 403)
    ):
        evidence["action"] = (
            "Check the OpenReview username/password or session token with config_status"
        )
        return ToolError("OpenReview authentication failed", "AUTH_FAILED", details=evidence)
    if status == 403:
        evidence["action"] = "Check the OpenReview account's permission for this resource"
        return ToolError("OpenReview access denied", "ACCESS_DENIED", details=evidence)
    evidence["action"] = (
        "Inspect OpenReview service status and retry; credentials and response bodies are not logged"
    )
    return ToolError(
        f"OpenReview official client failed during {operation}",
        "SOURCE_UNAVAILABLE",
        status is None or (isinstance(status, int) and status >= 500),
        evidence,
    )


def call(client, operation, method, **kwargs):
    try:
        return method(**kwargs)
    except Exception as exc:  # noqa: BLE001 - Translate the official SDK's public exception contract.
        raise translate_error(exc, operation) from None


@contextmanager
def session(client_class=None):
    client = create_client(client_class)
    try:
        yield client
    finally:
        sdk_session = getattr(client, "session", None)
        if sdk_session and hasattr(sdk_session, "close"):
            sdk_session.close()


def note_json(note):
    if isinstance(note, dict):
        return note
    if hasattr(note, "to_json"):
        return note.to_json()
    raise ToolError("OpenReview returned an unsupported Note object", "SOURCE_FORMAT_ERROR")

"""Safe source diagnostics; never solve or bypass site verification challenges."""

import os
import socket
from urllib.parse import urlparse

import requests

from .common import HTTP, ToolError, list_arg, now, result
from .configuration import token


def response_error(response, url):
    host = urlparse(url).hostname
    headers = {key.lower(): value for key, value in response.headers.items()}
    evidence = {
        "host": host,
        "http_status": response.status_code,
        "content_type": headers.get("content-type", "").split(";")[0][:80],
    }
    for field in ("x-ratelimit-limit", "x-ratelimit-remaining", "retry-after"):
        value = headers.get(field, "")
        if str(value).isdigit():
            evidence[field] = int(value)
    try:
        body = response.json()
    except (ValueError, AttributeError):
        body = {}
    body = body if isinstance(body, dict) else {}
    html = getattr(response, "text", "")[:100000].lower()
    challenge = body.get("name") == "ChallengeRequiredError"
    is_html = evidence["content_type"] == "text/html" or html.lstrip().startswith(
        ("<!doctype html", "<html")
    )
    anubis = is_html and (
        "anubis_challenge" in html or ("anubis" in html and "making sure you're not a bot" in html)
    )
    if challenge or anubis:
        evidence["verification"] = "OpenReview ChallengeRequiredError" if challenge else "Anubis"
        evidence["action"] = (
            "Complete the site's official verification manually or contact its API operator; "
            "an API token alone is not a confirmed fix. Use other available sources."
        )
        if host == "api2.openreview.net":
            evidence["verification_url"] = "https://openreview.net/challenge"
        return ToolError(
            f"{host}: site verification required (HTTP {response.status_code})",
            "CHALLENGE_REQUIRED",
            details=evidence,
        ), evidence
    status = response.status_code
    if status < 400:
        return None, evidence
    code = "SOURCE_HTTP_ERROR"
    if status == 429 or (status == 403 and evidence.get("x-ratelimit-remaining") == 0):
        code = "RATE_LIMITED"
        evidence["action"] = "Wait for the quota reset; check service account rate limits"
    elif status == 401:
        code = "AUTH_REQUIRED"
        evidence["action"] = "Configure or renew this service's credentials; inspect config_status"
    elif status == 403:
        code = "ACCESS_DENIED"
        evidence["action"] = (
            "Check account/resource permissions and contact the service; cause is not proven by HTTP 403 alone"
        )
    return ToolError(
        f"{host}: HTTP {status}", code, status >= 500 or code == "RATE_LIMITED", evidence
    ), evidence


def transport_error(exc, url):
    code, retryable = "SOURCE_UNAVAILABLE", True
    action = "Check DNS, routing and firewall access from the deployment machine"
    if isinstance(exc, requests.exceptions.ProxyError):
        code, retryable = "PROXY_ERROR", False
        action = "Check the configured proxy process/address; compare diagnose_sources routing=direct if permitted"
    elif isinstance(exc, requests.exceptions.SSLError):
        code, retryable = "TLS_ERROR", False
        action = "Check the server CA trust store and system clock; do not disable TLS verification"
    elif isinstance(exc, requests.exceptions.Timeout):
        code = "NETWORK_TIMEOUT"
        action = "Check route/connectivity and retry later"
    else:
        reason = exc
        for _ in range(8):
            if (
                isinstance(reason, socket.gaierror)
                or type(reason).__name__ == "NameResolutionError"
            ):
                code = "DNS_ERROR"
                break
            reason = (
                getattr(reason, "reason", None)
                or getattr(reason, "__cause__", None)
                or getattr(reason, "__context__", None)
            )
            if reason is None:
                break
    return ToolError(
        f"{urlparse(url).hostname}: {code}",
        code,
        retryable,
        {"host": urlparse(url).hostname, "action": action},
    )


def diagnose(params):
    sources = list_arg(params.get("sources")) or ["dblp", "openreview"]
    if set(sources) - {"dblp", "openreview", "github", "crossref"}:
        raise ToolError("Diagnostic sources: dblp, openreview, github, crossref")
    routing = params.get("routing", "configured")
    if routing not in ("configured", "direct"):
        raise ToolError("routing must be configured or direct")
    probes = {
        "dblp": [
            (
                "publications",
                "https://dblp.org/search/publ/api",
                {"q": "stream:conf/iclr:", "format": "json", "h": 1},
            )
        ],
        "github": [("repository", "https://api.github.com/repos/pytorch/pytorch", {})],
        "crossref": [("works", "https://api.crossref.org/works", {"rows": 1})],
    }
    rows = []
    for source in sources:
        if source == "openreview":
            rows.extend(_diagnose_openreview(routing))
            continue
        headers = {}
        credential = token(source) if source == "github" else ""
        if credential:
            headers["Authorization"] = "Bearer " + credential
        for probe, url, query in probes[source]:
            client = HTTP(attempts=1)
            if routing == "direct":
                client.session.trust_env = False
            row = {
                "source": source,
                "probe": probe,
                "checked_at": now(),
                "authenticated": bool(credential),
            }
            try:
                client.get(url, query, headers=headers)
                row.update(state="reachable", error_code=None, evidence=client.last_response)
            except ToolError as exc:
                row.update(
                    state="failed",
                    error_code=exc.code,
                    retryable=exc.retryable,
                    evidence=exc.details,
                )
            finally:
                client.session.close()
            rows.append(row)
    effective = (
        "direct"
        if routing == "direct" or os.getenv("SOTABAND_ACADEMIC_HTTP_PROXY") == "direct"
        else "environment"
    )
    return result(
        {
            "routing": effective,
            "probes": rows,
            "all_reachable": all(row["state"] == "reachable" for row in rows),
        },
        "Diagnostics completed; inspect each probe. No site challenge was bypassed",
    )


def _diagnose_openreview(routing):
    from .openreview_source import call, credentials, session

    checked = now()
    try:
        _, _, method = credentials()
        with session() as client:
            probes = [
                (
                    "notes",
                    client.get_notes,
                    {"content": {"venueid": "ICLR.cc/2025/Conference"}, "limit": 1},
                ),
                ("groups_control", client.get_group, {"id": "ICLR.cc/2025/Conference"}),
            ]
            rows = []
            for probe, operation, kwargs in probes:
                row = {
                    "source": "openreview",
                    "probe": probe,
                    "checked_at": now(),
                    "authenticated": True,
                    "authentication_method": method,
                }
                try:
                    call(client, probe, operation, **kwargs)
                    row.update(
                        state="reachable",
                        error_code=None,
                        evidence={
                            "host": "api2.openreview.net",
                            "sdk": "openreview-py",
                            "api_version": "v2",
                        },
                    )
                except ToolError as exc:
                    row.update(
                        state="failed",
                        error_code=exc.code,
                        retryable=exc.retryable,
                        evidence=exc.details,
                    )
                rows.append(row)
            return rows
    except ToolError as exc:
        return [
            {
                "source": "openreview",
                "probe": "official_client_login",
                "checked_at": checked,
                "authenticated": False,
                "state": "failed",
                "error_code": exc.code,
                "retryable": exc.retryable,
                "evidence": {
                    **exc.details,
                    "sdk": "openreview-py",
                    "api_version": "v2",
                    "routing_note": "OpenReview official client follows the process proxy/NO_PROXY environment"
                    if routing == "direct"
                    else "OpenReview official client follows the process network environment",
                },
            }
        ]

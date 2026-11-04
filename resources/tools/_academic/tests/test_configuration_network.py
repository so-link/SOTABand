import json
import os
from importlib.metadata import version
from types import SimpleNamespace

import pytest
import requests

from resources.tools._academic import configuration, network, openreview_source, papers
from resources.tools._academic.common import HTTP, Store, ToolError, invoke, llm


def response(status=200, body=None, text="", headers=None):
    def payload():
        if body is None:
            raise ValueError("not json")
        return body

    return SimpleNamespace(status_code=status, headers=headers or {}, text=text, json=payload)


def test_persistent_config_protected_isolated_and_redacted(store, monkeypatch):
    secret = "a-short-private-value"
    configuration.save({"github": {"token": secret}}, store)
    assert configuration.token("github") == secret
    path = configuration._path(store)
    if os.name == "nt":
        assert secret.encode() not in path.read_bytes()
        assert path.read_bytes().startswith(b"DPAPI\n")
    else:
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700
    assert not configuration.saved(Store(store.root, "other-user"))
    public = invoke("github", {"mode": "config_status"})
    assert public["data"]["github"]["configured"]
    assert secret not in json.dumps(public)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert not invoke("github", {"mode": "config_reset"})["data"]["github"]["configured"]


def test_temporary_file_does_not_persist_and_context_resets(tmp_path):
    configuration.save({"github": {"token": "saved-key"}})
    path = tmp_path / "temporary.json"
    path.write_text(json.dumps({"github": {"token": "temporary-key"}}), encoding="utf-8")
    with configuration.using_file(path):
        assert configuration.token("github") == "temporary-key"
    assert configuration.token("github") == "saved-key"
    public = invoke("github", {"mode": "config_status", "config_file": str(path)})
    assert public["data"]["github"]["source"] == "temporary_file"
    assert configuration.token("github") == "saved-key"
    assert invoke("github", {"mode": "configure", "config_file": str(path)})["status"] == "success"
    assert configuration.token("github") == "temporary-key"


def test_openreview_official_username_password_client_and_status():
    configuration.save(
        {"openreview": {"username": "reader@example.org", "password": "private-password"}}
    )
    captured = {}

    class Client:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    client = openreview_source.create_client(Client)
    assert isinstance(client, Client)
    assert captured == {
        "baseurl": "https://api2.openreview.net",
        "username": "reader@example.org",
        "password": "private-password",
    }
    public = configuration.status()
    assert public["data"]["openreview"]["authentication_method"] == "username_password"
    assert "private-password" not in json.dumps(public)


def test_official_openreview_dependency_is_pinned():
    assert version("openreview-py") == "2.6.2"


def test_openreview_official_token_client():
    configuration.save({"openreview": {"token": "official-session-token"}})
    captured = {}

    class Client:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    openreview_source.create_client(Client)
    assert captured == {
        "baseurl": "https://api2.openreview.net",
        "token": "official-session-token",
    }


def test_malformed_file_never_echoes_contents(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"api_key":"private-value"', encoding="utf-8")
    output = invoke("github", {"mode": "configure", "config_file": str(path)})
    assert output["data"]["error_code"] == "CONFIG_STORAGE_ERROR"
    assert "private-value" not in json.dumps(output)


def test_custom_endpoint_does_not_reuse_platform_key(monkeypatch):
    from config.settings import settings

    monkeypatch.setattr(settings.llm, "api_key", "platform-private")
    configuration.save({"llm": {"base_url": "https://example.org/v1", "model": "test"}})
    cfg, source = configuration.llm_config()
    assert not cfg.api_key and source == "user_configuration"
    with pytest.raises(ToolError) as err:
        llm([])
    assert err.value.code == "CONFIG_REQUIRED"
    assert err.value.details["required_fields"] == ["api_key"]
    assert settings.llm.api_key == "platform-private"


def test_default_llm_configuration_reuses_platform_settings(monkeypatch):
    from config.settings import settings

    monkeypatch.setattr(settings.llm, "provider", "openai")
    monkeypatch.setattr(settings.llm, "api_key", "platform-private")
    monkeypatch.setattr(settings.llm, "base_url", "https://api.openai.com/v1")
    monkeypatch.setattr(settings.llm, "model", "gpt-4o")
    cfg, source = configuration.llm_config()
    assert source == "platform"
    assert cfg is not settings.llm
    assert (cfg.provider, cfg.api_key, cfg.base_url, cfg.model) == (
        "openai",
        "platform-private",
        "https://api.openai.com/v1",
        "gpt-4o",
    )


def test_llm_scoped_configuration_and_exception_redaction(monkeypatch):
    configuration.save({"llm": {"provider": "deepseek", "api_key": "short-private"}})
    calls = []

    class Client:
        async def chat(self, messages, **kwargs):
            raise RuntimeError("short-private")

        async def aclose(self):
            calls.append("closed")

    def create(cfg):
        assert cfg.api_key == "short-private" and cfg.provider == "deepseek"
        return Client()

    monkeypatch.setattr("core.llm.client.create_llm_client", create)
    with pytest.raises(ToolError) as err:
        llm([])
    assert err.value.code == "LLM_REQUEST_FAILED"
    assert "short-private" not in str(err.value)
    assert calls == ["closed"]


@pytest.mark.parametrize(
    "url",
    [
        "http://example.org/v1",
        "https://user:secret@example.org/v1",
        "https://example.org/v1?key=secret",
    ],
)
def test_reject_unsafe_model_endpoint(url):
    with pytest.raises(ToolError):
        configuration.validate({"llm": {"base_url": url}})


@pytest.mark.parametrize("raw", [False, True])
def test_anubis_http_200_is_challenge_not_bad_json(raw):
    page = response(
        text="<title>Making sure you're not a bot!</title><script id='anubis_challenge'>IP_AND_TOKEN</script>",
        headers={"Content-Type": "text/html; charset=utf-8"},
    )
    client = HTTP(SimpleNamespace(get=lambda *a, **k: page))
    with pytest.raises(ToolError) as err:
        client.get("https://dblp.org/search/publ/api", raw=raw)
    assert err.value.code == "CHALLENGE_REQUIRED" and not err.value.retryable
    assert err.value.details["http_status"] == 200
    assert "IP_AND_TOKEN" not in json.dumps(err.value.details)


def test_openreview_challenge_not_auth_or_rate_limit():
    page = response(
        403,
        {"name": "ChallengeRequiredError", "details": {"challengeUrl": "secret-query"}},
        headers={"X-RateLimit-Limit": "180", "X-RateLimit-Remaining": "179"},
    )
    error, evidence = network.response_error(page, "https://api2.openreview.net/notes")
    assert error.code == "CHALLENGE_REQUIRED"
    assert evidence["x-ratelimit-remaining"] == 179
    assert "secret-query" not in json.dumps(evidence)


def test_source_content_mentioning_challenge_is_not_a_challenge():
    page = response(
        body={"title": "anubis_challenge"},
        text='{"title":"anubis_challenge"}',
        headers={"Content-Type": "application/json"},
    )
    error, _ = network.response_error(page, "https://example.org/works")
    assert error is None


@pytest.mark.parametrize(
    "status,headers,expected",
    [
        (401, {}, "AUTH_REQUIRED"),
        (403, {}, "ACCESS_DENIED"),
        (403, {"X-RateLimit-Remaining": "0"}, "RATE_LIMITED"),
        (429, {}, "RATE_LIMITED"),
    ],
)
def test_http_failures_distinguished(status, headers, expected):
    error, _ = network.response_error(response(status, {}, headers=headers), "https://example.org")
    assert error.code == expected


@pytest.mark.parametrize(
    "exception,expected",
    [
        (requests.exceptions.ProxyError, "PROXY_ERROR"),
        (requests.exceptions.SSLError, "TLS_ERROR"),
        (requests.exceptions.Timeout, "NETWORK_TIMEOUT"),
    ],
)
def test_transport_error_hides_proxy_credentials(exception, expected):
    def request(*args, **kwargs):
        raise exception("https://user:short-private@proxy.test")

    with pytest.raises(ToolError) as err:
        HTTP(SimpleNamespace(get=request), attempts=1).get("https://example.org")
    assert err.value.code == expected
    assert "short-private" not in str(err.value) + json.dumps(err.value.details)


def test_diagnostics_control_probe_and_auth(monkeypatch):
    configuration.save(
        {"openreview": {"username": "reader@example.org", "password": "private-password"}}
    )

    class Client:
        def get_notes(self, **kwargs):
            raise ToolError(
                "Verification required", "CHALLENGE_REQUIRED", details={"http_status": 403}
            )

        def get_group(self, **kwargs):
            return {"id": kwargs["id"]}

    class Context:
        def __enter__(self):
            return Client()

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(openreview_source, "session", lambda: Context())
    output = invoke("papers", {"mode": "diagnose_sources", "sources": ["openreview"]})
    assert output["status"] == "success" and not output["data"]["all_reachable"]
    assert [p["state"] for p in output["data"]["probes"]] == ["failed", "reachable"]
    assert all(p["authentication_method"] == "username_password" for p in output["data"]["probes"])
    assert "private-password" not in json.dumps(output)


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"name": "ChallengeRequiredError", "status": 403}, "CHALLENGE_REQUIRED"),
        ({"name": "AuthenticationError", "status": 401}, "AUTH_FAILED"),
        ({"name": "ForbiddenError", "status": 403}, "ACCESS_DENIED"),
        ({"name": "RateLimitError", "status": 429}, "RATE_LIMITED"),
    ],
)
def test_openreview_sdk_errors_are_classified_and_sanitized(payload, expected):
    payload = {**payload, "message": "private-password", "details": {"token": "private-token"}}
    error = openreview_source.translate_error(Exception(payload), "get_notes")
    assert error.code == expected
    assert "private" not in str(error) + json.dumps(error.details)


def test_openreview_login_forbidden_is_auth_failure():
    error = openreview_source.translate_error(
        Exception({"name": "ForbiddenError", "status": 403}), "login"
    )
    assert error.code == "AUTH_FAILED"


def test_openreview_requires_official_credentials(monkeypatch):
    monkeypatch.delenv("OPENREVIEW_USERNAME", raising=False)
    monkeypatch.delenv("OPENREVIEW_PASSWORD", raising=False)
    monkeypatch.delenv("OPENREVIEW_TOKEN", raising=False)
    with pytest.raises(ToolError) as err:
        openreview_source.credentials()
    assert err.value.code == "AUTH_REQUIRED"
    assert err.value.details["required_fields"] == ["username", "password"]


def test_openreview_search_surfaces_auth_requirement(monkeypatch):
    monkeypatch.delenv("OPENREVIEW_USERNAME", raising=False)
    monkeypatch.delenv("OPENREVIEW_PASSWORD", raising=False)
    monkeypatch.delenv("OPENREVIEW_TOKEN", raising=False)
    output = invoke(
        "papers",
        {
            "mode": "search",
            "source": "openreview",
            "venues": ["ICLR"],
            "since": "2025-01-01",
            "until": "2025-12-31",
        },
    )
    assert output["data"]["error_code"] == "AUTH_REQUIRED"


def test_all_failed_search_preserves_evidence(monkeypatch):
    def dblp(*args, **kwargs):
        raise ToolError("Verification required", "CHALLENGE_REQUIRED", details={"http_status": 200})

    monkeypatch.setattr(papers.Sources, "dblp", dblp)
    output = invoke("papers", {"mode": "search", "source": "dblp", "venues": ["ICLR"]})
    assert output["status"] == "failed"
    assert output["data"]["details"]["sources"][0]["error_code"] == "CHALLENGE_REQUIRED"
    assert not output["data"]["retryable"]

"""User-scoped credentials; no changes to platform settings or other tools."""

from __future__ import annotations

import copy
import json
import os
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from urllib.parse import urlparse

from .common import Store, ToolError, object_arg, result

_override = ContextVar("academic_configuration", default=None)
FIELDS = {
    "llm": {"provider", "base_url", "model", "api_key"},
    "github": {"token"},
    "openreview": {"username", "password", "token"},
}


def validate(value):
    value = object_arg(value, "configuration")
    if set(value) - FIELDS.keys():
        raise ToolError("Configuration supports llm, github and openreview only")
    for service, fields in value.items():
        if not isinstance(fields, dict) or set(fields) - FIELDS[service]:
            raise ToolError(f"Invalid {service} configuration fields")
        if any(not isinstance(v, str) or any(ord(c) < 32 for c in v) for v in fields.values()):
            raise ToolError("Configuration values must be single-line strings")
        if service == "llm" and fields.get("base_url"):
            url = urlparse(fields["base_url"])
            if (
                not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
                or url.scheme not in ("http", "https")
                or (url.scheme == "http" and url.hostname not in ("localhost", "127.0.0.1", "::1"))
            ):
                raise ToolError(
                    "LLM base_url needs HTTPS (HTTP allowed only on loopback), without credentials/query"
                )
    return value


def _dpapi(data, decrypt=False):
    # Windows protects at rest with the current OS account, not the application user ID.
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_char))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    output = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    func = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    func.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(Blob),
    ]
    func.restype = wintypes.BOOL
    if not func(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise ToolError(
            "Windows credential protection failed for this OS account", "CONFIG_STORAGE_ERROR"
        )
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(output.data)


def _path(store):
    return store.home / "private" / "configuration.bin"


def read_file(path):
    try:
        data = Path(path).read_bytes()
        if data.startswith(b"DPAPI\n"):
            if os.name != "nt":
                raise ToolError(
                    "Re-enter Windows-protected credentials on this server", "CONFIG_STORAGE_ERROR"
                )
            data = _dpapi(data[6:], decrypt=True)
        return validate(json.loads(data.decode("utf-8-sig")))
    except (OSError, ValueError, UnicodeError):
        raise ToolError(
            "Configuration file is unreadable or invalid JSON; contents are not logged",
            "CONFIG_STORAGE_ERROR",
        ) from None


def saved(store=None):
    path = _path(store or Store())
    return read_file(path) if path.exists() else {}


def save(value, store=None):
    value = validate(value)
    store = store or Store()
    path = _path(store)
    payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
    if os.name == "nt":
        payload = b"DPAPI\n" + _dpapi(payload)
    with store.lock("configuration", wait=5):
        path.parent.mkdir(mode=0o700, exist_ok=True)
        if os.name != "nt":
            path.parent.chmod(0o700)
        fd, temporary = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)


@contextmanager
def using_file(path=None):
    token = _override.set(read_file(path) if path else None)
    try:
        yield
    finally:
        _override.reset(token)


def resolve(service):
    from config.settings import settings

    temporary = _override.get() or {}
    if service in temporary:
        return temporary[service], "temporary_file"
    stored = saved()
    if service in stored:
        return stored[service], "user_configuration"
    if service == "openreview":
        return {
            "username": os.getenv("OPENREVIEW_USERNAME", ""),
            "password": os.getenv("OPENREVIEW_PASSWORD", ""),
            "token": os.getenv("OPENREVIEW_TOKEN", ""),
        }, "environment"
    if service != "llm":
        return {"token": os.getenv(service.upper() + "_TOKEN", "")}, "environment"
    fields = {
        field: os.getenv("SOTABAND_ACADEMIC_LLM_" + field.upper(), "") for field in FIELDS["llm"]
    }
    if any(fields.values()):
        return fields, "academic_environment"
    return {field: getattr(settings.llm, field) for field in FIELDS["llm"]}, "platform"


def token(service):
    return resolve(service)[0].get("token", "").removeprefix("Bearer ").strip()


def llm_config():
    from config.settings import PROVIDER_PRESETS, resolve_base_url, settings

    values, source = resolve("llm")
    cfg = copy.copy(settings.llm)
    if source != "platform":
        # Never combine a platform key with a tool-specific endpoint or provider.
        cfg.provider = values.get("provider") or "custom"
        cfg.api_key = values.get("api_key", "").strip()
        preset = PROVIDER_PRESETS.get(cfg.provider, {})
        cfg.base_url = values.get("base_url") or resolve_base_url(
            cfg.provider, cfg.api_key, preset.get("default_base_url", "")
        )
        cfg.model = values.get("model") or preset.get("default_model", "")
    return cfg, source


def missing(service, fields, code="CONFIG_REQUIRED"):
    scenarios = {
        "llm": "paper-writing outline/draft/revise and code-reproduction diagnose",
        "github": "GitHub remote source-code content search",
        "openreview": "OpenReview paper search through the official API V2 client",
    }
    return ToolError(
        f"Configure {service} for {scenarios.get(service, service)}; run config_status for setup instructions",
        code,
        details={
            "service": service,
            "required_fields": fields,
            "action": "Run python -m resources.tools._academic.configuration --service "
            + service
            + " on the deployment machine under the same SOTABAND_USER_ID; do not paste secrets into chat",
            "temporary_option": "Pass config_file pointing to a deployment-local JSON file",
        },
    )


def status():
    cfg, source = llm_config()
    missing_fields = [k for k in ("api_key", "base_url", "model") if not getattr(cfg, k)]
    openreview, openreview_source = resolve("openreview")
    openreview_method = (
        "token"
        if openreview.get("token")
        else "username_password"
        if openreview.get("username") and openreview.get("password")
        else None
    )
    openreview_missing = (
        []
        if openreview_method
        else [name for name in ("username", "password") if not openreview.get(name)]
    )
    return result(
        {
            "llm": {
                "configured": not missing_fields,
                "source": source,
                "provider": cfg.provider,
                "model": cfg.model,
                "endpoint_host": urlparse(cfg.base_url).hostname,
                "missing_fields": missing_fields,
            },
            "github": {
                "configured": bool(token("github")),
                "source": resolve("github")[1],
                "required_for": "remote code content search; public repository lookup is optional",
            },
            "openreview": {
                "configured": bool(openreview_method),
                "source": openreview_source,
                "authentication_method": openreview_method,
                "missing_fields": openreview_missing,
                "required_for": "paper search through official openreview-py API V2 client",
            },
            "setup_commands": {
                service: "python -m resources.tools._academic.configuration --service " + service
                for service in ("llm", "github", "openreview")
            },
            "configuration_path": str(_path(Store())),
            "storage": "Windows DPAPI, bound to OS account"
            if os.name == "nt"
            else "plaintext, directory 0700/file 0600; protect server backups",
            "connection_tested": False,
        },
        "Configuration presence only; no paid API request was made",
    )


def execute(params):
    mode = params["mode"]
    if mode == "configure":
        if not params.get("config_file"):
            raise ToolError(
                "configure requires a deployment-local config_file; never paste credentials into chat"
            )
        save(read_file(params["config_file"]))
        return status()
    if mode == "config_reset":
        store = Store()
        with store.lock("configuration", wait=5):
            _path(store).unlink(missing_ok=True)
        return status()
    with using_file(params.get("config_file")):
        return status()


def main():
    import argparse
    import getpass

    parser = argparse.ArgumentParser(
        description="Configure only the four academic tools; input secrets locally"
    )
    parser.add_argument("--service", choices=list(FIELDS), required=True)
    args = parser.parse_args()
    store = Store()
    print("Application user:", store.owner)
    print("Storage:", _path(store))
    value = saved(store)
    if args.service == "llm":
        fields = {
            "provider": input("Provider [deepseek]: ").strip() or "deepseek",
            "base_url": input("Base URL [provider default]: ").strip(),
            "model": input("Model [provider default]: ").strip(),
            "api_key": getpass.getpass("API key (hidden): ").strip(),
        }
    elif args.service == "github":
        fields = {"token": getpass.getpass(args.service + " token (hidden): ").strip()}
    else:
        method = (
            input("Authentication method [username_password/token]: ").strip()
            or "username_password"
        )
        if method == "username_password":
            fields = {
                "username": input("OpenReview username/email: ").strip(),
                "password": getpass.getpass("OpenReview password (hidden): "),
            }
        elif method == "token":
            fields = {"token": getpass.getpass("OpenReview session token (hidden): ").strip()}
        else:
            parser.error("Authentication method must be username_password or token")
    secret = fields.get("api_key") or fields.get("token") or fields.get("password")
    if not secret or (
        args.service == "openreview" and not fields.get("token") and not fields.get("username")
    ):
        parser.error("Empty secret: configuration was not changed")
    value[args.service] = fields
    save(value, store)
    print("Saved. Other tools and platform configuration were not changed.")


if __name__ == "__main__":
    main()

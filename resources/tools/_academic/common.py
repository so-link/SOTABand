"""Storage, networking and platform adapters for the academic tools."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parents[3]


class ToolError(Exception):
    def __init__(self, message, code="INVALID_INPUT", retryable=False, details=None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.details = details or {}


def now():
    return datetime.now(UTC).isoformat()


def uid(prefix):
    return prefix + "-" + uuid.uuid4().hex


def required(params, name):
    value = params.get(name)
    if value is None or value == "":
        raise ToolError(f"Missing parameter: {name}")
    return value


def integer(value, name, low=1, high=100):
    if isinstance(value, bool):
        raise ToolError(f"{name} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ToolError(f"{name} must be an integer") from None
    if str(number) != str(value).strip() or not low <= number <= high:
        raise ToolError(f"{name} must be between {low} and {high}")
    return number


def object_arg(value, name):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError as exc:
            raise ToolError(f"{name}: invalid JSON") from exc
    if not isinstance(value, dict):
        raise ToolError(f"{name} must be an object or JSON object string")
    return value


def list_arg(value):
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        if value.strip().startswith("["):
            try:
                result = json.loads(value)
            except ValueError as exc:
                raise ToolError("Invalid JSON array") from exc
            if not isinstance(result, list):
                raise ToolError("Expected a JSON array")
            return result
        return [s.strip() for s in value.split(",") if s.strip()]
    raise ToolError("Expected a list or comma-separated string")


def result(data=None, message="Completed", fmt="text"):
    data = dict(data or {})
    if fmt == "text":
        data.setdefault("text", json.dumps(data, ensure_ascii=False, indent=2) if data else message)
    return {"status": "success", "output_format": fmt, "message": message, "data": data}


def table(items, columns, message="Completed", **extra):
    return result(
        {
            "columns": columns,
            "rows": [[i.get(c) for c in columns] for i in items],
            "items": items,
            **extra,
        },
        message,
        "table",
    )


def failure(exc):
    # Do not include request URLs or environment values in public errors.
    import traceback

    from core.security.secrets import scrub_mapping, scrub_text

    data = {
        "error_code": getattr(exc, "code", "FILE_NOT_FOUND" if isinstance(exc, FileNotFoundError) else "INTERNAL_ERROR"),
        "retryable": getattr(exc, "retryable", False),
        "text": scrub_text(str(exc)),
    }
    if getattr(exc, "details", None):
        data["details"] = scrub_mapping(exc.details)
    if not isinstance(exc, ToolError):
        data["traceback"] = scrub_text(traceback.format_exc(), max_len=2500)
    return {
        "status": "failed",
        "output_format": "text",
        "message": scrub_text(str(exc)),
        "data": data,
    }


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


class Store:
    def __init__(self, root=None, owner=None):
        from core.user_context import get_current_user_id

        self.root = Path(root or os.getenv("SOTABAND_ACADEMIC_DATA", ROOT / "data/academic"))
        self.root = self.root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.owner = owner or get_current_user_id()
        self.home = self.root / hashlib.sha256(self.owner.encode()).hexdigest()[:20]
        self.home.mkdir(exist_ok=True)
        self.db = self.root / "state.sqlite3"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS records (
                    owner TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL,
                    revision INTEGER NOT NULL, body TEXT NOT NULL, updated REAL NOT NULL,
                    PRIMARY KEY(owner, kind, id));
                CREATE TABLE IF NOT EXISTS locks (
                    key TEXT PRIMARY KEY, token TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS gpu_leases (
                    device TEXT PRIMARY KEY, owner TEXT NOT NULL, job TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS paper_doi ON records(owner, json_extract(body, '$.doi'))
                    WHERE kind='paper';
                CREATE INDEX IF NOT EXISTS paper_title ON records(owner, json_extract(body, '$.title_key'),
                    json_extract(body, '$.year')) WHERE kind='paper';
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db, timeout=30)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA busy_timeout=30000")
            with db:
                yield db
        finally:
            db.close()

    def get(self, kind, ident):
        with self.connect() as db:
            row = db.execute(
                "SELECT body,revision FROM records WHERE owner=? AND kind=? AND id=?",
                (self.owner, kind, str(ident)),
            ).fetchone()
        if not row:
            raise ToolError(f"{kind} not found: {ident}", "NOT_FOUND")
        return {**json.loads(row[0]), "revision": row[1]}

    def find(self, kind, limit=1000):
        with self.connect() as db:
            rows = db.execute(
                "SELECT body,revision FROM records WHERE owner=? AND kind=? "
                "ORDER BY updated DESC LIMIT ?",
                (self.owner, kind, limit),
            ).fetchall()
        return [{**json.loads(b), "revision": v} for b, v in rows]

    def put(self, kind, obj, expected=None):
        obj = dict(obj)
        ident = obj.setdefault("id", uid(kind))
        obj["updated_at"] = now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT revision FROM records WHERE owner=? AND kind=? AND id=?",
                (self.owner, kind, ident),
            ).fetchone()
            revision = row[0] if row else 0
            if expected is not None and revision != expected:
                raise ToolError("Record changed; reload before updating", "REVISION_CONFLICT")
            obj["revision"] = revision + 1
            db.execute(
                "INSERT OR REPLACE INTO records VALUES (?,?,?,?,?,?)",
                (
                    self.owner,
                    kind,
                    ident,
                    revision + 1,
                    json.dumps(obj, ensure_ascii=False),
                    time.time(),
                ),
            )
        return obj

    def delete(self, kind, ident):
        self.get(kind, ident)
        with self.connect() as db:
            db.execute(
                "DELETE FROM records WHERE owner=? AND kind=? AND id=?", (self.owner, kind, ident)
            )

    @contextmanager
    def lock(self, key, seconds=60, renew=True, wait=0):
        key = self.owner + ":" + key
        token = uuid.uuid4().hex
        deadline = time.monotonic() + wait
        while True:
            try:
                with self.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    db.execute("DELETE FROM locks WHERE key=? AND expires<?", (key, time.time()))
                    db.execute(
                        "INSERT INTO locks VALUES (?,?,?)", (key, token, time.time() + seconds)
                    )
                break
            except sqlite3.IntegrityError:
                if time.monotonic() >= deadline:
                    raise ToolError("Operation already running", "BUSY", True) from None
                time.sleep(0.05)
        stopped = threading.Event()
        lost = threading.Event()

        def heartbeat():
            while not stopped.wait(max(seconds / 3, 0.05)):
                try:
                    with self.connect() as db:
                        cursor = db.execute(
                            "UPDATE locks SET expires=? WHERE key=? AND token=?",
                            (time.time() + seconds, key, token),
                        )
                        if cursor.rowcount != 1:
                            lost.set()
                            return
                except sqlite3.Error:
                    lost.set()
                    return

        thread = threading.Thread(target=heartbeat, daemon=True) if renew else None
        if thread:
            thread.start()
        try:
            yield
            if lost.is_set():
                raise ToolError("Operation lease lost", "LEASE_LOST", True)
        finally:
            stopped.set()
            if thread:
                thread.join(timeout=35)
            with self.connect() as db:
                db.execute("DELETE FROM locks WHERE key=? AND token=?", (key, token))

    def folder(self, kind, ident):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", str(ident)):
            raise ToolError("Invalid record identifier")
        path = self.home / kind / ident
        path.mkdir(parents=True, exist_ok=True)
        return path


class HTTP:
    def __init__(self, session=None, attempts=3):
        self.session = session or requests.Session()
        self.attempts = attempts
        self.last_response = {}
        if os.getenv("SOTABAND_ACADEMIC_HTTP_PROXY") == "direct":
            self.session.trust_env = False

    def get(self, url, params=None, headers=None, raw=False):
        from .network import response_error, transport_error

        if urlparse(url).scheme != "https":
            raise ToolError("Source URL must use HTTPS")
        self.last_response = {}
        for attempt in range(self.attempts):
            try:
                response = self.session.get(
                    url,
                    params=params,
                    headers={"User-Agent": "SOTABand-Academic/1.0", **(headers or {})},
                    timeout=(10, 45),
                )
            except requests.RequestException as exc:
                error = transport_error(exc, url)
                if error.retryable and attempt + 1 < self.attempts:
                    time.sleep(2**attempt)
                    continue
                raise error from None
            error, self.last_response = response_error(response, url)
            if error and error.code == "CHALLENGE_REQUIRED":
                raise error
            if response.status_code in (429, 502, 503, 504) and attempt + 1 < self.attempts:
                retry = response.headers.get("Retry-After", "2")
                time.sleep(min(float(retry) if retry.isdigit() else 2, 15))
                continue
            if error:
                raise error
            if raw:
                return response.text
            try:
                return response.json()
            except ValueError:
                raise ToolError(
                    "Source returned invalid JSON",
                    "SOURCE_FORMAT_ERROR",
                    details=self.last_response,
                ) from None


def command(args, cwd=None, timeout=60, include_stderr=False, env=None):
    try:
        proc = subprocess.run(
            args,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except FileNotFoundError:
        raise ToolError(
            f"Required executable unavailable: {args[0]}", "DEPENDENCY_MISSING"
        ) from None
    except subprocess.TimeoutExpired:
        raise ToolError(f"Command timed out: {args[0]}", "TIMEOUT", True) from None
    if proc.returncode:
        raise ToolError(f"{args[0]} failed: {proc.stderr[-1500:]}", "COMMAND_FAILED")
    return (proc.stdout + (proc.stderr if include_stderr else "")).strip()


def llm(messages):
    import asyncio

    from core.llm.client import create_llm_client

    from .configuration import llm_config, missing, validate

    cfg, _ = llm_config()
    fields = [field for field in ("api_key", "base_url", "model") if not getattr(cfg, field)]
    if fields:
        raise missing("llm", fields)
    validate({"llm": {"base_url": cfg.base_url}})

    async def call():
        client = create_llm_client(cfg)
        try:
            return await client.chat(messages, temperature=0.2, max_tokens=8000)
        except Exception as exc:  # noqa: BLE001 - SDK errors may contain arbitrary provider credentials.
            status = getattr(exc, "status_code", None)
            code = (
                "LLM_AUTH_ERROR"
                if status in (401, 403)
                else "LLM_RATE_LIMITED"
                if status == 429
                else "LLM_REQUEST_FAILED"
            )
            raise ToolError(
                "LLM request failed; check credentials, endpoint, model and provider availability",
                code,
                status == 429 or (isinstance(status, int) and status >= 500),
                {
                    "http_status": status,
                    "action": "Check config_status; do not put secrets in error reports",
                },
            ) from None
        finally:
            await client.aclose()

    return asyncio.run(call())


def register_artifact(folder, name):
    import asyncio

    from core.api import get_api

    folder = Path(folder).resolve()
    files = [p for p in folder.rglob("*") if p.is_file() and not p.is_symlink()]
    return asyncio.run(
        get_api("api-data-register").call(
            id="academic-" + digest(str(folder))[:24],
            name=name,
            data_path=str(folder),
            file_count=len(files),
            total_size=sum(p.stat().st_size for p in files),
            formats=sorted({p.suffix.lstrip(".") for p in files}),
            raw_md="# " + name + "\n\nAcademic tool exported artifacts.",
        )
    )


def invoke(module, params, _recovery=None):
    import importlib

    from . import configuration, recovery

    try:
        if params.get("mode") == "recover" or recovery.is_request(params.get("request")):
            return recovery.execute(module, params)
        params = recovery.complete_input(module, params)
        if params.get("request"):
            mode = params.get("mode")
            if module == "github" and mode in (None, "search", "inspect") and not params.get("query") and not params.get("repository"):
                params = {**params, "mode": "find"}
            elif module == "writing" and mode in (None, "create"):
                params = {**params, "mode": "compose"}
            elif module == "reproduction" and mode in (None, "inspect") and not re.search(r"检查|查看|检测|inspect", params["request"], re.IGNORECASE):
                params = {**params, "mode": "reproduce"}
        if params.get("mode") in ("config_status", "configure", "config_reset"):
            return configuration.execute(params)
        with configuration.using_file(params.get("config_file")):
            response = importlib.import_module("resources.tools._academic." + module).execute(
                params
            )
        if response.get("output_format") == "file" and params.get("register_output") in (
            True,
            "true",
            "True",
        ):
            try:
                path = Path(response["data"]["file_path"])
                response["data"]["registration"] = register_artifact(path.parent, path.stem)
            except Exception as exc:  # noqa: BLE001 - Keep a valid export if platform registration fails.
                response["data"]["registration_error"] = failure(exc)["message"]
        return recovery.attach(module, params, response, _recovery)
    except Exception as exc:  # noqa: BLE001 - Standard tool boundary must return structured failures.
        response = failure(exc)
        if params.get("mode") == "recover" or recovery.is_request(params.get("request")):
            return response
        return recovery.attach(
            module, params, response, _recovery, getattr(exc, "resume_params", None)
        )

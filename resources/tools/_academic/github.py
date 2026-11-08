"""Evidence-based GitHub repository and source discovery."""

import base64
import math
import re
from urllib.parse import urlparse

from .common import (
    HTTP,
    Store,
    ToolError,
    atomic_json,
    integer,
    list_arg,
    now,
    required,
    result,
    table,
    uid,
)
from .configuration import missing, token


def repository(value):
    value = str(value).strip().removesuffix(".git").rstrip("/")
    if value.startswith("https://"):
        parsed = urlparse(value)
        if parsed.hostname != "github.com":
            raise ToolError("Expected a github.com repository URL")
        value = parsed.path.strip("/")
    if not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}", value
    ) or value.split("/")[-1] in (".", ".."):
        raise ToolError("Repository must be owner/name or https://github.com/owner/name")
    return value


def reproduction_caveats(readme):
    """Quote explicit upstream limitations; absence is not proof of reproducibility."""
    return [
        line.strip()[:1500] for line in readme.splitlines()
        if re.search(r"\bnot\s+(?:intended|designed)\s+(?:for\s+(?:the\s+)?reproduction|to\s+reproduce)\b", line, re.IGNORECASE)
    ][:10]


class GitHub:
    def __init__(self, http=None):
        self.http = http or HTTP()

    def get(self, endpoint, params=None):
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        credential = token("github")
        if credential:
            headers["Authorization"] = "Bearer " + credential
        try:
            return self.http.get("https://api.github.com/" + endpoint, params=params, headers=headers)
        except ToolError as exc:
            exc.details = {**exc.details, "endpoint": endpoint[:300]}
            raise

    def search(self, query, limit=10, page=1):
        data = self.get("search/repositories", {"q": query, "per_page": limit, "page": page})
        rows = []
        for repo in data.get("items", []):
            rows.append(
                {
                    "repository": repo["full_name"],
                    "url": repo["html_url"],
                    "description": repo.get("description"),
                    "stars": repo.get("stargazers_count"),
                    "language": repo.get("language"),
                    "updated": repo.get("pushed_at"),
                    "license": (repo.get("license") or {}).get("spdx_id"),
                    "archived": repo.get("archived", False),
                    "relationship": "unknown",
                }
            )
        return rows, data

    def inspect(self, name, ref=None, paper_url="", paper_title=""):
        name = repository(name)
        meta = self.get("repos/" + name)
        head = self.get(f"repos/{name}/commits/{ref or meta['default_branch']}")
        sha = head["sha"]
        tree = self.get(f"repos/{name}/git/trees/{sha}", {"recursive": "1"})
        files = [r["path"] for r in tree.get("tree", []) if r.get("type") == "blob"]
        readme = ""
        warnings = []
        if tree.get("truncated"):
            warnings.append("Repository tree is incomplete; file search may omit results")
        try:
            data = self.get(f"repos/{name}/readme", {"ref": sha})
            if data.get("encoding") == "base64":
                readme = base64.b64decode(data["content"]).decode("utf-8", errors="replace")[
                    :100000
                ]
        except ToolError as exc:
            warnings.append(str(exc))
        evidence = []
        relationship = "unknown"
        if paper_url and paper_url in readme:
            evidence.append(
                {
                    "kind": "readme_paper_link",
                    "url": paper_url,
                    "source": f"https://github.com/{name}/tree/{sha}",
                }
            )
            relationship = "paper_linked"
        title_terms = [
            term
            for term in re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", paper_title.casefold())
            if term not in {"the", "and", "for", "with", "from", "using", "via"}
        ]
        title_hits = [term for term in title_terms if term in readme.casefold()]
        if title_terms and len(title_hits) >= max(2, math.ceil(len(title_terms) * 0.7)):
            evidence.append(
                {
                    "kind": "readme_paper_title",
                    "matched_terms": title_hits,
                    "source": f"https://github.com/{name}/tree/{sha}",
                }
            )
            if relationship == "unknown":
                relationship = "paper_title_matched"
        if re.search(r"\bofficial\s+(?:code|implementation|repository)\b", readme, re.IGNORECASE):
            relationship = "author_claimed"
            evidence.append(
                {"kind": "readme_claim", "source": f"https://github.com/{name}/tree/{sha}"}
            )
        snapshot = {
            "id": uid("repo"),
            "repository": name,
            "url": meta["html_url"],
            "commit": sha,
            "license": (meta.get("license") or {}).get("spdx_id"),
            "readme": readme,
            "files": files,
            "tree_truncated": tree.get("truncated", False),
            "entrypoints": [
                f
                for f in files
                if re.search(r"(^|/)(train|eval|evaluate|infer|predict|main|run|execute)[^/]*\.py$", f)
            ],
            "dependency_files": [
                f
                for f in files
                if re.search(
                    r"(^|/)(requirements[^/]*\.(txt|ya?ml)|pyproject\.toml|setup\.(py|cfg)|environment[^/]*\.ya?ml|Dockerfile)$",
                    f,
                    re.IGNORECASE,
                )
            ],
            "relationship": relationship,
            "evidence": evidence,
            "warnings": warnings,
            "fetched_at": now(),
            "upstream_caveats": reproduction_caveats(readme),
        }
        snapshot["warnings"].extend("Upstream reproduction limitation: " + text for text in snapshot["upstream_caveats"])
        snapshot["reproducibility"] = reproducibility(snapshot)
        return snapshot


def reproducibility(snapshot):
    files = snapshot.get("files", [])
    lowered = [path.casefold() for path in files]
    readme = snapshot.get("readme", "")

    def has(pattern):
        return any(re.search(pattern, path) for path in lowered)

    signals = {
        "entrypoint": bool(snapshot.get("entrypoints")),
        "dependencies": bool(snapshot.get("dependency_files")),
        "container": has(r"(^|/)dockerfile$|docker-compose|\.devcontainer/"),
        "configuration": has(r"(^|/)(config|configs)/|\.(ya?ml|toml|json)$"),
        "tests": has(r"(^|/)(test|tests)/|(^|/)test_[^/]+\.py$"),
        "checkpoint_or_release": has(r"checkpoint|pretrain|weight|\.ckpt$|\.pth$|\.pt$"),
        "data_instructions": bool(
            re.search(r"\b(dataset|data preparation|download data)\b", readme, re.IGNORECASE)
        ),
        "run_commands": bool(
            re.search(
                r"```(?:bash|shell|sh)?\s*[\s\S]{0,2000}?(?:python|torchrun|accelerate|bash)\s+",
                readme,
                re.IGNORECASE,
            )
        ),
        "license": bool(snapshot.get("license")),
        "paper_evidence": snapshot.get("relationship") != "unknown",
    }
    weights = {
        "entrypoint": 15,
        "dependencies": 15,
        "container": 10,
        "configuration": 10,
        "tests": 10,
        "checkpoint_or_release": 10,
        "data_instructions": 10,
        "run_commands": 10,
        "license": 5,
        "paper_evidence": 5,
    }
    score = sum(weights[key] for key, available in signals.items() if available)
    gaps = [key for key, available in signals.items() if not available]
    commands = []
    for block in re.findall(r"```(?:bash|shell|sh)?\s*\n([\s\S]*?)```", readme, re.IGNORECASE):
        for line in block.splitlines():
            line = line.strip()
            if re.match(r"(?:python|python3|torchrun|accelerate|bash)\s+", line):
                commands.append(line[:500])
    return {
        "score": score,
        "signals": signals,
        "gaps": gaps,
        "candidate_commands": list(dict.fromkeys(commands))[:20],
        "ready_for_automatic_reproduction": all(
            signals[key] for key in ("entrypoint", "dependencies", "run_commands")
        ),
    }


def candidate_score(row, snapshot=None):
    score = min(20, int(math.log10(max(int(row.get("stars") or 0), 1)) * 5))
    if row.get("license"):
        score += 5
    if not row.get("archived"):
        score += 5
    if snapshot:
        score += snapshot["reproducibility"]["score"]
        score += {"author_claimed": 30, "paper_linked": 25, "paper_title_matched": 15}.get(
            snapshot.get("relationship"), 0
        )
    return score


def paper_metadata_evidence(row, paper_title):
    """Score cheap GitHub search metadata before spending API calls on inspection."""
    title_terms = [
        term
        for term in re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", paper_title.casefold())
        if term not in {"the", "and", "for", "with", "from", "using", "via"}
    ]
    metadata = " ".join(
        str(row.get(field) or "") for field in ("repository", "description")
    ).casefold()
    title_hits = [term for term in title_terms if term in metadata]
    title_matched = bool(title_terms) and len(title_hits) >= max(
        2, math.ceil(len(title_terms) * 0.7)
    )
    official_claim = bool(
        re.search(r"\bofficial\s+(?:code|implementation|repository)\b", metadata)
    )
    if title_matched and official_claim:
        return "metadata_author_claimed", 50, {
            "kind": "repository_description_claim",
            "matched_terms": title_hits,
            "source": row.get("url"),
        }
    if title_matched:
        return "metadata_title_matched", 20, {
            "kind": "repository_description_title",
            "matched_terms": title_hits,
            "source": row.get("url"),
        }
    return "unverified", 0, None


def reproduction_handoff(snapshot):
    commands = snapshot.get("reproducibility", {}).get("candidate_commands", [])
    command = commands[0] if commands else ""
    return {
        "tool_id": "code-reproduction",
        "mode": "preflight",
        "params": {
            "snapshot_id": snapshot["id"],
            "command": command,
            "check_runtime": False,
        },
        "run": {
            "tool_id": "code-reproduction",
            "mode": "reproduce",
            "params": {
                "snapshot_id": snapshot["id"],
                "request": (
                    f"使用1张GPU复现固定仓库 {snapshot['repository']}"
                    + (f"，运行 {command}" if command else "")
                ),
            },
        },
    }


def parse_discovery_request(value):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        raise ToolError("请描述要查找的论文或 GitHub 仓库")
    github_url = re.search(r"https://github\.com/[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", text)
    if github_url:
        return {"kind": "repository", "repository": repository(github_url.group(0)), "request": text}
    repo_name = re.search(
        r"(?<![A-Za-z0-9_.-])([A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100})(?![A-Za-z0-9_.-])",
        text,
    )
    if repo_name and any(word in text.casefold() for word in ("仓库", "代码库", "repository", "repo")):
        return {"kind": "repository", "repository": repository(repo_name.group(1)), "request": text}
    title_match = re.search(r"(?:论文)?《([^》]+)》", text)
    if not title_match:
        title_match = re.search(r"(?:论文|paper)\s*[\"“]([^\"”]+)[\"”]", text, re.IGNORECASE)
    if title_match:
        title = title_match.group(1).strip()
    else:
        title = re.sub(
            r"^(?:请)?(?:帮我)?(?:查找|搜索|寻找|检索|找到|find|search for)\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )
        title = re.sub(r"(?:的)?(?:官方)?(?:开源)?(?:代码|仓库|实现)\s*[。.!！]?$", "", title).strip()
        title = re.sub(r"^论文\s*", "", title).strip()
    if len(title) < 3:
        raise ToolError("无法识别论文标题；请使用《论文完整标题》")
    return {"kind": "paper", "paper_title": title, "request": text}


def find_paper_code(client, store, params):
    title = str(required(params, "paper_title")).strip()
    keywords = str(params.get("keywords", "")).strip()
    query = f'"{title}" in:readme {keywords}'.strip()
    limit = integer(params.get("limit", 10), "limit", 1, 30)
    inspect_limit = integer(params.get("inspect_limit", min(5, limit)), "inspect_limit", 1, limit)
    candidates, meta = client.search(query, limit, 1)
    queries = [query]
    # Inspect the strongest title/official-description matches first. GitHub's
    # unauthenticated core quota is small, so API relevance order alone wastes
    # calls on popular general-purpose libraries.
    candidates.sort(
        key=lambda item: (
            paper_metadata_evidence(item, title)[1],
            candidate_score(item),
        ),
        reverse=True,
    )
    ranked = []
    snapshots = {}
    rate_limit_error = None
    for index, candidate in enumerate(candidates):
        snapshot = None
        error = None
        error_code = None
        error_details = None
        metadata_relationship, metadata_bonus, metadata_evidence = paper_metadata_evidence(
            candidate, title
        )
        verification_status = "not_inspected"
        if index < inspect_limit and rate_limit_error is None:
            try:
                snapshot = client.inspect(
                    candidate["repository"],
                    paper_url=params.get("paper_url", ""),
                    paper_title=title,
                )
                snapshot = store.put("repository", snapshot)
                snapshots[snapshot["id"]] = snapshot
                verification_status = "verified"
            except ToolError as exc:
                error = str(exc)
                error_code = exc.code
                error_details = exc.details
                verification_status = "rate_limited" if exc.code == "RATE_LIMITED" else "failed"
                if exc.code == "RATE_LIMITED":
                    rate_limit_error = exc
        elif index < inspect_limit:
            verification_status = "skipped_after_rate_limit"
        ranked.append(
            {
                **candidate,
                "snapshot_id": snapshot.get("id") if snapshot else None,
                "commit": snapshot.get("commit") if snapshot else None,
                "relationship": (
                    snapshot.get("relationship", metadata_relationship)
                    if snapshot
                    else metadata_relationship
                ),
                "metadata_evidence": metadata_evidence,
                "reproducibility_score": snapshot.get("reproducibility", {}).get("score") if snapshot else None,
                "entrypoints": snapshot.get("entrypoints", []) if snapshot else [],
                "candidate_commands": snapshot.get("reproducibility", {}).get("candidate_commands", []) if snapshot else [],
                "gaps": snapshot.get("reproducibility", {}).get("gaps", []) if snapshot else [],
                "verification_status": verification_status,
                "verification_error": error,
                "verification_error_code": error_code,
                "verification_error_details": error_details,
                "score": candidate_score(candidate, snapshot) + metadata_bonus,
            }
        )
    ranked.sort(key=lambda item: item["score"], reverse=True)
    recommended = next((item for item in ranked if item.get("snapshot_id")), None)
    provisional = next(
        (item for item in ranked if item.get("relationship") != "unverified"),
        None,
    )
    handoff = reproduction_handoff(snapshots[recommended["snapshot_id"]]) if recommended else None
    message = (
        "Completed"
        if recommended
        else "Candidate search completed; repository verification is incomplete"
    )
    return table(
        ranked,
        [
            "repository",
            "score",
            "relationship",
            "verification_status",
            "reproducibility_score",
            "stars",
            "license",
            "snapshot_id",
            "verification_error",
            "url",
        ],
        message=message,
        query=query,
        queries=queries,
        total=meta.get("total_count"),
        incomplete=meta.get("incomplete_results", False),
        recommended=recommended,
        provisional_recommendation=provisional if not recommended else None,
        reproduction_handoff=handoff,
        verification_summary={
            "requested": min(inspect_limit, len(candidates)),
            "verified": sum(bool(item.get("snapshot_id")) for item in ranked),
            "rate_limited": rate_limit_error is not None,
            "authenticated": bool(token("github")),
        },
        next_action=(
            "Configure a GitHub token, then run the same request again to fix commits and assess reproducibility."
            if rate_limit_error
            else None
        ),
        warning="README evidence is repository-authored metadata, not independent authorship proof",
    )


def execute(params):
    mode = params.get("mode", "search")
    store = Store()
    client = GitHub()
    if mode == "find":
        parsed = parse_discovery_request(required(params, "request"))
        if parsed["kind"] == "paper":
            response = find_paper_code(client, store, {**params, **parsed})
            response["data"]["interpreted_request"] = parsed
            return response
        snapshot = store.put(
            "repository",
            client.inspect(
                parsed["repository"],
                params.get("ref"),
                params.get("paper_url", ""),
                params.get("paper_title", ""),
            ),
        )
        return result(
            {
                **snapshot,
                "interpreted_request": parsed,
                "reproduction_handoff": reproduction_handoff(snapshot),
            },
            f"Repository snapshot: {snapshot['id']} @ {snapshot['commit']}",
        )
    if mode == "search":
        query = str(required(params, "query"))
        limit = integer(params.get("limit", 10), "limit")
        rows, meta = client.search(query, limit, integer(params.get("page", 1), "page", 1, 10))
        return table(
            rows,
            ["repository", "description", "stars", "language", "license", "url"],
            total=meta.get("total_count"),
            incomplete=meta.get("incomplete_results", False),
            query=query,
            authenticated=bool(token("github")),
        )
    if mode == "inspect":
        snapshot = client.inspect(
            required(params, "repository"),
            params.get("ref"),
            params.get("paper_url", ""),
            params.get("paper_title", ""),
        )
        snapshot = store.put("repository", snapshot)
        return result(
            {**snapshot, "reproduction_handoff": reproduction_handoff(snapshot)},
            f"Repository snapshot: {snapshot['id']} @ {snapshot['commit']}",
        )
    if mode == "paper_code":
        return find_paper_code(client, store, params)
    if mode == "compare":
        snapshots = [
            store.get("repository", ident) for ident in list_arg(params.get("snapshot_ids"))
        ]
        if len(snapshots) < 2:
            raise ToolError("Provide at least two snapshot_ids")
        rows = [
            {
                "snapshot_id": item["id"],
                "repository": item["repository"],
                "commit": item["commit"],
                "relationship": item["relationship"],
                "score": item["reproducibility"]["score"],
                "ready": item["reproducibility"]["ready_for_automatic_reproduction"],
                "entrypoints": item["entrypoints"],
                "gaps": item["reproducibility"]["gaps"],
            }
            for item in snapshots
        ]
        rows.sort(key=lambda item: item["score"], reverse=True)
        return table(
            rows,
            ["repository", "commit", "relationship", "score", "ready", "entrypoints", "gaps"],
        )
    if mode == "search_code":
        query = str(required(params, "query"))
        if params.get("snapshot_id"):
            snapshot = store.get("repository", params["snapshot_id"])
            paths = [p for p in snapshot["files"] if query.casefold() in p.casefold()]
            return table(
                [
                    {
                        "path": p,
                        "commit": snapshot["commit"],
                        "url": f"{snapshot['url']}/blob/{snapshot['commit']}/{p}",
                    }
                    for p in paths
                ],
                ["path", "commit", "url"],
                search_scope="snapshot_file_paths",
                incomplete=snapshot.get("tree_truncated", False),
            )
        if not token("github"):
            raise missing("github", ["token"], "AUTH_REQUIRED")
        if params.get("repository"):
            query += " repo:" + repository(params["repository"])
        data = client.get(
            "search/code",
            {
                "q": query,
                "per_page": integer(params.get("limit", 10), "limit"),
                "page": integer(params.get("page", 1), "page", 1, 10),
            },
        )
        return table(
            [
                {
                    "path": i["path"],
                    "repository": i["repository"]["full_name"],
                    "url": i["html_url"],
                    "blob_sha": i["sha"],
                }
                for i in data.get("items", [])
            ],
            ["repository", "path", "blob_sha", "url"],
            incomplete=data.get("incomplete_results", False),
            search_scope="default_branch",
        )
    if mode == "export":
        snapshot = store.get("repository", required(params, "snapshot_id"))
        path = store.folder("repositories", snapshot["id"]) / "snapshot.json"
        atomic_json(path, snapshot)
        return result({"file_path": str(path)}, "Snapshot exported", "file")
    raise ToolError(f"Unknown mode: {mode}")

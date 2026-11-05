import base64
import time
from types import SimpleNamespace

import pytest

from resources.tools._academic import catalog, configuration, github, papers
from resources.tools._academic.common import HTTP, Store, ToolError, integer, invoke


@pytest.mark.parametrize("value", [True, 1.5, "1.0", -1, None, "bad"])
def test_integer_validation(value):
    with pytest.raises(ToolError):
        integer(value, "limit")


def test_store_isolation_conflict_and_traversal(store):
    first = store.put("paper", {"id": "one", "title": "Original"})
    other = Store(store.root, "another-user")
    with pytest.raises(ToolError, match="not found"):
        other.get("paper", "one")
    store.put("paper", {**first, "title": "Changed"}, expected=1)
    with pytest.raises(ToolError, match="changed"):
        store.put("paper", first, expected=1)
    with pytest.raises(ToolError):
        store.folder("runs", "../../escape")


def test_lock_renewal_and_release(store):
    with store.lock("operation", seconds=0.3):
        time.sleep(0.7)
        with pytest.raises(ToolError, match="already running"), store.lock("operation"):
            pass
    with store.lock("operation"):
        pass


def test_expired_lease_recoverable(store):
    with store.connect() as db:
        db.execute(
            "INSERT INTO locks VALUES (?,?,?)", (store.owner + ":dead", "gone", time.time() - 1)
        )
    with store.lock("dead"):
        pass


def test_http_rate_limit_and_error(monkeypatch):
    responses = [
        SimpleNamespace(status_code=429, headers={"Retry-After": "0"}),
        SimpleNamespace(status_code=200, headers={}, json=lambda: {"ok": True}),
    ]
    client = HTTP(SimpleNamespace(get=lambda *args, **kwargs: responses.pop(0)))
    monkeypatch.setattr("resources.tools._academic.common.time.sleep", lambda _: None)
    assert client.get("https://example.org")["ok"]
    with pytest.raises(ToolError, match="HTTPS"):
        client.get("http://example.org")


@pytest.mark.parametrize("value", ["https://example.org/a/b", "a/b/c", "-x", "a/../b"])
def test_repository_rejects_invalid(value):
    with pytest.raises(ToolError):
        github.repository(value)


def test_github_snapshot_pins_tree_and_evidence():
    class FakeHTTP:
        def get(self, url, params=None, headers=None):
            if "/commits/" in url:
                return {"sha": "a" * 40}
            if "/git/trees/" in url:
                assert url.endswith("a" * 40)
                return {"tree": [{"type": "blob", "path": "train.py"}], "truncated": True}
            if url.endswith("readme"):
                assert params["ref"] == "a" * 40
                return {
                    "encoding": "base64",
                    "content": base64.b64encode(
                        b"Official implementation https://paper.test/one"
                    ).decode(),
                }
            return {"default_branch": "main", "html_url": "https://github.com/owner/repo"}

    snapshot = github.GitHub(FakeHTTP()).inspect("owner/repo", paper_url="https://paper.test/one")
    assert snapshot["relationship"] == "author_claimed"
    assert snapshot["entrypoints"] == ["train.py"]
    assert snapshot["tree_truncated"] and len(snapshot["evidence"]) == 2


def test_github_reproducibility_assessment_and_paper_title_evidence():
    snapshot = {
        "files": [
            "train.py",
            "requirements.txt",
            "Dockerfile",
            "configs/base.yaml",
            "tests/test_model.py",
        ],
        "entrypoints": ["train.py"],
        "dependency_files": ["requirements.txt", "Dockerfile"],
        "readme": "Official implementation of Reliable Temporal Forecasting.\n```bash\npython train.py --config configs/base.yaml\n```\nDownload dataset first.",
        "license": "MIT",
        "relationship": "author_claimed",
    }
    assessment = github.reproducibility(snapshot)
    assert assessment["score"] >= 70
    assert assessment["ready_for_automatic_reproduction"]
    assert assessment["candidate_commands"] == ["python train.py --config configs/base.yaml"]


def test_github_paper_code_ranks_verified_candidate(monkeypatch):
    class FakeGitHub:
        def search(self, query, limit, page):
            assert "Reliable Method" in query
            return (
                [
                    {
                        "repository": "owner/code",
                        "url": "https://github.com/owner/code",
                        "description": "paper implementation",
                        "stars": 100,
                        "language": "Python",
                        "updated": "2026-01-01",
                        "license": "MIT",
                        "archived": False,
                    }
                ],
                {"total_count": 1},
            )

        def inspect(self, name, ref=None, paper_url="", paper_title=""):
            snapshot = {
                "id": "repo-paper",
                "repository": name,
                "url": "https://github.com/owner/code",
                "commit": "a" * 40,
                "license": "MIT",
                "readme": "Official implementation\n```bash\npython train.py\n```",
                "files": ["train.py", "requirements.txt"],
                "entrypoints": ["train.py"],
                "dependency_files": ["requirements.txt"],
                "relationship": "paper_title_matched",
                "evidence": [],
                "warnings": [],
            }
            snapshot["reproducibility"] = github.reproducibility(snapshot)
            return snapshot

    monkeypatch.setattr(github, "GitHub", FakeGitHub)
    response = github.execute({"mode": "paper_code", "paper_title": "Reliable Method"})
    item = response["data"]["items"][0]
    assert item["snapshot_id"] == "repo-paper"
    assert item["reproducibility_score"] > 0
    assert response["data"]["recommended"]["repository"] == "owner/code"
    assert response["data"]["reproduction_handoff"]["params"]["snapshot_id"] == "repo-paper"
    natural = github.execute({"mode": "find", "request": "查找论文《Reliable Method》的官方代码"})
    assert natural["data"]["interpreted_request"]["paper_title"] == "Reliable Method"
    assert natural["data"]["reproduction_handoff"]["run"]["mode"] == "reproduce"


def test_github_plain_discovery_request():
    paper = github.parse_discovery_request("帮我查找论文《Are Transformers Effective for Time Series Forecasting?》的官方代码")
    assert paper["kind"] == "paper"
    assert paper["paper_title"].startswith("Are Transformers")
    repo = github.parse_discovery_request("检查仓库 https://github.com/cure-lab/LTSF-Linear")
    assert repo == {
        "kind": "repository",
        "repository": "cure-lab/LTSF-Linear",
        "request": "检查仓库 https://github.com/cure-lab/LTSF-Linear",
    }


def test_code_search_requires_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    response = invoke("github", {"mode": "search_code", "query": "torch"})
    assert response["data"]["error_code"] == "AUTH_REQUIRED"


def test_catalog_coverage_and_current_ranks():
    data = catalog.validate(catalog.load())
    assert len(data["venues"]) == 111
    assert len(catalog.selected(scope="ccf_a")) == 95
    assert len({v["field"] for v in data["venues"]}) == 10
    assert catalog.selected(["ICLR"])[0]["rank"] == "A"
    assert catalog.selected(["IJCAI"])[0]["rank"] == "B"
    assert catalog.selected(["USENIX ATC"])[0]["id"] == "conf-usenix"
    with pytest.raises(ToolError):
        catalog.selected(["not a conference"])


@pytest.mark.parametrize(
    "track,expected",
    [
        ("unknown", None),
        ("short", None),
        ("Findings", None),
        ("Workshop", None),
        ("demo", None),
        ("regular", "A"),
    ],
)
def test_rank_requires_track_evidence(track, expected):
    venue = catalog.selected(["ACL"])[0]
    assert catalog.classify(venue, {"venue": "ACL", "track": track})["ccf_rank"] == expected


def sample(source="dblp", track="unknown", abstract=""):
    return papers.normalize_paper(
        {
            "title": "A Reliable Method",
            "year": 2025,
            "doi": "https://doi.org/10.1234/ABC",
            "source": source,
            "source_key": source + "/one",
            "authors": ["A Researcher"],
            "venue_id": "conf-acl",
            "track": track,
            "abstract": abstract,
            "ccf_rank": "A" if track == "regular" else None,
        }
    )


def test_dedup_preserves_sources_and_no_false_repeat_changes(store):
    a, b = sample(), sample("anthology", "regular", "Grounded abstract")
    rows, _, changes = papers.persist([a, b], [], store)
    assert len(rows) == len(changes) == 1
    assert rows[0]["doi"] == "10.1234/abc"
    rows, _, changes = papers.persist([a, b], [], store)
    assert changes == []
    assert rows[0]["track"] == "regular"
    assert rows[0]["abstract"] == "Grounded abstract"
    assert len(rows[0]["source_records"]) == 2


def test_topic_profile_scores_specific_papers():
    profile = papers.topic_profile("时间序列异常检测")
    relevant = papers.score_paper(
        sample(abstract="We study time series anomaly detection with temporal models"), profile
    )
    unrelated = papers.score_paper(sample(abstract="Compiler verification"), profile)
    assert profile["id"] == "time_series"
    assert relevant["relevance_score"] >= 4
    assert unrelated["relevance_score"] == 0
    assert papers.rank_papers([relevant, unrelated], profile, 2) == [relevant]


def test_plain_tracking_request_selects_direction_and_venues(store):
    parsed = papers.parse_tracking_request(
        "持续跟踪 ICML、NeurIPS 2025年以来的时间序列异常检测论文，每周更新", store
    )
    assert parsed["topic"] == "时间序列"
    assert parsed["venues"] == ["NeurIPS", "ICML"]
    assert parsed["since"] == "2025-01-01"
    assert parsed["interval_hours"] == 168


def test_track_mode_returns_concrete_papers_and_subscription(monkeypatch):
    paper = sample(abstract="time series forecasting")

    def fake_search(settings, store):
        scored = {**paper, "relevance_score": 6, "matched_terms": ["time series"]}
        stored, _, changes = papers.persist([scored], [], store)
        return stored, [{"venue": "conf-icml", "source": "openreview", "state": "success"}], changes

    monkeypatch.setattr(papers, "search", fake_search)
    response = papers.execute(
        {"mode": "track", "request": "持续跟踪 ICML 的时间序列论文，每天更新"}
    )
    assert response["data"]["items"][0]["title"] == "A Reliable Method"
    assert response["data"]["subscription"]["settings"]["topic"] == "时间序列"
    assert response["data"]["report"]["paper_ids"]
    assert response["data"]["handoffs"]["writing"]["params"]["report_ids"]
    assert response["data"]["handoffs"]["code_search"][0]["mode"] == "find"


@pytest.mark.parametrize("action", ["pause", "delete"])
def test_sync_does_not_resurrect_or_unpause(store, monkeypatch, action):
    subscription = store.put(
        "subscription", {"id": "sub", "enabled": True, "settings": {}, "interval_hours": 24}
    )

    def concurrent_search(*args):
        current = store.get("subscription", "sub")
        if action == "delete":
            store.delete("subscription", "sub")
        else:
            store.put("subscription", {**current, "enabled": False}, expected=current["revision"])
        return [], [], []

    monkeypatch.setattr(papers, "search", concurrent_search)
    papers.sync_subscription(store, subscription)
    if action == "delete":
        assert store.find("subscription") == []
    else:
        assert not store.get("subscription", "sub")["enabled"]


def test_subscription_tracks_seen_papers_independently(store, monkeypatch):
    tracked = store.put("paper", sample())
    subscription = papers.create_subscription(
        store,
        {
            "name": "time series",
            "venues": ["ICLR"],
            "topic": "time series",
            "interval_hours": 24,
        },
    )
    monkeypatch.setattr(
        papers,
        "search",
        lambda *args, **kwargs: (
            [tracked],
            [{"venue": "conf-iclr", "source": "openreview", "state": "success"}],
            [],
        ),
    )
    _, first = papers.sync_subscription(store, subscription)
    _, second = papers.sync_subscription(store, store.get("subscription", subscription["id"]))
    assert first["paper_ids"] == [tracked["id"]]
    assert second["paper_ids"] == []


def test_anthology_filters_short_and_preserves_markup():
    xml = '<collection><volume id="long"><meta><booktitle>ACL</booktitle></meta><paper id="1"><title>A <fixed-case>GPU</fixed-case> Method</title><author><first>A</first><last>Researcher</last></author><abstract>Evidence</abstract></paper></volume><volume id="short"><paper id="1"><title>Short Paper</title></paper></volume></collection>'
    source = papers.Sources(SimpleNamespace(get=lambda *a, **k: xml))
    rows, truncated = source.anthology(catalog.selected(["ACL"])[0], "", "2025-01-01", "2025-12-31")
    assert not truncated
    assert rows[0]["title"] == "A GPU Method" and rows[0]["ccf_rank"] == "A"
    assert rows[1]["track"] == "short" and rows[1]["ccf_rank"] is None


def test_partial_source_failure_is_visible(store):
    class Partial:
        def dblp(self, venue, *args):
            if venue["acronym"] == "ICLR":
                raise ToolError("Rate limited", "RATE_LIMITED", True)
            return [sample()], True

    _, health, _ = papers.search({"venues": ["ICLR", "ACL"], "source": "dblp"}, store, Partial())
    assert {h["state"] for h in health} == {"failed", "truncated"}


def test_crossref_exact_journal_and_cursor():
    calls = []
    item = {
        "title": ["A Result"],
        "DOI": "10.1000/example",
        "published": {"date-parts": [[2025, 1, 1]]},
    }

    def get(url, params=None):
        calls.append(params)
        if url.endswith("/journals"):
            return {"message": {"items": [{"title": "Test Journal", "ISSN": ["1234-5678"]}]}}
        if params["cursor"] == "*":
            return {"message": {"items": [item] * 100, "next-cursor": "next"}}
        return {"message": {"items": []}}

    source = papers.Sources(SimpleNamespace(get=get))
    rows, truncated = source.crossref(
        {"id": "j", "name": "Test Journal", "kind": "journal", "rank": "A"},
        "",
        "2025-01-01",
        "2025-12-31",
    )
    assert len(rows) == 100 and not truncated
    assert calls[-1]["cursor"] == "next"
    assert rows[0]["date_precision"] == "day" and rows[0]["source_issn"] == "1234-5678"


def test_openreview_only_accepted_venue():
    target = "ICLR.cc/2025/Conference"
    notes = [
        {
            "id": "accepted",
            "content": {"venueid": {"value": target}, "title": {"value": "Accepted paper"}},
        },
        {
            "id": "rejected",
            "content": {
                "venueid": {"value": target + "/Rejected_Submission"},
                "title": {"value": "Not accepted"},
            },
        },
    ]

    class Client:
        session = None

        def get_notes(self, **kwargs):
            assert kwargs["content"] == {"venueid": target}
            return notes

    configuration.save({"openreview": {"username": "reader@example.org", "password": "private"}})
    source = papers.Sources(openreview_client_class=lambda **kwargs: Client())
    rows, _ = source.openreview(catalog.selected(["ICLR"])[0], "", "2025-01-01", "2025-12-31")
    assert len(rows) == 1 and rows[0]["source_key"] == "accepted"
    assert rows[0]["ccf_rank"] == "A"


def test_dblp_filters_venue_and_date_without_claiming_regular_track():
    def get(url, params):
        assert "stream:conf/iclr:" in params["q"]
        items = [
            {"info": {"title": "Correct", "year": "2025", "key": "conf/iclr/one"}},
            {"info": {"title": "Different venue", "year": "2025", "key": "conf/acl/two"}},
            {"info": {"title": "Wrong year", "year": "2024", "key": "conf/iclr/three"}},
        ]
        return {"result": {"hits": {"@total": "3", "hit": items}}}

    rows, _ = papers.Sources(SimpleNamespace(get=get)).dblp(
        catalog.selected(["ICLR"])[0], "", "2025-01-01", "2025-12-31"
    )
    assert len(rows) == 1 and rows[0]["ccf_rank"] is None


def test_paper_identity_uses_index_not_full_scan(store, monkeypatch):
    monkeypatch.setattr(store, "find", lambda *a, **k: pytest.fail("Full corpus scan"))
    papers.persist([sample()], [], store)
    assert papers.persist([sample()], [], store)[2] == []


def test_unicode_titles_and_venue_names_do_not_collapse():
    assert catalog.normalize("研究一") != catalog.normalize("研究二")
    with pytest.raises(ToolError, match="Unknown venues"):
        catalog.selected(["不存在的中文会议"])


def test_failed_digest_never_claims_no_new_papers(store):
    report = store.put(
        "digest",
        {
            "id": "failed-report",
            "paper_ids": [],
            "paper_snapshots": [],
            "partial": True,
            "sources": [
                {"venue": "conf-acl", "source": "anthology", "state": "failed", "error": "HTTP 403"}
            ],
        },
    )
    response = papers.execute({"mode": "digest", "report_id": report["id"]})
    assert "PARTIAL" in response["data"]["text"]
    assert "No new papers" not in response["data"]["text"]


def test_artifact_registration_uses_existing_async_api(tmp_path, monkeypatch):
    from resources.tools._academic.common import register_artifact

    folder = tmp_path / "export"
    folder.mkdir()
    (folder / "one.txt").write_text("Evidence", encoding="utf-8")

    async def call(**kwargs):
        assert kwargs["id"].startswith("academic-")
        assert kwargs["file_count"] == 1 and kwargs["formats"] == ["txt"]
        return {"dataset_id": kwargs["id"]}

    monkeypatch.setattr("core.api.get_api", lambda ident: SimpleNamespace(call=call))
    assert register_artifact(folder, "Test")["dataset_id"].startswith("academic-")

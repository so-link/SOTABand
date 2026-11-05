"""Source adapters, paper identity and persistent subscriptions."""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin

from . import catalog
from .common import (
    HTTP,
    Store,
    ToolError,
    atomic_json,
    failure,
    integer,
    list_arg,
    now,
    required,
    result,
    table,
    uid,
)
from .configuration import token
from .openreview_source import call as openreview_call
from .openreview_source import note_json
from .openreview_source import session as openreview_session

TOPIC_PROFILES = {
    "time_series": {
        "names": ["时间序列", "时序分析", "time series", "temporal analysis"],
        "terms": [
            "time series",
            "temporal",
            "forecasting",
            "anomaly detection",
            "change point",
            "sequence modeling",
        ],
        "venues": ["NeurIPS", "ICML", "ICLR", "KDD", "AAAI", "IJCAI", "ICDM"],
    },
    "large_language_models": {
        "names": ["大语言模型", "大模型", "llm", "large language model"],
        "terms": [
            "large language model",
            "language model",
            "instruction tuning",
            "reasoning",
            "retrieval augmented generation",
            "alignment",
        ],
        "venues": ["ACL", "EMNLP", "NAACL", "NeurIPS", "ICML", "ICLR", "AAAI"],
    },
    "multimodal": {
        "names": ["多模态", "multimodal", "vision language", "视觉语言"],
        "terms": [
            "multimodal",
            "vision language",
            "image text",
            "cross modal",
            "visual language model",
        ],
        "venues": ["CVPR", "ICCV", "ECCV", "ACL", "NeurIPS", "ICML", "ICLR"],
    },
    "computer_vision": {
        "names": ["计算机视觉", "视觉", "computer vision"],
        "terms": [
            "computer vision",
            "image",
            "visual",
            "object detection",
            "segmentation",
            "recognition",
        ],
        "venues": ["CVPR", "ICCV", "ECCV", "NeurIPS", "ICML", "AAAI"],
    },
    "graph_learning": {
        "names": ["图学习", "图神经网络", "gnn", "graph neural network"],
        "terms": [
            "graph neural network",
            "graph learning",
            "graph representation",
            "message passing",
            "graph transformer",
        ],
        "venues": ["NeurIPS", "ICML", "ICLR", "KDD", "AAAI", "IJCAI"],
    },
    "federated_learning": {
        "names": ["联邦学习", "federated learning"],
        "terms": [
            "federated learning",
            "federated optimization",
            "privacy preserving learning",
            "client heterogeneity",
            "distributed learning",
        ],
        "venues": ["NeurIPS", "ICML", "ICLR", "KDD", "AAAI", "IJCAI"],
    },
    "reinforcement_learning": {
        "names": ["强化学习", "reinforcement learning", "rl"],
        "terms": [
            "reinforcement learning",
            "offline reinforcement learning",
            "policy optimization",
            "reward model",
            "multi agent",
        ],
        "venues": ["NeurIPS", "ICML", "ICLR", "AAAI", "IJCAI"],
    },
    "recommendation": {
        "names": ["推荐系统", "recommendation", "recommender system"],
        "terms": [
            "recommender system",
            "recommendation",
            "collaborative filtering",
            "sequential recommendation",
            "ranking",
        ],
        "venues": ["KDD", "WWW", "SIGIR", "CIKM", "AAAI", "IJCAI"],
    },
    "causal_learning": {
        "names": ["因果学习", "因果表示学习", "causal learning", "causal representation"],
        "terms": [
            "causal learning",
            "causal representation",
            "causal inference",
            "causal discovery",
            "counterfactual",
        ],
        "venues": ["NeurIPS", "ICML", "ICLR", "AAAI", "IJCAI", "UAI"],
    },
    "medical_imaging": {
        "names": ["医学影像", "医疗影像", "medical imaging", "medical image analysis"],
        "terms": [
            "medical imaging",
            "medical image",
            "radiology",
            "clinical imaging",
            "medical segmentation",
        ],
        "venues": ["CVPR", "ICCV", "ECCV", "AAAI", "IJCAI"],
    },
    "three_dimensional_vision": {
        "names": ["三维视觉", "3d视觉", "3d vision", "three dimensional vision"],
        "terms": [
            "3d vision",
            "point cloud",
            "neural rendering",
            "3d reconstruction",
            "scene representation",
        ],
        "venues": ["CVPR", "ICCV", "ECCV", "NeurIPS", "ICML"],
    },
    "anomaly_detection": {
        "names": ["异常检测", "anomaly detection", "outlier detection"],
        "terms": [
            "anomaly detection",
            "outlier detection",
            "novelty detection",
            "open set detection",
        ],
        "venues": ["KDD", "ICDM", "NeurIPS", "ICML", "ICLR", "AAAI", "IJCAI"],
    },
}


def _terms(value):
    values = list_arg(value)
    expanded = []
    for item in values:
        expanded.extend(re.split(r"[，；;|]", str(item)))
    return [re.sub(r"\s+", " ", item).strip() for item in expanded if item.strip()]


def topic_profile(topic="", keywords=None, exclude_keywords=None):
    topic = re.sub(r"\s+", " ", str(topic or "")).strip()
    normalized = catalog.normalize(topic)
    matched_id = None
    definition = {}
    for ident, candidate in TOPIC_PROFILES.items():
        aliases = [catalog.normalize(name) for name in candidate["names"]]
        if normalized and any(alias in normalized or normalized in alias for alias in aliases):
            matched_id, definition = ident, candidate
            break
    positive = [topic] if topic else []
    positive.extend(definition.get("terms", []))
    positive.extend(_terms(keywords))
    positive = list(dict.fromkeys(term.casefold() for term in positive if term))
    negative = list(dict.fromkeys(term.casefold() for term in _terms(exclude_keywords)))
    return {
        "id": matched_id or "custom",
        "topic": topic,
        "label": definition.get("names", [topic or "custom"])[0],
        "terms": positive,
        "exclude_terms": negative,
        "recommended_venues": definition.get("venues", []),
        "source_query": (definition.get("terms") or positive or [""])[0],
    }


def score_paper(paper, profile, authors=None):
    title = str(paper.get("title", "")).casefold()
    abstract = str(paper.get("abstract", "")).casefold()
    author_text = " ".join(paper.get("authors", [])).casefold()
    excluded = [term for term in profile["exclude_terms"] if term in title or term in abstract]
    if excluded:
        return None
    author_filters = [a.casefold() for a in _terms(authors)]
    if author_filters and not any(author in author_text for author in author_filters):
        return None
    matches, score = [], 0
    for term in profile["terms"]:
        title_hit, abstract_hit = term in title, term in abstract
        if title_hit or abstract_hit:
            matches.append(term)
            score += (4 if title_hit else 0) + (2 if abstract_hit else 0)
    if author_filters:
        score += 3
    if not profile["terms"]:
        score = 1
    enriched = dict(paper)
    enriched.update(
        {
            "topic_id": profile["id"],
            "topic": profile["topic"],
            "relevance_score": score,
            "matched_terms": matches,
            "relevance_reason": (
                "matched " + ", ".join(matches[:8]) if matches else "no topic term matched"
            ),
        }
    )
    return enriched


def rank_papers(rows, profile, minimum=1, authors=None):
    ranked = []
    for paper in rows:
        scored = score_paper(paper, profile, authors)
        if scored and scored["relevance_score"] >= minimum:
            ranked.append(scored)
    return sorted(
        ranked,
        key=lambda p: (p["relevance_score"], p.get("year", 0), p.get("title", "")),
        reverse=True,
    )


def parse_tracking_request(value, store=None):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        raise ToolError("请描述要跟踪的研究方向，例如：持续跟踪 ICML、NeurIPS 的时间序列论文")
    profile = topic_profile(text)
    venues = []
    for venue in catalog.selected(store=store):
        names = [venue.get("acronym", ""), venue["name"], *venue.get("aliases", [])]
        if any(
            name and re.search(rf"(?<![A-Za-z]){re.escape(name)}(?![A-Za-z])", text, re.IGNORECASE)
            for name in names
        ):
            venues.append(venue.get("acronym") or venue["id"])
    if profile["id"] == "custom":
        cleaned = text
        for venue in catalog.selected(store=store):
            for name in [venue.get("acronym", ""), venue["name"], *venue.get("aliases", [])]:
                if name:
                    cleaned = re.sub(re.escape(name), " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"20\d{2}\s*(?:年|年以来|起)?", " ", cleaned)
        cleaned = re.sub(
            r"持续|跟踪|关注|检索|查找|论文|文章|方向|相关|每天更新|每周更新|每日|每周|以来|的",
            " ",
            cleaned,
        )
        cleaned = re.sub(r"[、，,。；;：:\s]+", " ", cleaned).strip()
        profile = topic_profile(cleaned)
    if not venues:
        venues = profile["recommended_venues"]
    if not venues:
        raise ToolError("无法从描述中确定会议期刊；请写出会议简称，例如 ICML、ACL 或 CVPR")
    since, until = None, today().isoformat()
    years = re.search(r"(20\d{2})\s*(?:-|—|至|到)\s*(20\d{2})", text)
    after = re.search(r"(20\d{2})\s*年?(?:以来|起)", text)
    recent = re.search(r"近\s*(\d+)\s*(?:年|个月|月)", text)
    if years:
        since, until = years.group(1) + "-01-01", years.group(2) + "-12-31"
    elif after:
        since = after.group(1) + "-01-01"
    elif recent:
        amount = int(recent.group(1))
        days = amount * (365 if "年" in recent.group(0) else 30)
        since = (today() - timedelta(days=days)).isoformat()
    else:
        since = (today() - timedelta(days=365)).isoformat()
    interval = 168 if "每周" in text or "一周" in text else 24
    return {
        "name": profile["label"][:60] + "论文跟踪",
        "topic": profile["label"],
        "venues": venues,
        "since": since,
        "until": until,
        "interval_hours": interval,
        "lookback_days": max(30, (date.fromisoformat(until) - date.fromisoformat(since)).days),
        "scope": "all",
        "source": "auto",
        "max_pages": 3,
        "min_relevance": 2,
    }


def doi(value):
    return (
        re.sub(r"^https?://(?:dx\.)?doi.org/", "", value or "", flags=re.IGNORECASE).strip().lower()
    )


def normalize_paper(paper):
    paper = dict(paper)
    paper["doi"] = doi(paper.get("doi"))
    paper["title"] = re.sub(r"\s+", " ", paper.get("title", "")).strip()
    if not paper["title"]:
        raise ToolError("Source paper has no title", "SOURCE_FORMAT_ERROR")
    paper["year"] = int(paper.get("year") or 0)
    paper.setdefault("authors", [])
    paper.setdefault("track", "unknown")
    paper.setdefault("publication_status", "published")
    paper.setdefault("fetched_at", now())
    paper.setdefault("abstract", "")
    identity = (
        paper["doi"] or paper.get("source_key") or (paper["title"].casefold() + str(paper["year"]))
    )
    paper["id"] = "paper-" + hashlib.sha256(identity.encode()).hexdigest()[:24]
    return paper


def date_arg(value):
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise ToolError("Date must use YYYY-MM-DD") from None


def today():
    return datetime.now(UTC).date()


class Sources:
    def __init__(self, http=None, openreview_client_class=None):
        self.http = http or HTTP()
        self.openreview_client_class = openreview_client_class

    def dblp(self, venue, query, since, until, max_pages=5):
        rows = []
        total = 0
        prefix = (
            ("journals" if venue["kind"] == "journal" else "conf") + "/" + venue["dblp_key"] + "/"
        )
        for page in range(max_pages):
            years = " | ".join(f"year:{y}:" for y in range(int(since[:4]), int(until[:4]) + 1))
            search = " ".join(filter(None, [query, f"stream:{prefix.rstrip('/')}:", f"({years})"]))
            data = self.http.get(
                "https://dblp.org/search/publ/api",
                {"q": search, "format": "json", "h": 100, "f": page * 100},
            )
            hits = data.get("result", {}).get("hits", {})
            total = int(hits.get("@total", 0))
            items = hits.get("hit", [])
            if isinstance(items, dict):
                items = [items]
            for item in items:
                info = item.get("info", {})
                key = info.get("key", "")
                if not key.startswith(prefix):
                    continue
                year = int(info.get("year", 0))
                if not int(since[:4]) <= year <= int(until[:4]):
                    continue
                authors = info.get("authors", {}).get("author", [])
                if not isinstance(authors, list):
                    authors = [authors]
                paper = normalize_paper(
                    {
                        "title": info.get("title", ""),
                        "year": year,
                        "authors": [
                            a.get("text", "") if isinstance(a, dict) else a for a in authors
                        ],
                        "doi": info.get("doi", ""),
                        "url": info.get("ee") or info.get("url"),
                        "venue": info.get("venue", venue["name"]),
                        "source": "dblp",
                        "source_key": key,
                        "track": "journal" if venue["kind"] == "journal" else "unknown",
                        "date_precision": "year",
                    }
                )
                rows.append(catalog.classify(venue, paper))
            if (page + 1) * 100 >= total:
                break
        return rows, total > max_pages * 100

    def crossref(self, venue, query, since, until, max_pages=5):
        issn = venue.get("issn")
        if not issn:
            name = re.split(r"[（(]原", venue["name"])[0].strip()
            matches = self.http.get(
                "https://api.crossref.org/journals", {"query": name, "rows": 10}
            )["message"].get("items", [])
            exact = [
                j
                for j in matches
                if catalog.normalize(j.get("title", "")) == catalog.normalize(name)
                and j.get("ISSN")
            ]
            if len(exact) != 1:
                raise ToolError(
                    "Crossref journal identity ambiguous; configure its ISSN",
                    "SOURCE_NOT_CONFIGURED",
                )
            issn = exact[0]["ISSN"][0]
        rows, cursor = [], "*"
        complete = False
        for _ in range(max_pages):
            data = self.http.get(
                f"https://api.crossref.org/journals/{issn}/works",
                {
                    "query": query,
                    "filter": f"from-pub-date:{since},until-pub-date:{until}",
                    "rows": 100,
                    "cursor": cursor,
                },
            )["message"]
            items = data.get("items", [])
            for item in items:
                parts = (item.get("published") or item.get("issued") or {}).get(
                    "date-parts", [[0]]
                )[0]
                paper = normalize_paper(
                    {
                        "title": (item.get("title") or [""])[0],
                        "year": parts[0],
                        "authors": [
                            " ".join(filter(None, [a.get("given"), a.get("family")]))
                            for a in item.get("author", [])
                        ],
                        "doi": item.get("DOI", ""),
                        "source_key": item.get("DOI"),
                        "url": item.get("URL"),
                        "venue": venue["name"],
                        "source": "crossref",
                        "track": "journal",
                        "abstract": re.sub(r"<[^>]+>", "", item.get("abstract", "")),
                        "date_precision": "day"
                        if len(parts) > 2
                        else "month"
                        if len(parts) > 1
                        else "year",
                        "published_date_parts": parts,
                        "source_issn": issn,
                    }
                )
                rows.append(catalog.classify(venue, paper))
            next_cursor = data.get("next-cursor")
            if len(items) < 100 or not next_cursor or next_cursor == cursor:
                complete = True
                break
            cursor = next_cursor
        return rows, not complete

    def openreview(self, venue, query, since, until, max_pages=5):
        venue_id = venue.get("openreview_id")
        if not venue_id:
            raise ToolError("Set openreview_id in the venue catalog", "SOURCE_NOT_CONFIGURED")
        rows = []
        truncated = False
        with openreview_session(self.openreview_client_class) as client:
            for year in range(int(since[:4]), int(until[:4]) + 1):
                target = venue_id.replace("{year}", str(year))
                for page in range(max_pages):
                    notes = openreview_call(
                        client,
                        "get_notes",
                        client.get_notes,
                        content={"venueid": target},
                        limit=100,
                        offset=page * 100,
                    )
                    for raw_note in notes:
                        note = note_json(raw_note)
                        content = {
                            k: v.get("value") if isinstance(v, dict) else v
                            for k, v in note.get("content", {}).items()
                        }
                        if content.get("venueid") != target:
                            continue
                        title = content.get("title", "")
                        if (
                            query
                            and query.casefold()
                            not in (title + " " + content.get("abstract", "")).casefold()
                        ):
                            continue
                        paper = normalize_paper(
                            {
                                "title": title,
                                "year": year,
                                "authors": content.get("authors", []),
                                "url": "https://openreview.net/forum?id=" + note["id"],
                                "source_key": note["id"],
                                "source": "openreview",
                                "venue": content.get("venue", venue["name"]),
                                "publication_status": "accepted",
                                "abstract": content.get("abstract", ""),
                                "date_precision": "year",
                                "track": "regular"
                                if venue.get("regular_track_verified")
                                else "unknown",
                            }
                        )
                        rows.append(catalog.classify(venue, paper))
                    if len(notes) < 100:
                        break
                else:
                    truncated = True
        return rows, truncated

    def cvf(self, venue, query, since, until, max_pages=5):
        from bs4 import BeautifulSoup

        rows = []
        for year in range(int(since[:4]), int(until[:4]) + 1):
            if venue["acronym"] == "ICCV" and year % 2 == 0:
                continue
            url = f"https://openaccess.thecvf.com/{venue['acronym']}{year}?day=all"
            html = self.http.get(url, raw=True)
            soup = BeautifulSoup(html, "html.parser")
            links = soup.select("dt.ptitle a")
            if not links:
                raise ToolError(
                    "CVF list not available for this year or page format changed",
                    "SOURCE_FORMAT_ERROR",
                )
            for link in links:
                title = link.get_text(" ", strip=True)
                if query and not all(w in title.casefold() for w in query.casefold().split()):
                    continue
                detail = urljoin(url, link.get("href", ""))
                paper = normalize_paper(
                    {
                        "title": title,
                        "year": year,
                        "url": detail,
                        "venue": venue["acronym"],
                        "source": "cvf",
                        "source_key": detail,
                        "track": "regular",
                        "date_precision": "year",
                    }
                )
                rows.append(catalog.classify(venue, paper))
        return rows, False

    def anthology(self, venue, query, since, until, max_pages=5):
        import xml.etree.ElementTree as ET

        key = venue.get("anthology_key")
        if not key:
            raise ToolError("Venue has no ACL Anthology mapping", "SOURCE_NOT_CONFIGURED")
        rows = []
        for year in range(int(since[:4]), int(until[:4]) + 1):
            collection = f"{year}.{key}"
            url = f"https://api.github.com/repos/acl-org/acl-anthology/contents/data/xml/{collection}.xml"
            try:
                root = ET.fromstring(
                    self.http.get(
                        url,
                        params={"ref": "master"},
                        headers={
                            "Accept": "application/vnd.github.raw+json",
                            **(
                                {"Authorization": "Bearer " + token("github")}
                                if token("github")
                                else {}
                            ),
                        },
                        raw=True,
                    )
                )
            except ET.ParseError as exc:
                raise ToolError("Invalid ACL Anthology XML", "SOURCE_FORMAT_ERROR") from exc
            for volume in root.findall("volume"):
                track = (
                    "journal"
                    if venue["kind"] == "journal"
                    else "regular"
                    if volume.get("id") == "long"
                    else "unknown"
                )
                name = "".join(volume.findtext("meta/booktitle", default=venue["name"]))
                for item in volume.findall("paper"):
                    if item.get("id") == "0":
                        continue
                    title = (
                        "".join(item.find("title").itertext())
                        if item.find("title") is not None
                        else ""
                    )
                    if query and not all(w in title.casefold() for w in query.casefold().split()):
                        continue
                    ident = f"{collection}-{volume.get('id')}.{item.get('id')}"
                    authors = [
                        " ".join(filter(None, [a.findtext("first"), a.findtext("last")]))
                        for a in item.findall("author")
                    ]
                    abstract = item.find("abstract")
                    paper = normalize_paper(
                        {
                            "title": title,
                            "authors": authors,
                            "year": year,
                            "source": "anthology",
                            "source_key": ident,
                            "url": f"https://aclanthology.org/{ident}/",
                            "doi": item.findtext("doi", ""),
                            "abstract": "".join(abstract.itertext())
                            if abstract is not None
                            else "",
                            "venue": name,
                            "track": "short" if volume.get("id") == "short" else track,
                            "date_precision": "year",
                        }
                    )
                    rows.append(catalog.classify(venue, paper))
        return rows, False

    def jmlr(self, venue, query, since, until, max_pages=5):
        from bs4 import BeautifulSoup

        if venue["id"] != "journals-jmlr":
            raise ToolError("JMLR source only supports JMLR", "SOURCE_NOT_CONFIGURED")
        rows = []
        for year in range(int(since[:4]), int(until[:4]) + 1):
            url = f"https://www.jmlr.org/papers/v{year - 1999}/"
            try:
                html = self.http.get(url, raw=True)
            except ToolError:
                url = f"https://jmlr.org/papers/v{year - 1999}/"
                html = self.http.get(url, raw=True)
            soup = BeautifulSoup(html, "html.parser")
            entries = soup.select("dl")
            if not entries:
                raise ToolError("JMLR volume unavailable", "SOURCE_FORMAT_ERROR")
            for entry in entries:
                title, link = entry.find("dt"), entry.find("a", string="abs")
                if title is None or link is None:
                    continue
                title = title.get_text(" ", strip=True)
                if query and not all(w in title.casefold() for w in query.casefold().split()):
                    continue
                authors = entry.select_one("dd i")
                detail = urljoin(url, link["href"])
                paper = normalize_paper(
                    {
                        "title": title,
                        "year": year,
                        "url": detail,
                        "source_key": detail,
                        "source": "jmlr",
                        "venue": "JMLR",
                        "track": "journal",
                        "date_precision": "year",
                        "authors": authors.get_text(" ", strip=True).split(", ") if authors else [],
                    }
                )
                rows.append(catalog.classify(venue, paper))
        return rows, False


def search(params, store=None, sources=None, cancel=None):
    store = store or Store()
    sources = sources or Sources()
    venues = catalog.selected(list_arg(params.get("venues")), params.get("scope", "all"), store)
    if not venues:
        raise ToolError("No venues match the requested catalog scope")
    since = date_arg(params.get("since") or (today() - timedelta(days=30)).isoformat())
    until = date_arg(params.get("until") or today().isoformat())
    if since > until:
        raise ToolError("since must not be after until")
    max_pages = integer(params.get("max_pages", 3), "max_pages", 1, 100)
    requested_query = str(params.get("query", "")).strip()
    profile = topic_profile(
        params.get("topic") or requested_query,
        params.get("keywords"),
        params.get("exclude_keywords"),
    )
    query = requested_query or profile["source_query"]
    minimum = integer(params.get("min_relevance", 1), "min_relevance", 0, 100)
    authors = params.get("authors")
    rows, health = [], []
    for venue in venues:
        if cancel and cancel():
            raise ToolError("Synchronization stopped by scheduler request", "CANCELLED")
        source = params.get("source", "auto")
        automatic = source == "auto"
        if automatic:
            source = (
                "jmlr"
                if venue["id"] == "journals-jmlr"
                else "anthology"
                if venue.get("anthology_key")
                else "cvf"
                if venue.get("acronym") in ("CVPR", "ICCV")
                else "crossref"
                if venue["kind"] == "journal"
                else "openreview"
                if venue.get("openreview_id")
                else "dblp"
            )
        method = getattr(sources, source, None)
        if (
            source not in ("dblp", "crossref", "openreview", "cvf", "anthology", "jmlr")
            or not method
        ):
            raise ToolError(
                "source must be auto, dblp, crossref, openreview, cvf, anthology or jmlr"
            )
        try:
            if source == "dblp" and not venue.get("dblp_key"):
                raise ToolError("Venue has no DBLP mapping", "SOURCE_NOT_CONFIGURED")
            items, truncated = method(venue, query, since, until, max_pages)
            ranked = rank_papers(items, profile, minimum, authors)
            rows.extend(ranked)
            health.append(
                {
                    "venue": venue["id"],
                    "source": source,
                    "state": "truncated" if truncated else "success",
                    "count": len(ranked),
                    "fetched_count": len(items),
                    "checked_at": now(),
                }
            )
        except ToolError as exc:
            health.append(
                {
                    "venue": venue["id"],
                    "source": source,
                    "state": "failed",
                    "error": str(exc),
                    "checked_at": now(),
                    **failure(exc)["data"],
                }
            )
            if automatic and source != "dblp" and venue.get("dblp_key"):
                try:
                    items, truncated = sources.dblp(venue, query, since, until, max_pages)
                    ranked = rank_papers(items, profile, minimum, authors)
                    rows.extend(ranked)
                    health.append(
                        {
                            "venue": venue["id"],
                            "source": "dblp",
                            "fallback": True,
                            "state": "truncated" if truncated else "success",
                            "count": len(ranked),
                            "fetched_count": len(items),
                        }
                    )
                except ToolError as fallback:
                    health.append(
                        {
                            "venue": venue["id"],
                            "source": "dblp",
                            "state": "failed",
                            "error": str(fallback),
                            "checked_at": now(),
                            **failure(fallback)["data"],
                        }
                    )
    with store.lock("paper-index"):
        saved, health, changes = persist(rows, health, store)
    return saved, health, changes


def create_subscription(store, params):
    names = list_arg(params.get("venues"))
    catalog.selected(names, params.get("scope", "all"), store)
    settings = {
        k: params[k]
        for k in (
            "query",
            "topic",
            "keywords",
            "exclude_keywords",
            "authors",
            "min_relevance",
            "venues",
            "scope",
            "source",
            "max_pages",
        )
        if k in params
    }
    return store.put(
        "subscription",
        {
            "id": uid("subscription"),
            "name": str(required(params, "name")),
            "enabled": True,
            "interval_hours": integer(params.get("interval_hours", 24), "interval_hours", 1, 8760),
            "lookback_days": integer(params.get("lookback_days", 365), "lookback_days", 1, 36500),
            "next_run": time.time(),
            "seen_paper_ids": [],
            "settings": settings,
            "topic_profile": topic_profile(
                params.get("topic") or params.get("query", ""),
                params.get("keywords"),
                params.get("exclude_keywords"),
            ),
        },
    )


def digest_record(store, subscription, rows, health, changes):
    changed_ids = set(changes)
    return store.put(
        "digest",
        {
            "id": uid("digest"),
            "subscription_id": subscription["id"],
            "topic_profile": subscription.get("topic_profile"),
            "paper_ids": changes,
            "sources": health,
            "created_at": now(),
            "paper_snapshots": [p for p in rows if p["id"] in changed_ids],
            "partial": any(h["state"] != "success" for h in health),
        },
    )


def related_papers(target, candidates, limit=10):
    words = set(
        re.findall(
            r"[A-Za-z][A-Za-z0-9-]{2,}",
            (target.get("title", "") + " " + target.get("abstract", "")).casefold(),
        )
    )
    ignored = {"the", "and", "for", "with", "from", "using", "via", "based", "towards"}
    words -= ignored
    ranked = []
    for paper in candidates:
        if paper["id"] == target["id"]:
            continue
        other = (
            set(
                re.findall(
                    r"[A-Za-z][A-Za-z0-9-]{2,}",
                    (paper.get("title", "") + " " + paper.get("abstract", "")).casefold(),
                )
            )
            - ignored
        )
        overlap = words & other
        union = words | other
        score = round(len(overlap) / len(union), 4) if union else 0
        if score:
            ranked.append({**paper, "similarity": score, "shared_terms": sorted(overlap)[:20]})
    return sorted(ranked, key=lambda item: item["similarity"], reverse=True)[:limit]


def persist(rows, health, store):
    saved, changes = {}, []
    for paper in rows:
        paper = dict(paper)
        paper["title_key"] = catalog.normalize(paper["title"])
        previous = None
        with store.connect() as db:
            if paper.get("doi"):
                found = db.execute(
                    "SELECT body FROM records WHERE kind='paper' AND owner=? "
                    "AND json_extract(body,'$.doi')=? LIMIT 1",
                    (store.owner, paper["doi"]),
                ).fetchone()
                previous = json.loads(found[0]) if found else None
            if previous is None:
                found = db.execute(
                    "SELECT body FROM records WHERE kind='paper' AND owner=? AND id=?",
                    (store.owner, paper["id"]),
                ).fetchone()
                previous = json.loads(found[0]) if found else None
            if previous is None:
                found = db.execute(
                    "SELECT body FROM records WHERE kind='paper' AND owner=? "
                    "AND json_extract(body,'$.title_key')=? AND json_extract(body,'$.year')=?",
                    (store.owner, paper["title_key"], paper["year"]),
                ).fetchall()
                for (body,) in found:
                    candidate = json.loads(body)
                    a, b = set(candidate.get("authors", [])), set(paper.get("authors", []))
                    if a & b or (
                        candidate.get("venue_id") == paper.get("venue_id") and (not a or not b)
                    ):
                        previous = candidate
                        break
        query_fields = {
            "topic_id",
            "topic",
            "relevance_score",
            "matched_terms",
            "relevance_reason",
        }
        comparable = {
            k: v for k, v in paper.items() if k not in {"id", "fetched_at", *query_fields}
        }
        source_id = paper["source"] + ":" + str(paper.get("source_key", paper["id"]))
        sources_seen = dict((previous or {}).get("source_records", {}))
        changed = previous is None or sources_seen.get(source_id) != comparable
        sources_seen[source_id] = comparable
        if previous:
            paper = {
                **previous,
                **{k: v for k, v in paper.items() if v not in (None, "", [])},
                "id": previous["id"],
            }
            if (
                previous.get("track") in ("regular", "full", "journal")
                and comparable.get("track") == "unknown"
            ):
                paper["track"], paper["ccf_rank"] = previous["track"], previous["ccf_rank"]
        paper["source_records"] = sources_seen
        paper.setdefault("first_seen", now())
        paper["last_seen"] = now()
        if changed:
            changes.append(paper["id"])
        paper = store.put("paper", paper)
        saved[paper["id"]] = paper
    return list(saved.values()), health, list(dict.fromkeys(changes))


def sync_subscription(store, subscription, sources=None, cancel=None):
    with store.lock("sync:" + subscription["id"]):
        subscription = store.get("subscription", subscription["id"])
        settings = dict(subscription["settings"])
        # A rolling overlap discovers late indexing; explicit backfill can widen it.
        settings["since"] = (
            today() - timedelta(days=subscription.get("lookback_days", 365))
        ).isoformat()
        rows, health, changes = search(settings, store, sources, cancel)
        seen = set(subscription.get("seen_paper_ids", []))
        report_ids = [paper["id"] for paper in rows if paper["id"] not in seen]
        report_ids.extend(ident for ident in changes if ident not in report_ids)
        report = digest_record(store, subscription, rows, health, report_ids)
        with store.lock("subscription:" + subscription["id"]):
            try:
                subscription = store.get("subscription", subscription["id"])
            except ToolError as exc:
                if exc.code == "NOT_FOUND":
                    return rows, report
                raise
            subscription["last_sync"] = now()
            subscription["next_run"] = time.time() + subscription["interval_hours"] * 3600
            subscription["last_report_id"] = report["id"]
            subscription["seen_paper_ids"] = list(
                dict.fromkeys([*subscription.get("seen_paper_ids", []), *(p["id"] for p in rows)])
            )
            store.put("subscription", subscription, expected=subscription["revision"])
        return rows, report


def execute(params):
    mode = params.get("mode", "search")
    store = Store()
    if mode == "diagnose_sources":
        from .network import diagnose

        return diagnose(params)
    if mode == "catalog":
        data = catalog.load(store)
        venues = catalog.selected(list_arg(params.get("venues")), params.get("scope", "all"), store)
        return table(
            venues,
            ["id", "acronym", "name", "rank", "kind", "field"],
            catalog_version=data["version"],
            source_url=data["source_url"],
        )
    if mode == "topics":
        profiles = [
            {
                "id": ident,
                "name": profile["names"][0],
                "aliases": profile["names"],
                "terms": profile["terms"],
                "recommended_venues": profile["venues"],
            }
            for ident, profile in TOPIC_PROFILES.items()
        ]
        return table(
            profiles,
            ["id", "name", "terms", "recommended_venues"],
            message="Built-in topic profiles; custom topics are also supported",
        )
    if mode == "import_catalog":
        data = catalog.validate(
            json.loads(Path(required(params, "file_path")).read_text(encoding="utf-8"))
        )
        store.put("catalog", {**data, "id": "active"})
        return result({"count": len(data["venues"]), "version": data["version"]})
    if mode == "search":
        rows, health, changes = search(params, store)
        if health and all(h["state"] == "failed" for h in health):
            error_codes = {h.get("error_code", "SOURCE_UNAVAILABLE") for h in health}
            error_code = (
                next(iter(error_codes))
                if len(error_codes) == 1
                and next(iter(error_codes))
                in {
                    "CONFIG_REQUIRED",
                    "AUTH_REQUIRED",
                    "AUTH_FAILED",
                    "MFA_REQUIRED",
                    "CHALLENGE_REQUIRED",
                }
                else "SOURCE_UNAVAILABLE"
            )
            raise ToolError(
                "All sources failed: " + "; ".join(h["error"] for h in health),
                error_code,
                any(h.get("retryable", False) for h in health),
                {"sources": health},
            )
        paper_ids = [paper["id"] for paper in rows]
        return table(
            rows,
            [
                "id",
                "title",
                "year",
                "venue",
                "ccf_rank",
                "relevance_score",
                "matched_terms",
                "url",
            ],
            message=f"{len(rows)} papers; "
            + (
                "PARTIAL source coverage, inspect sources"
                if any(h["state"] != "success" for h in health)
                else "source queries completed"
            ),
            sources=health,
            changed_ids=changes,
            partial=any(h["state"] != "success" for h in health),
            topic_profile=topic_profile(
                params.get("topic") or params.get("query", ""),
                params.get("keywords"),
                params.get("exclude_keywords"),
            ),
            handoffs={
                "writing": {
                    "tool_id": "paper-writing",
                    "mode": "create",
                    "params": {"paper_ids": paper_ids},
                },
                "code_search": [
                    {
                        "tool_id": "github-code-search",
                        "mode": "find",
                        "params": {"request": f"查找论文《{paper['title']}》的官方代码"},
                    }
                    for paper in rows[:10]
                ],
            },
        )
    if mode == "track":
        settings = parse_tracking_request(required(params, "request"), store)
        rows, health, _changes = search(settings, store)
        subscription = create_subscription(store, settings)
        report = digest_record(store, subscription, rows, health, [paper["id"] for paper in rows])
        subscription["last_sync"] = now()
        subscription["last_report_id"] = report["id"]
        subscription["seen_paper_ids"] = [paper["id"] for paper in rows]
        subscription["next_run"] = time.time() + subscription["interval_hours"] * 3600
        subscription = store.put("subscription", subscription, expected=subscription["revision"])
        return table(
            rows,
            [
                "id",
                "title",
                "year",
                "venue",
                "relevance_score",
                "matched_terms",
                "url",
            ],
            message=(
                f"Tracked {settings['topic']} in {', '.join(settings['venues'])}; "
                f"{len(rows)} matching papers and subscription {subscription['id']}"
            ),
            subscription=subscription,
            report=report,
            sources=health,
            partial=report["partial"],
            handoffs={
                "writing": {
                    "tool_id": "paper-writing",
                    "mode": "create",
                    "params": {"report_ids": [report["id"]]},
                },
                "code_search": [
                    {
                        "tool_id": "github-code-search",
                        "mode": "find",
                        "params": {"request": f"查找论文《{paper['title']}》的官方代码"},
                    }
                    for paper in rows[:10]
                ],
            },
        )
    if mode == "subscribe":
        subscription = create_subscription(store, params)
        return result(
            subscription, "Subscription saved; start the worker for scheduled synchronization"
        )
    if mode == "subscriptions":
        return table(
            store.find("subscription"),
            ["id", "name", "enabled", "interval_hours", "last_sync", "last_report_id"],
        )
    if mode == "paper_detail":
        return result(store.get("paper", required(params, "paper_id")))
    if mode == "related":
        target = store.get("paper", required(params, "paper_id"))
        rows = related_papers(
            target,
            store.find("paper", 100000),
            integer(params.get("limit", 10), "limit", 1, 100),
        )
        return table(
            rows,
            ["id", "title", "year", "venue", "similarity", "shared_terms", "url"],
            target={"id": target["id"], "title": target["title"]},
        )
    if mode in ("update_subscription", "unsubscribe", "sync"):
        subscription = store.get("subscription", required(params, "subscription_id"))
        if mode == "unsubscribe":
            with store.lock("subscription:" + subscription["id"]):
                store.delete("subscription", subscription["id"])
            return result(message="Subscription removed")
        if mode == "update_subscription":
            action = params.get("action", "pause")
            if action not in ("pause", "resume"):
                raise ToolError("action must be pause or resume")
            subscription["enabled"] = action == "resume"
            return result(
                store.put("subscription", subscription, expected=subscription["revision"])
            )
        rows, report = sync_subscription(store, subscription)
        return result(
            report,
            f"{'PARTIAL synchronization' if report['partial'] else 'Synchronization finished'}: {len(rows)} records, {len(report['paper_ids'])} changes",
        )
    if mode in ("digest", "export"):
        report_id = params.get("report_id")
        report = store.get("digest", report_id) if report_id else None
        papers = (
            (
                report["paper_snapshots"]
                if "paper_snapshots" in report
                else [store.get("paper", i) for i in report["paper_ids"]]
            )
            if report
            else store.find("paper", 100000)
        )
        if mode == "export":
            path = store.folder("exports", uid("papers")) / "papers.json"
            atomic_json(path, {"papers": papers, "report": report})
            return result({"file_path": str(path)}, "Papers exported", "file")
        text = "\n\n".join(
            f"### {p['title']}\n{p.get('venue', '')} ({p['year']})\n{p.get('url', '')}\n{p.get('abstract', '')}"
            for p in papers
        )
        if report and report.get("partial"):
            issues = "\n".join(
                f"- {s['venue']} / {s['source']}: {s['state']} {s.get('error', '')}"
                for s in report["sources"]
                if s["state"] != "success"
            )
            text = (
                "# PARTIAL synchronization\nSource failures or pagination limits prevent a complete update.\n"
                + issues
                + "\n\n"
                + text
            )
        return result(
            {
                "text": text or "No new papers in the indexed window",
                "report": report,
                "papers": papers,
            }
        )
    if mode in ("worker_start", "worker_stop", "worker_status"):
        from .worker import control

        return result(control(mode, store))
    raise ToolError(f"Unknown mode: {mode}")

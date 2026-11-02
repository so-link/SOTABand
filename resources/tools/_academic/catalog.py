"""Versioned venue catalog; classifications are data, never LLM guesses."""

import argparse
import hashlib
import json
import re
from pathlib import Path

from .common import ToolError, atomic_json, now

CATALOG = Path(__file__).with_name("venues.json")


def enrich(venues):
    aliases = {
        "conf-usenix": ["USENIX ATC", "ATC", "ACM SIGOPS ATC"],
        "conf-kdd": ["KDD"],
        "conf-nips": ["NIPS"],
        "conf-mm": ["ACM MM"],
        "conf-uss": ["USENIX Security"],
        "conf-visualization": ["VIS"],
        "journals-vldb": ["VLDB Journal"],
        "journals-joc": ["Journal of Cryptology"],
        "journals-ml": ["Machine Learning"],
    }
    for venue in venues.values():
        venue["aliases"].extend(aliases.get(venue["id"], []))
        if venue["id"] in ("conf-iclr", "conf-icml", "conf-nips"):
            venue["openreview_id"] = {
                "conf-iclr": "ICLR",
                "conf-icml": "ICML",
                "conf-nips": "NeurIPS",
            }[venue["id"]] + ".cc/{year}/Conference"
            venue["regular_track_verified"] = True
        if venue["id"] == "journals-jmlr":
            venue["issn"] = "1533-7928"
        if venue["id"] in ("conf-acl", "conf-naacl", "conf-emnlp", "journals-tacl"):
            venue["anthology_key"] = venue["id"].split("-")[1]
    # The source PDF links Pattern Recognition to conf/par; correct only its DBLP mapping.
    if "conf-par" in venues:
        venue = venues.pop("conf-par")
        venue.update(
            {
                "id": "journals-pr",
                "dblp_key": "pr",
                "mapping_note": "Corrected publisher catalog DBLP link",
            }
        )
        venues[venue["id"]] = venue
    return venues


def normalize(value):
    return "".join(character for character in str(value).casefold() if character.isalnum())


def load(store=None):
    if store:
        try:
            return store.get("catalog", "active")
        except ToolError as exc:
            if exc.code != "NOT_FOUND":
                raise
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def validate(data):
    if not isinstance(data, dict) or not isinstance(data.get("venues"), list) or not data["venues"]:
        raise ToolError("Catalog requires version, source_url and venues")
    if not data.get("version") or not data.get("source_url"):
        raise ToolError("Catalog version and source_url are required")
    ids = set()
    for venue in data["venues"]:
        if (
            not isinstance(venue, dict)
            or not venue.get("id")
            or venue["id"] in ids
            or not venue.get("name")
        ):
            raise ToolError("Venue IDs must be present and unique")
        ids.add(venue["id"])
        if venue.get("rank") not in ("A", "B", "C", None):
            raise ToolError("Invalid CCF rank")
        if venue.get("kind") not in ("conference", "journal"):
            raise ToolError("Venue kind must be conference or journal")
        if venue.get("dblp_key") and not re.fullmatch(r"[A-Za-z0-9_-]+", venue["dblp_key"]):
            raise ToolError("Invalid DBLP mapping")
        if venue.get("issn") and not re.fullmatch(r"\d{4}-\d{3}[\dX]", venue["issn"]):
            raise ToolError("Invalid ISSN")
    return data


def selected(names=None, scope="all", store=None):
    if scope not in ("all", "ccf_a"):
        raise ToolError("scope must be all or ccf_a")
    venues = load(store)["venues"]
    if scope == "ccf_a":
        venues = [v for v in venues if v.get("rank") == "A"]
    if names:
        wanted = {normalize(n) for n in names}
        if "" in wanted:
            raise ToolError("Venue names must not be empty")
        matches = []
        found = set()
        for venue in venues:
            aliases = {
                normalize(v)
                for v in [
                    venue["id"],
                    venue["name"],
                    venue.get("acronym", ""),
                    *venue.get("aliases", []),
                ]
            }
            if wanted & aliases:
                matches.append(venue)
                found.update(wanted & aliases)
        if wanted - found:
            raise ToolError("Unknown venues: " + ", ".join(sorted(wanted - found)))
        venues = matches
    return venues


def classify(venue, paper):
    text = (paper.get("venue", "") + " " + paper.get("track", "")).casefold()
    excluded = next(
        (k for k in ("workshop", "findings", "demo", "short", "companion") if k in text), None
    )
    paper["venue_id"] = venue["id"]
    paper["catalog_rank"] = venue.get("rank")
    paper["rank_basis"] = "Current bundled/imported catalog, not historical-year certification"
    paper["track"] = excluded or paper.get("track", "unknown")
    # A venue rank does not establish the paper's publication track.
    paper["ccf_rank"] = (
        venue.get("rank") if paper["track"] in ("full", "regular", "journal") else None
    )
    return paper


def import_pdf(path, source_url, version, output):
    """Extract the CCF's ruled tables, preserving source page numbers."""
    import pdfplumber

    venues = {}
    category, kind, grade = "", "", None
    with pdfplumber.open(path) as pdf:
        for page_number, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            match = re.search(r"[（(]([^）)]+)[）)]", text)
            if (
                match
                and not match[1].startswith("原")
                and (
                    "计算" in match[1]
                    or "/" in match[1]
                    or "人工智能" in match[1]
                    or "安全" in match[1]
                )
            ):
                category = match[1].replace("\n", "")
            if "推荐国际学术期刊" in text.replace(" ", ""):
                kind = "journal"
            elif "推荐国际学术会议" in text.replace(" ", ""):
                kind = "conference"
            for table in page.find_tables():
                above = page.crop((0, 0, page.width, table.bbox[1])).extract_text() or ""
                levels = re.findall(r"\b([ABC])\s*类", above)
                if levels:
                    grade = levels[-1]
                for row in table.extract(x_tolerance=1):
                    if len(row) < 5 or not re.fullmatch(r"\d+", (row[0] or "").strip()):
                        continue
                    acronym, name = (
                        (row[1] or "").replace("\n", " "),
                        (row[2] or "").replace("\n", " "),
                    )
                    url = re.sub(r"\s", "", row[-1] or "")
                    key = re.search(r"/db/(conf|journals)/([^/]+)", url)
                    ident = (key[1] + "-" + key[2]) if key else normalize(acronym or name)
                    important = {
                        "conf-colt",
                        "conf-emnlp",
                        "conf-eccv",
                        "conf-icra",
                        "conf-naacl",
                        "conf-coling",
                        "conf-ijcai",
                        "conf-uai",
                        "conf-aistats",
                        "conf-iros",
                        "journals-tacl",
                        "journals-jair",
                        "journals-ml",
                        "journals-neco",
                        "journals-nn",
                        "conf-par",
                    }
                    if grade != "A" and ident not in important:
                        continue
                    venues[ident] = {
                        "id": ident,
                        "acronym": acronym.strip(),
                        "name": name.strip(),
                        "rank": grade,
                        "kind": kind,
                        "field": category,
                        "dblp_key": key[2] if key else None,
                        "source_url": url,
                        "catalog_page": page_number,
                        "aliases": [acronym.strip()],
                        "groups": ["ccf_a" if grade == "A" else "domain_important"],
                    }
    if len(venues) < 80:
        raise ToolError(
            f"Catalog extraction incomplete ({len(venues)} entries); refusing replacement"
        )
    data = {
        "version": version,
        "source_url": source_url,
        "imported_at": now(),
        "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        "venues": list(enrich(venues).values()),
    }
    validate(data)
    atomic_json(output, data)
    return data


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf")
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", default=str(CATALOG))
    args = parser.parse_args()
    data = import_pdf(args.pdf, args.source_url, args.version, args.output)
    print(
        json.dumps({"version": data["version"], "venues": len(data["venues"])}, ensure_ascii=False)
    )

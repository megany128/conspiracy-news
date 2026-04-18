"""
Livewire — free news source fetchers.

Pulls from HN, Reddit, NYT RSS, BBC RSS, and Wikipedia "In the News" using
stdlib only (urllib + xml.etree + json + email.utils). No API keys, no pip
deps beyond what the rest of the project already pulls in.

All fetchers return a normalized list of dicts shaped like:
    {"source": str, "id": str, "title": str, "url": str, "ts": str,
     "summary": str}

`id` is stable per item (HN objectID, Reddit t3_ fullname, RSS link, etc.) so
the journal can dedupe by it.

Usage from the cron_scan.py agent's Python tool:

    from tools.news_sources import fetch_all, fetch_hn, fetch_reddit
    items = fetch_all()   # mixed list, sorted newest first
    hn = fetch_hn()
    reddit = fetch_reddit()
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

UA = "conspiracyyy/0.1 (+https://github.com/aradotso/ara-hackathon)"
TIMEOUT = 10.0


def _fetch(url: str, timeout: float = TIMEOUT) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept-Encoding": "identity"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _iso_utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stable_id(source: str, raw: str) -> str:
    return f"{source}_{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:12]}"


# ---------- Hacker News (Algolia front page) ------------------------------


HN_URL = "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=30"


def fetch_hn(limit: int = 20) -> list[dict]:
    try:
        data = json.loads(_fetch(HN_URL).decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError):
        return []
    out: list[dict] = []
    for h in data.get("hits", [])[:limit]:
        object_id = str(h.get("objectID") or "")
        url = h.get("url") or f"https://news.ycombinator.com/item?id={object_id}"
        out.append({
            "source": "hackernews",
            "id": f"hn_{object_id}" if object_id else _stable_id("hn", url),
            "title": (h.get("title") or "").strip(),
            "url": url,
            "ts": h.get("created_at") or "",
            "summary": (h.get("story_text") or "")[:500],
        })
    return [x for x in out if x["title"]]


# ---------- Reddit popular ------------------------------------------------


REDDIT_URL = "https://www.reddit.com/r/popular.json?limit=30"


def fetch_reddit(limit: int = 20, skip_nsfw: bool = True) -> list[dict]:
    try:
        data = json.loads(_fetch(REDDIT_URL).decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError):
        return []
    out: list[dict] = []
    for c in data.get("data", {}).get("children", [])[:limit * 2]:
        d = c.get("data", {})
        if not d:
            continue
        if skip_nsfw and d.get("over_18"):
            continue
        title = (d.get("title") or "").strip()
        if not title:
            continue
        permalink = d.get("permalink") or ""
        fullname = d.get("name") or f"t3_{d.get('id','')}"
        ts_float = d.get("created_utc", 0) or 0
        try:
            ts = dt.datetime.fromtimestamp(
                float(ts_float), tz=dt.timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (TypeError, ValueError, OSError):
            ts = ""
        out.append({
            "source": "reddit",
            "id": f"rdt_{fullname}",
            "title": title,
            "url": "https://www.reddit.com" + permalink if permalink else (d.get("url") or ""),
            "ts": ts,
            "summary": (d.get("selftext") or "")[:500],
        })
        if len(out) >= limit:
            break
    return out


# ---------- Generic RSS (NYT + BBC) --------------------------------------


def _parse_rss(raw: bytes, source_name: str, limit: int = 20) -> list[dict]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    out: list[dict] = []
    for item in root.findall("channel/item")[:limit]:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        desc = (item.findtext("description") or "").strip()
        pub = item.findtext("pubDate") or ""
        try:
            ts = parsedate_to_datetime(pub).astimezone(
                dt.timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (TypeError, ValueError):
            ts = ""
        # Strip BBC-style tracking params.
        clean_link = link.split("?at_medium=", 1)[0] if "?at_medium=" in link else link
        if not title or not clean_link:
            continue
        out.append({
            "source": source_name,
            "id": _stable_id(source_name, clean_link),
            "title": title,
            "url": clean_link,
            "ts": ts,
            "summary": desc[:500],
        })
    return out


NYT_URL = "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml"
BBC_URL = "https://feeds.bbci.co.uk/news/rss.xml"


def fetch_nyt(limit: int = 20) -> list[dict]:
    try:
        return _parse_rss(_fetch(NYT_URL), "nyt", limit=limit)
    except urllib.error.URLError:
        return []


def fetch_bbc(limit: int = 20) -> list[dict]:
    try:
        return _parse_rss(_fetch(BBC_URL), "bbc", limit=limit)
    except urllib.error.URLError:
        return []


# ---------- Wikipedia In The News ----------------------------------------


def _wikipedia_url(date: dt.date) -> str:
    return (
        f"https://en.wikipedia.org/api/rest_v1/feed/featured/"
        f"{date.year:04d}/{date.month:02d}/{date.day:02d}"
    )


_TAG_RE = re.compile(r"<[^>]+>")


def fetch_wikipedia(limit: int = 20) -> list[dict]:
    today = dt.datetime.now(dt.timezone.utc).date()
    for candidate in (today, today - dt.timedelta(days=1)):
        try:
            raw = _fetch(_wikipedia_url(candidate))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue
            return []
        except urllib.error.URLError:
            return []
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return []
        out: list[dict] = []
        for story in data.get("news", [])[:limit]:
            links = story.get("links") or []
            if not links:
                continue
            lead = links[0]
            title = (lead.get("titles") or {}).get("normalized") or lead.get("title") or ""
            url = (
                ((lead.get("content_urls") or {}).get("desktop") or {}).get("page") or ""
            )
            summary = _TAG_RE.sub("", story.get("story", "")).strip()
            if not title or not url:
                continue
            out.append({
                "source": "wikipedia",
                "id": _stable_id("wiki", url),
                "title": title,
                "url": url,
                "ts": candidate.strftime("%Y-%m-%dT00:00:00Z"),
                "summary": summary[:500],
            })
        return out
    return []


# ---------- Combined ------------------------------------------------------


SOURCES = {
    "hackernews": fetch_hn,
    "reddit": fetch_reddit,
    "nyt": fetch_nyt,
    "bbc": fetch_bbc,
    "wikipedia": fetch_wikipedia,
}


def fetch_all(limit_per: int = 20) -> dict[str, list[dict]]:
    """Fetch every source; failures become empty lists (never raise)."""
    result: dict[str, list[dict]] = {}
    for name, fn in SOURCES.items():
        try:
            result[name] = fn(limit=limit_per)
        except Exception:
            result[name] = []
    return result


if __name__ == "__main__":
    # Smoke test: print a one-line summary per source.
    bundle = fetch_all(limit_per=5)
    for name, items in bundle.items():
        print(f"[{name}] {len(items)} items")
        for it in items[:3]:
            print(f"  - {it['title'][:80]}  ({it['url']})")

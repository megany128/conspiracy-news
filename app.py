"""
Livewire — the reactive reply channel (conspiracyyy-reply automation name kept for deploy compatibility).

This is the automation that handles INBOUND iMessage commands. Each `@ara.tool`
function below is SELF-CONTAINED — it imports stdlib inside its body and
inlines every helper it needs. This is because `ara deploy` only ships the
source of each tool function (via `inspect.getsource`); module-level imports
from sibling files like `tools/*.py` do NOT reach the cloud sandbox.

The `tools/` package is still used locally by `server.py` + manual tests, but
is intentionally NOT imported here.

Deploy:
    ara deploy app.py

One-off test:
    ara run app.py --input '{"message": "HELP", "phone": "+15558675309"}'
"""
from __future__ import annotations

import ara_sdk as ara  # type: ignore


# ---------- SUBSCRIBER TOOLS ---------------------------------------------


@ara.tool
def add_subscriber_tool(phone: str) -> dict:
    """Add a phone number to the subscriber list. Call this for SUBSCRIBE /
    START / YES commands.

    Args:
        phone: the sender's phone number (E.164 preferred; we'll normalize).
    """
    import datetime as dt
    import json
    import os
    import re
    import tempfile
    from pathlib import Path

    def _subs_path() -> Path:
        override = os.environ.get("CONSPIRACYYY_SUBSCRIBERS")
        return Path(override) if override else Path(tempfile.gettempdir()) / "conspiracyyy_subscribers.json"

    def _normalize(raw: str) -> str:
        if not raw:
            return ""
        raw = raw.strip()
        digits = re.sub(r"[^\d]", "", raw)
        if not digits:
            return ""
        if raw.startswith("+"):
            return "+" + digits
        if len(digits) == 10:
            return "+1" + digits
        if len(digits) == 11 and digits.startswith("1"):
            return "+" + digits
        return "+" + digits

    def _load() -> dict:
        p = _subs_path()
        if not p.exists():
            return {"subscribers": []}
        try:
            data = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            return {"subscribers": []}
        data.setdefault("subscribers", [])
        return data

    def _save(data: dict) -> None:
        p = _subs_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        tmp.replace(p)

    phone = _normalize(phone)
    if not phone:
        return {"ok": False, "error": "invalid phone"}
    data = _load()
    for s in data["subscribers"]:
        if s.get("phone") == phone:
            return {"ok": True, "already_subscribed": True, "phone": phone}
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data["subscribers"].append({"phone": phone, "subscribed_at": now_iso})
    _save(data)
    return {
        "ok": True,
        "already_subscribed": False,
        "phone": phone,
        "subscriber_count": len(data["subscribers"]),
    }


@ara.tool
def remove_subscriber_tool(phone: str) -> dict:
    """Remove a phone from the subscriber list. Call this for STOP /
    UNSUBSCRIBE / OFF commands.
    """
    import json
    import os
    import re
    import tempfile
    from pathlib import Path

    def _subs_path() -> Path:
        override = os.environ.get("CONSPIRACYYY_SUBSCRIBERS")
        return Path(override) if override else Path(tempfile.gettempdir()) / "conspiracyyy_subscribers.json"

    def _normalize(raw: str) -> str:
        if not raw:
            return ""
        raw = raw.strip()
        digits = re.sub(r"[^\d]", "", raw)
        if not digits:
            return ""
        if raw.startswith("+"):
            return "+" + digits
        if len(digits) == 10:
            return "+1" + digits
        if len(digits) == 11 and digits.startswith("1"):
            return "+" + digits
        return "+" + digits

    def _load() -> dict:
        p = _subs_path()
        if not p.exists():
            return {"subscribers": []}
        try:
            data = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            return {"subscribers": []}
        data.setdefault("subscribers", [])
        return data

    def _save(data: dict) -> None:
        p = _subs_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        tmp.replace(p)

    phone = _normalize(phone)
    if not phone:
        return {"ok": False, "error": "invalid phone"}
    data = _load()
    before = len(data["subscribers"])
    data["subscribers"] = [s for s in data["subscribers"] if s.get("phone") != phone]
    removed = before - len(data["subscribers"])
    _save(data)
    return {
        "ok": True,
        "removed": bool(removed),
        "phone": phone,
        "subscriber_count": len(data["subscribers"]),
    }


@ara.tool
def check_more_cooldown_tool(phone: str) -> dict:
    """Check whether this sender is allowed to trigger MORE right now.

    Returns {allowed: bool, wait_seconds: int}. If not allowed, reply with a
    short playful message that includes the wait_seconds instead of
    generating. DO NOT bypass this check — it's the only rate limit we have.
    """
    import datetime as dt
    import json
    import os
    import re
    import tempfile
    from pathlib import Path

    COOLDOWN = 60

    def _subs_path() -> Path:
        override = os.environ.get("CONSPIRACYYY_SUBSCRIBERS")
        return Path(override) if override else Path(tempfile.gettempdir()) / "conspiracyyy_subscribers.json"

    def _normalize(raw: str) -> str:
        if not raw:
            return ""
        raw = raw.strip()
        digits = re.sub(r"[^\d]", "", raw)
        if not digits:
            return ""
        if raw.startswith("+"):
            return "+" + digits
        if len(digits) == 10:
            return "+1" + digits
        if len(digits) == 11 and digits.startswith("1"):
            return "+" + digits
        return "+" + digits

    def _parse_iso(s: str):
        if not s:
            return None
        try:
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            return dt.datetime.fromisoformat(s)
        except ValueError:
            return None

    phone = _normalize(phone)
    p = _subs_path()
    if not p.exists():
        return {"allowed": True, "wait_seconds": 0}
    try:
        data = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {"allowed": True, "wait_seconds": 0}
    for s in data.get("subscribers", []):
        if s.get("phone") == phone:
            last = _parse_iso(s.get("last_more_at", ""))
            if not last:
                return {"allowed": True, "wait_seconds": 0}
            elapsed = (dt.datetime.now(dt.timezone.utc) - last).total_seconds()
            if elapsed >= COOLDOWN:
                return {"allowed": True, "wait_seconds": 0}
            return {"allowed": False, "wait_seconds": int(COOLDOWN - elapsed)}
    return {"allowed": True, "wait_seconds": 0}


@ara.tool
def mark_more_fired_tool(phone: str) -> dict:
    """Mark a MORE as fired after successfully generating a drop for this
    sender. Call this AFTER generate_conspiracy_tool succeeds, before
    replying.
    """
    import datetime as dt
    import json
    import os
    import re
    import tempfile
    from pathlib import Path

    def _subs_path() -> Path:
        override = os.environ.get("CONSPIRACYYY_SUBSCRIBERS")
        return Path(override) if override else Path(tempfile.gettempdir()) / "conspiracyyy_subscribers.json"

    def _normalize(raw: str) -> str:
        if not raw:
            return ""
        raw = raw.strip()
        digits = re.sub(r"[^\d]", "", raw)
        if not digits:
            return ""
        if raw.startswith("+"):
            return "+" + digits
        if len(digits) == 10:
            return "+1" + digits
        if len(digits) == 11 and digits.startswith("1"):
            return "+" + digits
        return "+" + digits

    phone = _normalize(phone)
    p = _subs_path()
    if not p.exists():
        return {"ok": True, "note": "no subscribers file"}
    try:
        data = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {"ok": False, "error": "corrupt subscribers file"}
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    found = False
    for s in data.get("subscribers", []):
        if s.get("phone") == phone:
            s["last_more_at"] = now_iso
            found = True
            break
    if found:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        tmp.replace(p)
    return {"ok": True, "marked": found}


@ara.tool
def set_interests_tool(phone: str, interests_csv: str) -> dict:
    """Save a subscriber's interests (comma-separated list) for FORCED
    COLLISION drops. Auto-subscribes the phone if they aren't already.

    Args:
        phone: the sender's phone number.
        interests_csv: e.g. "taylor swift, F1, mushroom foraging". "and"
            is also treated as a separator. Max 10 interests; duplicates
            and empties dropped; stored lowercase.
    """
    import datetime as dt
    import json
    import os
    import re
    import tempfile
    from pathlib import Path

    def _subs_path() -> Path:
        override = os.environ.get("CONSPIRACYYY_SUBSCRIBERS")
        return Path(override) if override else Path(tempfile.gettempdir()) / "conspiracyyy_subscribers.json"

    def _normalize(raw: str) -> str:
        if not raw:
            return ""
        raw = raw.strip()
        digits = re.sub(r"[^\d]", "", raw)
        if not digits:
            return ""
        if raw.startswith("+"):
            return "+" + digits
        if len(digits) == 10:
            return "+1" + digits
        if len(digits) == 11 and digits.startswith("1"):
            return "+" + digits
        return "+" + digits

    def _parse_interests(csv: str) -> list:
        if not csv:
            return []
        # Split on commas, semicolons, and " and ".
        parts = re.split(r"[,;]|\s+and\s+", csv, flags=re.IGNORECASE)
        out: list = []
        seen: set = set()
        for p in parts:
            t = (p or "").strip().lower()
            t = t.strip("\"'")
            if t and t not in seen:
                seen.add(t)
                out.append(t)
            if len(out) >= 10:
                break
        return out

    phone = _normalize(phone)
    if not phone:
        return {"ok": False, "error": "invalid phone"}
    interests = _parse_interests(interests_csv or "")
    p = _subs_path()
    data = {"subscribers": []}
    if p.exists():
        try:
            data = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            data = {"subscribers": []}
    data.setdefault("subscribers", [])
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    found = False
    for s in data["subscribers"]:
        if s.get("phone") == phone:
            s["interests"] = interests
            found = True
            break
    if not found:
        data["subscribers"].append({
            "phone": phone,
            "subscribed_at": now_iso,
            "interests": interests,
        })
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.replace(p)
    return {
        "ok": True,
        "phone": phone,
        "interests": interests,
        "auto_subscribed": not found,
        "subscriber_count": len(data["subscribers"]),
    }


@ara.tool
def get_subscriber_interests_tool(phone: str) -> dict:
    """Return this subscriber's saved interests (empty list if none). Also
    tells the caller whether the phone is subscribed at all."""
    import json
    import os
    import re
    import tempfile
    from pathlib import Path

    def _subs_path() -> Path:
        override = os.environ.get("CONSPIRACYYY_SUBSCRIBERS")
        return Path(override) if override else Path(tempfile.gettempdir()) / "conspiracyyy_subscribers.json"

    def _normalize(raw: str) -> str:
        if not raw:
            return ""
        raw = raw.strip()
        digits = re.sub(r"[^\d]", "", raw)
        if not digits:
            return ""
        if raw.startswith("+"):
            return "+" + digits
        if len(digits) == 10:
            return "+1" + digits
        if len(digits) == 11 and digits.startswith("1"):
            return "+" + digits
        return "+" + digits

    phone = _normalize(phone)
    p = _subs_path()
    if not p.exists():
        return {"interests": [], "subscribed": False}
    try:
        data = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {"interests": [], "subscribed": False}
    for s in data.get("subscribers", []):
        if s.get("phone") == phone:
            return {"interests": list(s.get("interests") or []), "subscribed": True}
    return {"interests": [], "subscribed": False}


@ara.tool
def pick_collision_pair_tool(
    interests: list,
    hn_items: list,
    reddit_items: list,
    nyt_items: list = None,
    bbc_items: list = None,
    wikipedia_items: list = None,
) -> dict:
    """Pick a headline pair where ONE side matches an audience interest and
    the OTHER side does not — the "forced collision" that makes readers learn
    about new subject areas. Pools across all provided sources (HN, Reddit,
    NYT, BBC, Wikipedia).

    Returns:
        {"aligned": {..., source_side}, "foreign": {..., source_side},
         "fallback": False} on success, or {"aligned": None, "foreign": None,
         "fallback": True} if no clean split is possible.
    """
    STOPWORDS = {"the", "a", "an", "of", "and", "or", "in", "on", "for", "to",
                 "with", "at", "by", "from", "as", "is", "was", "are", "be",
                 "this", "that", "it", "its", "but", "not", "you", "your",
                 "new", "says", "said", "will", "has", "have", "had", "about"}

    def _tokens(s: str) -> set:
        out: set = set()
        for w in (s or "").lower().split():
            clean = "".join(ch for ch in w if ch.isalnum())
            if clean and clean not in STOPWORDS and len(clean) > 2:
                out.add(clean)
        return out

    def _matches_any(title: str, ints: list) -> bool:
        t = (title or "").lower()
        title_toks = _tokens(title)
        for i in ints:
            i_clean = (i or "").strip().lower()
            if not i_clean:
                continue
            if i_clean in t:
                return True
            i_toks = _tokens(i_clean)
            if i_toks and (i_toks & title_toks):
                return True
        return False

    SENSITIVE_KEYWORDS = (
        "shooting", "shooter", "gunman", "gunmen", "mass shooting",
        "school shooting", "massacre", "terror", "terrorist", "terrorism",
        "bombing", "bomber", "suicide", "self-harm", "self harm",
        "sexual assault", "rape", "raped", "molest", "abuse",
        "child abuse", "pedophile", "groom", "trafficking",
        "domestic violence", "hate crime", "genocide", "ethnic cleansing",
        "war crime", "killed", "killing", "murder", "murdered", "homicide",
        "dead", "died", "death toll", "fatal", "fatality", "casualties",
        "missing person", "abducted", "kidnap", "overdose",
        "famine", "starvation", "refugee crisis", "humanitarian crisis",
        "airstrike", "air strike", "missile strike", "hostage",
    )

    def _is_sensitive(title: str) -> bool:
        t = (title or "").lower()
        return any(kw in t for kw in SENSITIVE_KEYWORDS)

    ints = [str(i).strip().lower() for i in (interests or []) if i]

    pools = [
        ("hackernews", list(hn_items or [])),
        ("reddit", list(reddit_items or [])),
        ("nyt", list(nyt_items or [])),
        ("bbc", list(bbc_items or [])),
        ("wikipedia", list(wikipedia_items or [])),
    ]
    pools = [(n, [x for x in p if not _is_sensitive(x.get("title", ""))]) for n, p in pools]
    pools = [(n, p) for n, p in pools if p]

    if not ints or not pools:
        return {"aligned": None, "foreign": None, "fallback": True}

    for a_name, a_pool in pools:
        for f_name, f_pool in pools:
            if a_name == f_name:
                continue
            aligned = next((x for x in a_pool if _matches_any(x.get("title", ""), ints)), None)
            foreign = next((x for x in f_pool if not _matches_any(x.get("title", ""), ints)), None)
            if aligned and foreign and aligned.get("id") != foreign.get("id"):
                a = dict(aligned); a["source_side"] = a_name
                f = dict(foreign); f["source_side"] = f_name
                return {"aligned": a, "foreign": f, "fallback": False}

    for name, pool in pools:
        aligned = next((x for x in pool if _matches_any(x.get("title", ""), ints)), None)
        foreign = next((x for x in pool if not _matches_any(x.get("title", ""), ints)), None)
        if aligned and foreign and aligned.get("id") != foreign.get("id"):
            a = dict(aligned); a["source_side"] = name
            f = dict(foreign); f["source_side"] = name
            return {"aligned": a, "foreign": f, "fallback": False}

    return {"aligned": None, "foreign": None, "fallback": True}


# ---------- NEWS + JOURNAL TOOLS ------------------------------------------


@ara.tool
def fetch_recent_headlines() -> dict:
    """Fetch today's trending items from Hacker News, Reddit, NYT, BBC, and
    Wikipedia's In-The-News feed. Stdlib only.

    Returns dict with keys "hackernews", "reddit", "nyt", "bbc", "wikipedia",
    each a list of items shaped {source, id, title, url, ts, summary}.
    """
    import datetime as dt
    import hashlib
    import json
    import re
    import urllib.error
    import urllib.request
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime

    UA = "conspiracyyy/0.1 (+https://ara.so)"

    def _fetch(url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "identity"})
        with urllib.request.urlopen(req, timeout=10.0) as r:
            return r.read()

    def _sid(prefix: str, raw: str) -> str:
        return f"{prefix}_{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:12]}"

    def _fetch_hn(limit: int = 20) -> list[dict]:
        try:
            data = json.loads(_fetch("https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=30").decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError):
            return []
        out: list[dict] = []
        for h in data.get("hits", [])[:limit]:
            oid = str(h.get("objectID") or "")
            url = h.get("url") or f"https://news.ycombinator.com/item?id={oid}"
            out.append({
                "source": "hackernews",
                "id": f"hn_{oid}" if oid else _sid("hn", url),
                "title": (h.get("title") or "").strip(),
                "url": url,
                "ts": h.get("created_at") or "",
                "summary": (h.get("story_text") or "")[:500],
            })
        return [x for x in out if x["title"]]

    def _fetch_reddit(limit: int = 20) -> list[dict]:
        try:
            data = json.loads(_fetch("https://www.reddit.com/r/popular.json?limit=30").decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError):
            return []
        out: list[dict] = []
        for c in data.get("data", {}).get("children", [])[:limit * 2]:
            d = c.get("data", {})
            if not d or d.get("over_18"):
                continue
            title = (d.get("title") or "").strip()
            if not title:
                continue
            permalink = d.get("permalink") or ""
            fullname = d.get("name") or f"t3_{d.get('id','')}"
            ts_float = d.get("created_utc", 0) or 0
            try:
                ts = dt.datetime.fromtimestamp(float(ts_float), tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
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
                ts = parsedate_to_datetime(pub).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            except (TypeError, ValueError):
                ts = ""
            clean_link = link.split("?at_medium=", 1)[0] if "?at_medium=" in link else link
            if not title or not clean_link:
                continue
            out.append({
                "source": source_name,
                "id": _sid(source_name, clean_link),
                "title": title,
                "url": clean_link,
                "ts": ts,
                "summary": desc[:500],
            })
        return out

    def _fetch_nyt(limit: int = 20) -> list[dict]:
        try:
            return _parse_rss(_fetch("https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml"), "nyt", limit=limit)
        except urllib.error.URLError:
            return []

    def _fetch_bbc(limit: int = 20) -> list[dict]:
        try:
            return _parse_rss(_fetch("https://feeds.bbci.co.uk/news/rss.xml"), "bbc", limit=limit)
        except urllib.error.URLError:
            return []

    _tag = re.compile(r"<[^>]+>")

    def _fetch_wikipedia(limit: int = 20) -> list[dict]:
        today = dt.datetime.now(dt.timezone.utc).date()
        for candidate in (today, today - dt.timedelta(days=1)):
            url = f"https://en.wikipedia.org/api/rest_v1/feed/featured/{candidate.year:04d}/{candidate.month:02d}/{candidate.day:02d}"
            try:
                raw = _fetch(url)
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
                page_url = ((lead.get("content_urls") or {}).get("desktop") or {}).get("page") or ""
                summary = _tag.sub("", story.get("story", "")).strip()
                if not title or not page_url:
                    continue
                out.append({
                    "source": "wikipedia",
                    "id": _sid("wiki", page_url),
                    "title": title,
                    "url": page_url,
                    "ts": candidate.strftime("%Y-%m-%dT00:00:00Z"),
                    "summary": summary[:500],
                })
            return out
        return []

    return {
        "hackernews": _fetch_hn(20),
        "reddit": _fetch_reddit(20),
        "nyt": _fetch_nyt(20),
        "bbc": _fetch_bbc(20),
        "wikipedia": _fetch_wikipedia(20),
    }


@ara.tool
def search_news_for_entity(entity: str) -> dict:
    """Search HN + Reddit for recent news mentioning a specific entity, so
    freeform "X + Y" drops can be grounded in real current headlines instead
    of being fully invented. Call this twice — once per entity — before
    generate_conspiracy_tool for any freeform request.

    Args:
        entity: the public figure / brand / place / cultural object to search
            for (e.g. "Olivia Rodrigo", "iPhone", "Olive Garden").

    Returns:
        {"results": [{source, id, title, url, ts, summary}, ...]} up to ~8
        items combined across HN + Reddit. Empty list if both fail.
    """
    import datetime as dt
    import hashlib
    import json
    import urllib.error
    import urllib.parse
    import urllib.request

    UA = "conspiracyyy/0.1 (+https://ara.so)"

    def _fetch(url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "identity"})
        with urllib.request.urlopen(req, timeout=10.0) as r:
            return r.read()

    q = urllib.parse.quote((entity or "").strip())
    if not q:
        return {"results": []}

    results: list[dict] = []

    # HN Algolia search (date-weighted)
    try:
        hn_data = json.loads(_fetch(
            f"https://hn.algolia.com/api/v1/search_by_date?query={q}&tags=story&hitsPerPage=6"
        ).decode("utf-8"))
        for h in hn_data.get("hits", [])[:6]:
            oid = str(h.get("objectID") or "")
            url = h.get("url") or f"https://news.ycombinator.com/item?id={oid}"
            title = (h.get("title") or "").strip()
            if not title:
                continue
            results.append({
                "source": "hackernews",
                "id": f"hn_{oid}" if oid else "hn_" + hashlib.sha1(url.encode()).hexdigest()[:12],
                "title": title,
                "url": url,
                "ts": h.get("created_at") or "",
                "summary": (h.get("story_text") or "")[:500],
            })
    except (urllib.error.URLError, json.JSONDecodeError):
        pass

    # Reddit search (newest first)
    try:
        rd_data = json.loads(_fetch(
            f"https://www.reddit.com/search.json?q={q}&limit=6&sort=new"
        ).decode("utf-8"))
        for c in rd_data.get("data", {}).get("children", [])[:12]:
            d = c.get("data", {})
            if not d or d.get("over_18"):
                continue
            title = (d.get("title") or "").strip()
            if not title:
                continue
            permalink = d.get("permalink") or ""
            fullname = d.get("name") or f"t3_{d.get('id','')}"
            ts_float = d.get("created_utc", 0) or 0
            try:
                ts = dt.datetime.fromtimestamp(float(ts_float), tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            except (TypeError, ValueError, OSError):
                ts = ""
            results.append({
                "source": "reddit",
                "id": f"rdt_{fullname}",
                "title": title,
                "url": "https://www.reddit.com" + permalink if permalink else (d.get("url") or ""),
                "ts": ts,
                "summary": (d.get("selftext") or "")[:500],
            })
    except (urllib.error.URLError, json.JSONDecodeError):
        pass

    # Newest first, cap at 8.
    results.sort(key=lambda x: x.get("ts") or "", reverse=True)
    return {"results": results[:8]}


@ara.tool
def get_journal_state() -> dict:
    """Return dedupe indexes + last 10 drops. Use before picking a pair."""
    import datetime as dt
    import json
    import os
    import tempfile
    from pathlib import Path

    def _journal_path() -> Path:
        override = os.environ.get("CONSPIRACYYY_JOURNAL")
        return Path(override) if override else Path(tempfile.gettempdir()) / "conspiracyyy_journal.json"

    def _parse_iso(s: str):
        if not s:
            return None
        try:
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            return dt.datetime.fromisoformat(s)
        except ValueError:
            return None

    p = _journal_path()
    empty = {"drops": [], "used_headline_ids": {}, "used_pair_hashes": {}}
    if not p.exists():
        return {"used_headline_ids": [], "used_pair_hashes": [], "recent_drops": []}
    try:
        data = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        data = empty
    for k, v in empty.items():
        data.setdefault(k, v)

    now = dt.datetime.now(dt.timezone.utc)
    cutoff_h = now - dt.timedelta(hours=24)
    cutoff_p = now - dt.timedelta(days=30)
    data["used_headline_ids"] = {
        k: v for k, v in data.get("used_headline_ids", {}).items()
        if (_parse_iso(v) or now) >= cutoff_h
    }
    data["used_pair_hashes"] = {
        k: v for k, v in data.get("used_pair_hashes", {}).items()
        if (_parse_iso(v) or now) >= cutoff_p
    }
    return {
        "used_headline_ids": list(data["used_headline_ids"].keys()),
        "used_pair_hashes": list(data["used_pair_hashes"].keys()),
        "recent_drops": [
            {"id": d.get("id"), "title": d.get("title"), "ts": d.get("ts")}
            for d in data.get("drops", [])[-10:]
        ],
    }


@ara.tool
def generate_conspiracy_tool(
    thing_a: str,
    thing_b: str,
    context_a: str = "",
    context_b: str = "",
    interests: list = None,
) -> dict:
    """Generate the satirical red-string conspiracy connecting two public
    entities, using Anthropic tool-use (structured output) so the returned
    dict can never be corrupted by unescaped quotes in the body.

    Args:
        context_a, context_b: optional real-headline flavour. Pass empty
            strings for freeform "X + Y" requests with no news grounding.
        interests: optional list of audience interests for FORCED COLLISION
            mode — treat thing_a as within their world, thing_b as foreign,
            and weave 1-2 sentences of real factual context about thing_b
            so the reader learns something new.

    Returns dict: {refused, title, body, red_string_score, loop_back,
    disclaimer} or {refused: true, reason: ...}.
    """
    import datetime as dt
    import os
    import subprocess
    import sys

    try:
        import anthropic  # type: ignore
    except ImportError:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet",
             "--break-system-packages", "anthropic"],
            check=True,
        )
        import anthropic  # type: ignore

    MODEL = os.environ.get("CONSPIRACY_MODEL", "claude-sonnet-4-5")
    TODAY = dt.date.today().isoformat()

    SYSTEM_PROMPT = f"""You are the RED STRING ORACLE — a chronically online, gossip-girl-meets-corkboard
conspiracy theorist who generates OBVIOUSLY SATIRICAL, dramatically unhinged
"conspiracy theories" connecting two public figures, celebrities, politicians,
fictional characters, brands, places, or cultural objects.

Today is {TODAY}. Prefer items published today. When you reference a date,
write it plainly (e.g. "{TODAY}" or "April 18") — never ##, ██, or any
censor blocks. The absurdity itself is the disclaimer.

ETHICS — NON-NEGOTIABLE:
1. ONLY accept CLEARLY PUBLIC FIGURES or cultural objects. If either input
   looks like a private non-famous individual, refuse via the emit_refusal
   tool with a playful reason asking them to pick a celebrity instead.
2. NEVER allege real crimes. NEVER invent real relationships, affairs, or
   anything that could be mistaken for fact. NEVER be mean-spirited or
   defamatory.
3. Keep it PLAYFUL, ABSURDIST, UNHINGED — think astrological patterns, menu
   items, sock colors, backwards song lyrics, birthday numerology, parking
   garages, specific shades of beige.
4. SENSITIVE TOPICS — HARD NO. Do NOT build drops around, reference, or riff on:
   mass shootings (school, church, workplace), terrorism, war casualties,
   suicide / self-harm, sexual assault, child abuse / exploitation, domestic
   violence, hate crimes, genocide, active humanitarian crises, fatal
   accidents involving named victims, missing persons, overdose deaths, or
   individual tragedies. If a provided headline touches any of these, IGNORE
   that headline entirely and call emit_refusal with reason
   "sensitive_topic" — do NOT try to reframe it as satire. The joke is
   celebrity trivia and cultural noise, never human suffering.

GROUNDING — THIS IS WHAT MAKES IT FUNNY:
If a CONTEXT block is provided below, it contains ACTUAL RECENT NEWS HEADLINES
for A and B. Weave specific concrete details from those headlines into the
body: product names, song/movie/album titles, scores, prices, places, company
names. The joke is "real current event + real current event + absurd invented
link".
- The ABSURD CONNECTION is invented (matching shapes, numerology, astrology,
  backwards lyrics, beige).
- The BASE FACTS anchoring the drop must come from the provided context.
- NEVER invent current-event "facts" not in the context.
- Speculative evidence bullets should be *obviously* speculative, not fake news.
- If NO context is provided (freeform X+Y), keep the body shorter and entirely
  speculative in tone — do not invent current events.

DATES — RELAXED:
Evidence bullets do NOT need a timestamp prefix. Not everything is time-linked.
Only mention a date when it's actually relevant (the headline explicitly
references an event on that day). When you DO use a date, write it plainly.
Never use ## or █ censor blocks.

STRUCTURE — THIS IS AN SMS:
This ships as an iMessage. Users read a lot of these. Short and punchy.
  - TOTAL body length: 100–150 words. Absolute ceiling: 170.
  - 3–5 evidence bullets. ONE claim per bullet. 8–16 words per bullet.
    Each bullet should read like a single text message.
  - Short sentences (under ~15 words). At most ONE em-dash per sentence.
    No nested parentheticals.
  - Blank line between sections:
      1. Hook — 1 short sentence. If context exists, name both events.
      2. Blank line, then the bullets.
      3. Blank line, then a SHOCKING REVELATION line in ALL CAPS (one sentence).
      4. Blank line, then a 1-sentence loop-back conclusion.
  - Do NOT start bullets with bracketed timestamps. Start with a verb, a
    name, a place, or a vibe word — something concrete.

BULLET FORMATTING (STRICT):
Each bullet MUST start on its own line, preceded by a literal newline.
Use "• " as the marker. NEVER run bullets together inline with spaces
between them. The renderer expects one bullet per line. Example (good):

  • first claim here.
  • second claim here.
  • third claim here.

Not this (bad): "• first claim. • second claim. • third claim."

EXPLAIN UNFAMILIAR TERMS:
If you use a term that isn't everyday English — a policy name, an acronym,
niche jargon — include a 4–10 word plain-English gloss the first time you
use it, inline. Example: "the pied-à-terre tax (a levy on pricey second
homes)". Don't burn a whole bullet on definitions.

VOICE: "sources say", "coincidence? I THINK NOT.", "follow the thread", "the
girls who get it, get it", "this is not a drill", "wake UP", em-dashes,
lowercase outbursts, dramatic line breaks. Sparingly use red-circle / thread
emoji.

OUTPUT:
Call emit_conspiracy with the full drop, or emit_refusal if either input is
a private individual. Do not reply in plain text."""

    news_block = ""
    if context_a or context_b:
        lines = ["", "CONTEXT — these come from today's real news headlines:"]
        if context_a:
            lines.append(f"  A's headline: {context_a}")
        if context_b:
            lines.append(f"  B's headline: {context_b}")
        lines.append(
            "Reference concrete specifics from these headlines (titles, "
            "product names, places, numbers) in the body. Do NOT invent "
            "current events not listed here. Invent ALL connections between "
            "them; do not claim the headlines are related in reality."
        )
        news_block = "\n".join(lines)

    interests_block = ""
    if interests:
        ilist = ", ".join(str(i) for i in interests if i)
        if ilist:
            interests_block = (
                f"\n\nAUDIENCE INTERESTS (forced-collision mode): the reader is into: {ilist}.\n"
                f"Treat thing_a as within their world. Treat thing_b as foreign to "
                f"them — weave 1-2 sentences of real, factual context about thing_b "
                f"into the body so they learn something concrete. The conspiracy "
                f"link remains absurdly satirical."
            )

    user_prompt = (
        f"Generate a satirical conspiracy theory connecting:\n"
        f"  A: {thing_a}\n"
        f"  B: {thing_b}\n"
        f"{news_block}"
        f"{interests_block}\n\n"
        f"Call emit_conspiracy with the drop, or emit_refusal if either input "
        f"is a private non-famous individual."
    )

    TOOLS = [
        {
            "name": "emit_conspiracy",
            "description": "Emit the satirical red-string drop.",
            "input_schema": {
                "type": "object",
                "required": ["refused", "title", "body", "red_string_score", "loop_back", "disclaimer"],
                "properties": {
                    "refused": {"type": "boolean"},
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "red_string_score": {"type": "integer", "minimum": 1, "maximum": 10},
                    "loop_back": {"type": "string"},
                    "disclaimer": {"type": "string"},
                },
            },
        },
        {
            "name": "emit_refusal",
            "description": "Refuse because an input is a private individual (not a public figure).",
            "input_schema": {
                "type": "object",
                "required": ["refused", "reason"],
                "properties": {
                    "refused": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
            },
        },
    ]

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return {"refused": True, "reason": "ANTHROPIC_API_KEY not set"}
    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        tools=TOOLS,
        tool_choice={"type": "any"},
        messages=[{"role": "user", "content": user_prompt}],
    )
    for block in msg.content:
        if getattr(block, "type", None) == "tool_use":
            out = dict(block.input or {})
            out["refused"] = bool(out.get("refused", block.name == "emit_refusal"))
            return out
    return {"refused": True, "reason": "oracle went silent — no structured output"}


@ara.tool
def record_drop(
    title: str,
    body: str,
    loop_back: str,
    red_string_score: int,
    disclaimer: str,
    source_a: dict,
    source_b: dict,
) -> dict:
    """Persist a drop to the journal so it appears on the live wire feed
    and counts toward dedupe. For freeform X+Y / RANDOM requests with no news
    grounding, pass synthetic source entries like
    {'source': 'request', 'id': 'req_<hash>', 'title': thing, 'url': ''}.
    """
    import datetime as dt
    import hashlib
    import json
    import os
    import tempfile
    from pathlib import Path

    def _journal_path() -> Path:
        override = os.environ.get("CONSPIRACYYY_JOURNAL")
        return Path(override) if override else Path(tempfile.gettempdir()) / "conspiracyyy_journal.json"

    def _pair_hash(a: str, b: str) -> str:
        x, y = sorted([a or "", b or ""])
        return hashlib.sha1(f"{x}|{y}".encode("utf-8")).hexdigest()[:16]

    p = _journal_path()
    empty = {"drops": [], "used_headline_ids": {}, "used_pair_hashes": {}}
    try:
        data = json.loads(p.read_text()) if p.exists() else dict(empty)
    except (json.JSONDecodeError, OSError):
        data = dict(empty)
    for k, v in empty.items():
        data.setdefault(k, v)

    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ts_slug = now_iso.replace(":", "").replace("-", "").replace("T", "_")[:13]
    drop_id = f"drop_{ts_slug}_{hashlib.sha1((title + now_iso).encode()).hexdigest()[:6]}"
    drop = {
        "id": drop_id,
        "ts": now_iso,
        "title": title,
        "body": body,
        "loop_back": loop_back,
        "red_string_score": red_string_score,
        "disclaimer": disclaimer,
        "source_a": source_a or {},
        "source_b": source_b or {},
        "broadcast_count": 0,
    }
    data["drops"].append(drop)
    for side in ("source_a", "source_b"):
        hid = (drop.get(side) or {}).get("id")
        if hid:
            data["used_headline_ids"][hid] = now_iso
    a_id = (source_a or {}).get("id") or ""
    b_id = (source_b or {}).get("id") or ""
    if a_id and b_id:
        data["used_pair_hashes"][_pair_hash(a_id, b_id)] = now_iso

    if len(data["drops"]) > 200:
        data["drops"] = data["drops"][-200:]

    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.replace(p)
    return drop


@ara.tool
def get_last_drop_tool() -> dict:
    """Return the most recent aired drop (for LAST / RATE commands)."""
    import json
    import os
    import tempfile
    from pathlib import Path

    override = os.environ.get("CONSPIRACYYY_JOURNAL")
    p = Path(override) if override else Path(tempfile.gettempdir()) / "conspiracyyy_journal.json"
    if not p.exists():
        return {"empty": True, "note": "no drops aired yet"}
    try:
        data = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {"empty": True, "note": "journal unreadable"}
    drops = list(data.get("drops", []))
    if not drops:
        return {"empty": True, "note": "no drops aired yet"}
    drops.sort(key=lambda d: d.get("ts", ""), reverse=True)
    return drops[0]


@ara.tool
def rate_conspiracy_tool(body: str) -> dict:
    """Score a conspiracy body on the red string scale (1-10). Uses Anthropic
    tool-use so the structured result can't be corrupted by string-level JSON
    issues."""
    import os
    import subprocess
    import sys

    try:
        import anthropic  # type: ignore
    except ImportError:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet",
             "--break-system-packages", "anthropic"],
            check=True,
        )
        import anthropic  # type: ignore

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return {"red_string_score": 7, "verdict": "cannot rate — ANTHROPIC_API_KEY missing"}
    client = anthropic.Anthropic(api_key=key)
    TOOLS = [{
        "name": "emit_rating",
        "description": "Emit the red-string rating for a conspiracy body.",
        "input_schema": {
            "type": "object",
            "required": ["red_string_score", "verdict"],
            "properties": {
                "red_string_score": {"type": "integer", "minimum": 1, "maximum": 10},
                "verdict": {"type": "string", "description": "One short chronically-online sentence."},
            },
        },
    }]
    msg = client.messages.create(
        model=os.environ.get("CONSPIRACY_MODEL", "claude-sonnet-4-5"),
        max_tokens=300,
        tools=TOOLS,
        tool_choice={"type": "tool", "name": "emit_rating"},
        messages=[{
            "role": "user",
            "content": (
                "Rate this conspiracy on the RED STRING SCALE (1=mild, "
                "10=fully unhinged) via emit_rating.\n\n"
                f"Theory:\n{body}"
            ),
        }],
    )
    for block in msg.content:
        if getattr(block, "type", None) == "tool_use":
            return dict(block.input or {})
    return {"red_string_score": 7, "verdict": "certified unhinged behavior"}


@ara.tool
def random_pair_tool() -> dict:
    """Return a real-headline-grounded pair — one fresh HN item + one fresh
    Reddit item — along with extracted canonical entities. RANDOM drops still
    need real news grounding so the conspiracy references real current events.

    Returns:
        {
          "thing_a": str,                # canonical entity from HN headline
          "thing_b": str,                # canonical entity from Reddit headline
          "context_a": str,              # the raw HN headline text
          "context_b": str,              # the raw Reddit headline text
          "source_a": {source, id, title, url},
          "source_b": {source, id, title, url},
        }

    Pass `context_a`/`context_b` straight to generate_conspiracy_tool and the
    source_a/source_b straight to record_drop. No synthetic sources.

    If both fetchers fail, falls back to a static pool pick with empty
    context strings and a synthetic source (so the tool still works offline).
    """
    import datetime as dt
    import hashlib
    import json
    import random
    import re
    import urllib.error
    import urllib.request
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime

    UA = "conspiracyyy/0.1 (+https://ara.so)"
    STOPWORDS = {"the", "a", "an", "of", "and", "or", "in", "on", "for", "to",
                 "with", "at", "by", "from", "as", "is", "was", "are", "be"}

    def _fetch(url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "identity"})
        with urllib.request.urlopen(req, timeout=10.0) as r:
            return r.read()

    def _sid(prefix: str, raw: str) -> str:
        return f"{prefix}_{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:12]}"

    def _extract_entity(headline: str) -> str:
        """Heuristic: first 1-4 capitalised words (skipping stopwords), else
        first 5 words of the headline."""
        if not headline:
            return "something"
        words = headline.split()
        picks: list[str] = []
        for w in words:
            clean = w.strip(",.:;!?\"'()[]—–-")
            if not clean:
                continue
            if clean[:1].isupper() and clean.lower() not in STOPWORDS:
                picks.append(clean)
                if len(picks) >= 4:
                    break
            elif picks:
                break
        if picks:
            return " ".join(picks)
        return " ".join(words[:5]) or headline[:40]

    def _fetch_hn(limit: int = 15) -> list[dict]:
        try:
            data = json.loads(_fetch(
                "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=30"
            ).decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError):
            return []
        out: list[dict] = []
        for h in data.get("hits", [])[:limit]:
            oid = str(h.get("objectID") or "")
            url = h.get("url") or f"https://news.ycombinator.com/item?id={oid}"
            title = (h.get("title") or "").strip()
            if not title:
                continue
            out.append({
                "source": "hackernews",
                "id": f"hn_{oid}" if oid else "hn_" + hashlib.sha1(url.encode()).hexdigest()[:12],
                "title": title,
                "url": url,
            })
        return out

    def _fetch_reddit(limit: int = 15) -> list[dict]:
        try:
            data = json.loads(_fetch(
                "https://www.reddit.com/r/popular.json?limit=30"
            ).decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError):
            return []
        out: list[dict] = []
        for c in data.get("data", {}).get("children", [])[:limit * 2]:
            d = c.get("data", {})
            if not d or d.get("over_18"):
                continue
            title = (d.get("title") or "").strip()
            if not title:
                continue
            permalink = d.get("permalink") or ""
            fullname = d.get("name") or f"t3_{d.get('id','')}"
            out.append({
                "source": "reddit",
                "id": f"rdt_{fullname}",
                "title": title,
                "url": "https://www.reddit.com" + permalink if permalink else (d.get("url") or ""),
            })
            if len(out) >= limit:
                break
        return out

    def _parse_rss(raw: bytes, source_name: str, limit: int = 15) -> list[dict]:
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            return []
        out: list[dict] = []
        for item in root.findall("channel/item")[:limit]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            clean_link = link.split("?at_medium=", 1)[0] if "?at_medium=" in link else link
            if not title or not clean_link:
                continue
            out.append({
                "source": source_name,
                "id": _sid(source_name, clean_link),
                "title": title,
                "url": clean_link,
            })
        return out

    def _fetch_nyt(limit: int = 15) -> list[dict]:
        try:
            return _parse_rss(_fetch("https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml"), "nyt", limit=limit)
        except urllib.error.URLError:
            return []

    def _fetch_bbc(limit: int = 15) -> list[dict]:
        try:
            return _parse_rss(_fetch("https://feeds.bbci.co.uk/news/rss.xml"), "bbc", limit=limit)
        except urllib.error.URLError:
            return []

    _tag = re.compile(r"<[^>]+>")

    def _fetch_wikipedia(limit: int = 15) -> list[dict]:
        today = dt.datetime.now(dt.timezone.utc).date()
        for candidate in (today, today - dt.timedelta(days=1)):
            url = f"https://en.wikipedia.org/api/rest_v1/feed/featured/{candidate.year:04d}/{candidate.month:02d}/{candidate.day:02d}"
            try:
                raw = _fetch(url)
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
                page_url = ((lead.get("content_urls") or {}).get("desktop") or {}).get("page") or ""
                if not title or not page_url:
                    continue
                out.append({
                    "source": "wikipedia",
                    "id": _sid("wiki", page_url),
                    "title": title,
                    "url": page_url,
                })
            return out
        return []

    hn = _fetch_hn(15)
    rd = _fetch_reddit(15)
    nyt = _fetch_nyt(15)
    bbc = _fetch_bbc(15)
    wiki = _fetch_wikipedia(15)

    SENSITIVE_KEYWORDS = (
        "shooting", "shooter", "gunman", "gunmen", "mass shooting",
        "school shooting", "massacre", "terror", "terrorist", "terrorism",
        "bombing", "bomber", "suicide", "self-harm", "self harm",
        "sexual assault", "rape", "raped", "molest", "abuse",
        "child abuse", "pedophile", "groom", "trafficking",
        "domestic violence", "hate crime", "genocide", "ethnic cleansing",
        "war crime", "killed", "killing", "murder", "murdered", "homicide",
        "dead", "died", "death toll", "fatal", "fatality", "casualties",
        "missing person", "abducted", "kidnap", "overdose",
        "famine", "starvation", "refugee crisis", "humanitarian crisis",
        "airstrike", "air strike", "missile strike", "hostage",
    )

    def _is_sensitive(title: str) -> bool:
        t = (title or "").lower()
        return any(kw in t for kw in SENSITIVE_KEYWORDS)

    all_items = [x for x in (hn + rd + nyt + bbc + wiki) if not _is_sensitive(x.get("title", ""))]
    if len(all_items) >= 2:
        a, b = random.sample(all_items, 2)
        # Prefer mixing different domains when possible (one news-y, one web-y).
        news_side = [x for x in all_items if x["source"] in ("nyt", "bbc", "wikipedia")]
        web_side = [x for x in all_items if x["source"] in ("hackernews", "reddit")]
        if news_side and web_side:
            a = random.choice(news_side)
            b = random.choice(web_side)
        return {
            "thing_a": _extract_entity(a["title"]),
            "thing_b": _extract_entity(b["title"]),
            "context_a": a["title"],
            "context_b": b["title"],
            "source_a": a,
            "source_b": b,
        }

    # Offline fallback — static pool, no context. Generator will produce a
    # speculative (non-news-grounded) drop per the GROUNDING rules.
    pool_a = [
        "Zendaya", "Taylor Swift", "Timothée Chalamet", "Beyoncé", "Harry Styles",
        "Pedro Pascal", "Rihanna", "Lana Del Rey", "Keanu Reeves", "Dolly Parton",
        "Shrek", "the Michelin Man", "Big Bird", "the Geico Gecko", "Garfield",
        "Oprah", "Martha Stewart", "Gordon Ramsay", "Bob Ross", "Mr. Beast",
    ]
    pool_b = [
        "Olive Garden", "the moon", "the Denver airport", "Costco hot dogs",
        "Crocs", "the Eras Tour", "the Mariana Trench", "IKEA meatballs",
        "the color beige", "Waffle House", "Mercury in retrograde",
        "the Bermuda Triangle", "Trader Joe's parking lots", "Roombas",
        "the year 2004", "Labubus", "the Stanley cup craze",
    ]
    a_pick = random.choice(pool_a)
    b_pick = random.choice(pool_b)
    rid = hashlib.sha1(f"{a_pick}|{b_pick}".encode()).hexdigest()[:8]
    return {
        "thing_a": a_pick,
        "thing_b": b_pick,
        "context_a": "",
        "context_b": "",
        "source_a": {"source": "random", "id": f"rnd_{rid}_a", "title": a_pick, "url": ""},
        "source_b": {"source": "random", "id": f"rnd_{rid}_b", "title": b_pick, "url": ""},
    }


# ---------- AUTOMATION ---------------------------------------------------


REACTIVE_SYSTEM_PROMPT = """You are the RED STRING ORACLE running the inbound reply channel for
LIVEWIRE — a chronically-online satirical conspiracy wire. Users text
a paired phone number; each inbound message is your input.

ETHICS — NON-NEGOTIABLE:
- Only public figures / brands / places / cultural objects.
- Never private individuals.
- Never allege real crimes or real relationships.
- SENSITIVE TOPICS ARE OFF-LIMITS: mass shootings, terrorism, war
  casualties, suicide, sexual assault, child abuse, domestic violence,
  hate crimes, genocide, active humanitarian crises, fatal accidents,
  missing persons, overdose deaths, or individual tragedies. Never pick a
  headline touching these topics for a drop. If the only fresh headlines
  are all sensitive, reply plainly: "the wire's quiet on funny news right
  now — today's headlines are too heavy to riff on. try MORE in a bit. 🧵"
- Every generated drop carries a disclaimer. The absurdity is the disclaimer.
- If generate_conspiracy_tool returns refused=true, pass its `reason` text
  through verbatim as the reply.

Your final assistant message IS the reply — the runtime delivers it on the
same thread. Do NOT call linq_send_message; the paired iMessage channel has
no per-recipient routing.

The user's input includes the sender's phone number in `input.phone` (or
failing that, use the paired phone route). Subscriber tools normalize for
you.

EVERY generated drop MUST be grounded in real recent news — that's what
makes the bit funny. Prefer headlines published TODAY; if everything is 2+
days old, still pick the freshest available.

DATES: don't prefix evidence bullets with timestamps — mention a date only
when it's actually relevant to a point (e.g. the headline is explicitly
tied to that day). Write dates plainly. Never use ## or █ censor blocks.

FORCED COLLISION (the core mechanic):
When a subscriber has saved interests, every drop they get should pair ONE
thing from their interests with ONE thing they've probably never heard of —
so they learn about a new subject area. The generator receives their
interests and will weave 1-2 sentences of factual context about the unfamiliar
side. Use the MORE flow below to drive this.

COMMAND ROUTING (match case-insensitively against the FIRST non-whitespace
token of the user's message, except where noted):

- SUBSCRIBE / SUB / START / YES / JOIN
    → add_subscriber_tool(phone).
    → reply: "🔴 you're IN. expect a drop every 2 hours. text MORE for one
      now. STOP to bail. — quick one: what are you into? text `INTERESTS
      <comma list>` so every drop pairs one thing you know with one you
      don't. welcome to the corkboard 🧵"
    → if already_subscribed, reply: "relax — you're already on the list 📌.
      text MORE for a fresh drop. (or update INTERESTS any time.)"

- UNSUBSCRIBE / STOP / OFF / NO / QUIT / LEAVE
    → remove_subscriber_tool(phone).
    → reply: "unsubscribed. the red string remembers. 📌"

- INTERESTS / "I'M INTO" / "IM INTO" / "I LIKE" (followed by a list)
    → strip the leading command token(s); what remains is the interests CSV.
      If nothing follows (bare INTERESTS), reply:
      "so — what are you into? text `INTERESTS taylor swift, F1, mushroom
      foraging` (comma list). we use it to pair something you know with
      something you don't. that's how you learn stuff while reading
      conspiracy slop 🧵"
      If a list follows, call set_interests_tool(phone, interests_csv=<list>).
      On ok reply: "locked in 🧵 — your red string now crosses: {interests}.
      text MORE for a collision drop." (If auto_subscribed, prepend
      "🔴 you're IN.")

- MORE / NEXT / AGAIN
    → check_more_cooldown_tool(phone). If allowed=false, reply:
      "easy tiger — ask me again in {wait_seconds}s." and STOP.
    → fetch_recent_headlines() (returns hackernews, reddit, nyt, bbc,
      wikipedia), get_journal_state(), get_subscriber_interests_tool(phone).
    → FORCED COLLISION PATH: if interests is non-empty, call
      pick_collision_pair_tool(interests, hn_items, reddit_items,
      nyt_items, bbc_items, wikipedia_items) passing all five lists. If it
      returns a non-fallback pair whose ids are BOTH unused, use it —
      `aligned` → thing_a (familiar), `foreign` → thing_b (teaches them).
    → FALLBACK PATH: if no collision pair (no interests, or no split
      possible), pick one unused item from one source + one unused item from
      a DIFFERENT source. Prefer news + web crossovers (e.g. NYT + Reddit,
      BBC + HN, Wikipedia + Reddit).
    → Extract canonical entities. Call generate_conspiracy_tool(thing_a,
      thing_b, context_a, context_b, interests=<interests list or None>).
      If refused, try a different pair (max 3 retries).
    → on success: record_drop(...), mark_more_fired_tool(phone), reply
      with title + body + loop_back.

- LAST / LATEST / RECENT
    → get_last_drop_tool(). If empty, reply: "no drops yet — the wire is
      silent 📡. text MORE to force one." Otherwise reply title + body +
      loop_back.

- SOURCES / SRC
    → get_last_drop_tool(). If empty, reply: "no drops yet — nothing to
      source."
    → Otherwise reply:
      "SOURCES for '<title>':
       A: <source_a.url or source_a.title>
       B: <source_b.url or source_b.title>
       (100% satirical — the absurd link is invented; the base facts are
       real headlines.)"
      If a side has no url, show just the title.

- RATE
    → get_last_drop_tool(); if empty, same reply as LAST. Otherwise
      rate_conspiracy_tool(body) and reply: "{score}/10 — {verdict}"

- RANDOM
    → random_pair_tool() — returns live-headline-grounded pair. Also call
      get_subscriber_interests_tool(phone) and pass interests into
      generate_conspiracy_tool if any. Pass source_a/source_b straight to
      record_drop. Reply with the drop.

- "<X> + <Y>" / "<X> and <Y>" / "connect <X> and <Y>"
    → parse the two entities. Call search_news_for_entity(thing_a) AND
      search_news_for_entity(thing_b). From each result pick the most
      relevant recent headline. Pass those as context_a/context_b.
      Also pass the subscriber's interests (via
      get_subscriber_interests_tool) into generate_conspiracy_tool. Pass
      the two result objects as source_a/source_b to record_drop. If
      either search returns empty, proceed with context="" and a
      synthesized source {source:'request', id:'req_<short-hash>',
      title:thing, url:''} for that side. Reply with the drop.

- HELP / "?" / anything unparseable
    → reply the short menu:
      "commands: SUBSCRIBE · STOP · MORE · LAST · RATE · RANDOM ·
      INTERESTS <csv> · SOURCES · 'X + Y' to connect anything. 100%
      satirical 🔴🧵"

KEEP REPLIES PUNCHY. A full drop is fine for MORE / LAST / X+Y / RANDOM, but
for SUBSCRIBE / STOP / INTERESTS / SOURCES / HELP / cooldown, one or two
short lines max.
"""


ara.Automation(
    "conspiracyyy-reply",
    system_instructions=REACTIVE_SYSTEM_PROMPT,
    tools=[
        add_subscriber_tool,
        remove_subscriber_tool,
        check_more_cooldown_tool,
        mark_more_fired_tool,
        set_interests_tool,
        get_subscriber_interests_tool,
        pick_collision_pair_tool,
        fetch_recent_headlines,
        search_news_for_entity,
        get_journal_state,
        generate_conspiracy_tool,
        record_drop,
        get_last_drop_tool,
        rate_conspiracy_tool,
        random_pair_tool,
    ],
    allow_connector_tools=True,
    required_env=["ANTHROPIC_API_KEY"],
    entrypoint="reactive iMessage command router",
)

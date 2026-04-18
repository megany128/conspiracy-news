"""
Conspiracyyy — the reactive reply channel (conspiracyyy-reply).

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


# ---------- NEWS + JOURNAL TOOLS ------------------------------------------


@ara.tool
def fetch_recent_headlines() -> dict:
    """Fetch today's trending items from Hacker News and Reddit.

    Returns dict with keys "hackernews" and "reddit", each a list of up to 20
    items shaped {source, id, title, url, ts, summary}. Uses stdlib only.
    """
    import datetime as dt
    import hashlib
    import json
    import urllib.error
    import urllib.request

    UA = "conspiracyyy/0.1 (+https://ara.so)"

    def _fetch(url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "identity"})
        with urllib.request.urlopen(req, timeout=10.0) as r:
            return r.read()

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
                "id": f"hn_{oid}" if oid else "hn_" + hashlib.sha1(url.encode()).hexdigest()[:12],
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

    return {"hackernews": _fetch_hn(20), "reddit": _fetch_reddit(20)}


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
) -> dict:
    """Generate the satirical red-string conspiracy connecting two public
    entities. context_a/context_b are optional flavour (real headlines); pass
    empty strings for freeform "X + Y" requests with no news grounding.

    Returns dict with keys: refused, title, body, red_string_score, loop_back,
    disclaimer (or {refused: true, reason: ...}).
    """
    import json
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

    SYSTEM_PROMPT = """You are the RED STRING ORACLE — a chronically online, gossip-girl-meets-corkboard
conspiracy theorist who generates OBVIOUSLY SATIRICAL, dramatically unhinged
"conspiracy theories" connecting two public figures, celebrities, politicians,
fictional characters, brands, places, or cultural objects.

ETHICS — NON-NEGOTIABLE:
1. ONLY accept CLEARLY PUBLIC FIGURES or cultural objects. If either input looks
   like a private non-famous individual (e.g. "my coworker Greg", a random full
   name with no cultural presence), REFUSE and set refused=true with a playful
   reason asking them to pick a celebrity instead.
2. NEVER allege real crimes. NEVER invent real relationships, affairs, or
   anything that could be mistaken for fact. NEVER be mean-spirited or
   defamatory. The absurdity itself is the disclaimer.
3. Keep it PLAYFUL, ABSURDIST, UNHINGED — think astrological patterns, menu
   items, sock colors, backwards song lyrics, birthday numerology, parking
   garages, specific shades of beige.

GROUNDING — THIS IS WHAT MAKES IT FUNNY:
The CONTEXT block below contains ACTUAL RECENT NEWS HEADLINES for A and B.
You MUST weave specific concrete details from those headlines into the body:
dates, product names, song/movie/album titles, scores, prices, places,
company names — whatever real specifics the headlines give you. The joke is
"real current event + real current event + absurd invented link". Example
shape: "Apple just dropped the iPhone 17 Pro. Olivia Rodrigo's GUTS vinyl
shipped the same week. The camera bump — you guessed it — is the EXACT
shape of her album cover."
- The ABSURD CONNECTION is invented (matching shapes, numerology, astrology,
  backwards lyrics, beige).
- The BASE FACTS anchoring the drop must come from the provided context.
- NEVER invent current-event "facts" not supported by the context. Do not
  claim "X launched Y yesterday" unless "Y" is literally in the headlines
  you were given.
- Speculative evidence bullets should be *obviously* speculative (astrology,
  sock colors, parking garages, specific shades of beige), not fake news.
- At least 3 evidence bullets must anchor to a concrete specific from the
  provided context (a title, date, number, name, place).
- If NO context is provided (rare fallback), keep the body shorter and
  entirely speculative in tone — do not invent current events.

STRUCTURE (aim for ~280-340 words in the body):
  - Dramatic opening hook ("Okay. OKAY. Buckle up.") that names both real
    events from the context in the first 2 sentences.
  - 5-7 "EVIDENCE" bullets, each starting with a redacted timestamp like
    "[##-##-#### • ##:## EST]" and escalating absurdity. At least 3 of
    these must cite specifics from the real headlines.
  - A SHOCKING REVELATION line in ALL CAPS.
  - Absurd conclusion that LOOPS BACK to the opening, implying the conspiracy
    goes even deeper.

VOICE: "sources say", "coincidence? I THINK NOT.", "follow the thread", "the
girls who get it, get it", "this is not a drill", "wake UP", em-dashes,
lowercase outbursts, dramatic line breaks. Sparingly use red-circle / thread
emoji."""

    news_block = ""
    if context_a or context_b:
        lines = ["", "CONTEXT — these come from today's real news headlines:"]
        if context_a:
            lines.append(f"  A's headline: {context_a}")
        if context_b:
            lines.append(f"  B's headline: {context_b}")
        lines.append(
            "Reference at least 3 concrete specifics from these headlines "
            "(titles, dates, product names, places, numbers) in the body. "
            "Do NOT invent current events not listed here — stick to what the "
            "headlines actually say. Invent ALL connections between them "
            "(the absurd red string); do not claim the headlines are related "
            "in reality."
        )
        news_block = "\n".join(lines)

    user_prompt = (
        f"Generate a satirical conspiracy theory connecting:\n"
        f"  A: {thing_a}\n"
        f"  B: {thing_b}\n"
        f"{news_block}\n\n"
        "Return ONLY a JSON object (no prose, no code fences) with this EXACT schema:\n"
        "{\n"
        '  "refused": false,\n'
        '  "title": "THE CONNECTION HAS BEEN ESTABLISHED (a dramatic ~6 word title)",\n'
        '  "body": "the full ~300-word conspiracy with markdown bullets for evidence",\n'
        '  "red_string_score": 1-10 integer (how unhinged this theory is),\n'
        '  "loop_back": "one-sentence tease that implies the theory goes even deeper",\n'
        '  "disclaimer": "This is 100% made up for entertainment purposes. No figures named were involved in any of this nonsense."\n'
        "}\n\n"
        "If EITHER input appears to be a private, non-famous individual (a "
        "random personal name with no cultural presence), instead return:\n"
        '{"refused": true, "reason": "a playful one-liner asking them to pick a celebrity, brand, or cultural object instead"}'
    )

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return {"refused": True, "reason": "ANTHROPIC_API_KEY not set"}
    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = msg.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip().rstrip("`").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {
            "refused": False,
            "title": "THE CONNECTION HAS BEEN ESTABLISHED",
            "body": text,
            "red_string_score": 7,
            "loop_back": "but that's just what they WANT you to think…",
            "disclaimer": "100% satirical fiction.",
        }


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
    """Score a conspiracy body on the red string scale (1-10)."""
    import json
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
    msg = client.messages.create(
        model=os.environ.get("CONSPIRACY_MODEL", "claude-sonnet-4-5"),
        max_tokens=200,
        messages=[{
            "role": "user",
            "content": (
                "Rate this conspiracy on the RED STRING SCALE (1=mild, 10=fully "
                'unhinged). Return ONLY JSON: {"red_string_score": int, '
                '"verdict": "one short chronically-online sentence"}.\n\n'
                f"Theory:\n{body}"
            ),
        }],
    )
    text = msg.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip().rstrip("`").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
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
    import urllib.error
    import urllib.request

    UA = "conspiracyyy/0.1 (+https://ara.so)"
    STOPWORDS = {"the", "a", "an", "of", "and", "or", "in", "on", "for", "to",
                 "with", "at", "by", "from", "as", "is", "was", "are", "be"}

    def _fetch(url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "identity"})
        with urllib.request.urlopen(req, timeout=10.0) as r:
            return r.read()

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

    hn = _fetch_hn(15)
    rd = _fetch_reddit(15)

    if hn and rd:
        a = random.choice(hn)
        b = random.choice(rd)
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
CONSPIRACYYY — a chronically-online satirical conspiracy wire. Users text
a paired phone number; each inbound message is your input.

ETHICS — NON-NEGOTIABLE:
- Only public figures / brands / places / cultural objects.
- Never private individuals.
- Never allege real crimes or real relationships.
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
makes the bit funny ("real current event + real current event + absurd
invented link"). random_pair_tool already returns live headlines + real
sources; for freeform "X + Y" use search_news_for_entity on both sides
before generating.

COMMAND ROUTING (match case-insensitively against the FIRST non-whitespace
token of the user's message):

- SUBSCRIBE / SUB / START / YES / JOIN
    → add_subscriber_tool(phone).
    → reply: "🔴 you're IN. expect a drop every 2 hours. text MORE for one
      now. STOP to bail. welcome to the corkboard 🧵"
    → if already_subscribed, reply: "relax — you're already on the list 📌.
      text MORE for a fresh drop."

- UNSUBSCRIBE / STOP / OFF / NO / QUIT / LEAVE
    → remove_subscriber_tool(phone).
    → reply: "unsubscribed. the red string remembers. 📌"

- MORE / NEXT / AGAIN
    → check_more_cooldown_tool(phone). If allowed=false, reply:
      "easy tiger — ask me again in {wait_seconds}s. the corkboard needs
      a minute." and STOP.
    → otherwise: fetch_recent_headlines(), get_journal_state(), pick one
      unused HN + one unused Reddit item from DIFFERENT domains. Extract
      canonical public entities for each. Call
      generate_conspiracy_tool(thing_a, thing_b, context_a, context_b).
      If refused, try a different pair (max 3 retries).
    → on success: record_drop(...), then mark_more_fired_tool(phone), then
      reply with the full title + body + loop_back.

- LAST / LATEST / RECENT
    → get_last_drop_tool(). If empty, reply: "no drops yet — the wire is
      silent 📡. text MORE to force one." Otherwise reply title + body +
      loop_back.

- RATE
    → get_last_drop_tool(); if empty, same reply as LAST. Otherwise
      rate_conspiracy_tool(body) and reply: "{score}/10 — {verdict}"

- RANDOM
    → random_pair_tool() — it returns {thing_a, thing_b, context_a,
      context_b, source_a, source_b} grounded in LIVE HEADLINES. Pass
      context_a and context_b straight into generate_conspiracy_tool, and
      pass source_a and source_b straight into record_drop (NO synthetic
      sources — they're already real). Reply with the drop.

- "<X> + <Y>" / "<X> and <Y>" / "connect <X> and <Y>"
    → parse the two entities. Call search_news_for_entity(thing_a) AND
      search_news_for_entity(thing_b). From each result list pick the most
      relevant recent headline (prefer ones actually naming the entity).
      Pass those headlines as context_a/context_b to
      generate_conspiracy_tool. Pass the two picked result objects
      ({source, id, title, url}) as source_a/source_b to record_drop. If
      either search returns empty, proceed with context="" and a
      synthesized source {source:'request', id:'req_<short-hash>',
      title:thing, url:''} for that side. Reply with the drop.

- HELP / "?" / anything unparseable
    → reply the short menu:
      "commands: SUBSCRIBE · STOP · MORE · LAST · RATE · RANDOM · 'X + Y' to
      connect anything. 100% satirical 🔴🧵"

KEEP REPLIES PUNCHY. A full drop is fine for MORE / LAST / X+Y / RANDOM, but
for SUBSCRIBE / STOP / HELP / cooldown, one or two short lines max.
"""


ara.Automation(
    "conspiracyyy-reply",
    system_instructions=REACTIVE_SYSTEM_PROMPT,
    tools=[
        add_subscriber_tool,
        remove_subscriber_tool,
        check_more_cooldown_tool,
        mark_more_fired_tool,
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

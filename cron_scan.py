"""
Livewire — the 24/7 wire service (conspiracyyy-wire automation name kept for deploy compatibility).

Scheduled Ara automation. Runs twice a day (morning + afternoon ET):

  1. Fetch today's trending items from Hacker News + Reddit.
  2. Pick one unrelated pair (different domains, unused in the journal).
  3. Generate a red-string satirical conspiracy connecting them.
  4. Record the drop to the journal (so /api/wire picks it up live).
  5. Send ONCE to the paired phone via linq_send_message.

Every `@ara.tool` below is SELF-CONTAINED — stdlib imports + helpers are
inlined in the body because only the function source text ships to the cloud.

Deploy (do NOT run yourself without confirming — see README):
    ara deploy cron_scan.py --cron "0 */2 * * *"

Manual one-shot for demos:
    ara run cron_scan.py
"""
from __future__ import annotations

import ara_sdk as ara  # type: ignore


@ara.tool
def fetch_recent_headlines() -> dict:
    """Fetch today's trending items from Hacker News, Reddit, NYT, BBC, and
    Wikipedia's In-The-News feed (stdlib only — no pip deps).

    Returns dict with keys "hackernews", "reddit", "nyt", "bbc", "wikipedia",
    each a list of items shaped {source, id, title, url, ts, summary}.
    Any source that fails returns an empty list — never raises.
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
def get_journal_state() -> dict:
    """Return dedupe indexes + last 10 drops. Use BEFORE picking a pair.

    Never pick a headline whose `id` is already in used_headline_ids. Never
    pick a pair whose sorted id pair hashes to anything in used_pair_hashes.
    """
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
    if not p.exists():
        return {"used_headline_ids": [], "used_pair_hashes": [], "recent_drops": []}
    try:
        data = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {"used_headline_ids": [], "used_pair_hashes": [], "recent_drops": []}
    data.setdefault("drops", [])
    data.setdefault("used_headline_ids", {})
    data.setdefault("used_pair_hashes", {})

    now = dt.datetime.now(dt.timezone.utc)
    cutoff_h = now - dt.timedelta(hours=24)
    cutoff_p = now - dt.timedelta(days=30)
    data["used_headline_ids"] = {
        k: v for k, v in data["used_headline_ids"].items()
        if (_parse_iso(v) or now) >= cutoff_h
    }
    data["used_pair_hashes"] = {
        k: v for k, v in data["used_pair_hashes"].items()
        if (_parse_iso(v) or now) >= cutoff_p
    }
    return {
        "used_headline_ids": list(data["used_headline_ids"].keys()),
        "used_pair_hashes": list(data["used_pair_hashes"].keys()),
        "recent_drops": [
            {"id": d.get("id"), "title": d.get("title"), "ts": d.get("ts")}
            for d in data["drops"][-10:]
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
    figures/brands/places/cultural objects — grounded in today's real news.

    Uses Anthropic tool-use (structured output) so the returned dict can never
    be corrupted by unescaped quotes in the body.

    Args:
        interests: optional list of audience interests. When provided, treat
            `thing_a` as within the audience's world and `thing_b` as foreign —
            the body should weave 1-2 sentences of real factual context about
            `thing_b` so the reader learns something new.

    Returns: {refused, title, body, red_string_score, loop_back, disclaimer}
             or {refused: true, reason: ...}
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
write it plainly (e.g. "{TODAY}" or "April 18") — never use ##, ██, or any
censor blocks. The absurdity itself is the disclaimer, not redacted dates.

ETHICS — NON-NEGOTIABLE:
1. ONLY accept CLEARLY PUBLIC FIGURES or cultural objects. If either input looks
   like a private non-famous individual, refuse by calling the emit_refusal
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
   individual tragedies. If EITHER provided headline touches any of these,
   IGNORE this cron tick entirely and call emit_refusal with reason
   "sensitive_topic" — do NOT try to reframe it as satire. The joke is
   celebrity trivia and cultural noise, never human suffering.

GROUNDING — THIS IS WHAT MAKES IT FUNNY:
If a CONTEXT block is provided below, it contains ACTUAL RECENT NEWS HEADLINES
for A and B. Weave specific concrete details from those headlines into the
body: product names, song/movie/album titles, scores, prices, places, company
names. The joke is "real current event + real current event + absurd invented
link". Example shape: "Apple just dropped the iPhone 17 Pro. Olivia Rodrigo's
GUTS vinyl shipped the same week. The camera bump — you guessed it — is the
EXACT shape of her album cover."
- The ABSURD CONNECTION is invented (matching shapes, numerology, astrology,
  backwards lyrics, beige).
- The BASE FACTS anchoring the drop must come from the provided context.
- NEVER invent current-event "facts" not in the context. Do not claim
  "X launched Y yesterday" unless "Y" is literally in the headlines given.
- Speculative evidence bullets should be *obviously* speculative (astrology,
  sock colors, parking garages, specific shades of beige), not fake news.
- If NO context is provided, keep the body shorter and entirely speculative
  in tone — do not invent current events.

DATES — RELAXED:
Evidence bullets do NOT need a timestamp prefix. Not everything is time-linked.
Only mention a date when it's actually relevant to the point being made (e.g.
the headline references an event on that day). When you DO use a date, write
it plainly. Never use ## or █ censor blocks.

STRUCTURE — THIS IS AN SMS BROADCAST:
This drop ships as an iMessage twice a day. Users read a lot of these.
Short and punchy.
  - TOTAL body length: 100–150 words. Absolute ceiling: 170.
  - 3–5 evidence bullets. ONE claim per bullet. 8–16 words per bullet.
    Each bullet should read like a single text message.
  - Short sentences (under ~15 words). At most ONE em-dash per sentence.
    No nested parentheticals.
  - Blank line between sections:
      1. Hook — 1 short sentence. Name both real events from the context.
      2. Blank line, then the bullets.
      3. Blank line, then a SHOCKING REVELATION line in ALL CAPS (one sentence).
      4. Blank line, then a 1-sentence loop-back conclusion that implies
         the conspiracy goes even deeper.
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
Call the emit_conspiracy tool with the full drop, OR emit_refusal if either
input is a private individual. Do not reply in plain text."""

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
            "them (the absurd red string); do not claim the headlines are "
            "related in reality."
        )
        news_block = "\n".join(lines)

    interests_block = ""
    if interests:
        ilist = ", ".join(str(i) for i in interests if i)
        if ilist:
            interests_block = (
                f"\n\nAUDIENCE INTERESTS (forced-collision mode): the reader is into: {ilist}.\n"
                f"Treat thing_a as within their world (they already know about it). "
                f"Treat thing_b as foreign/new to them — weave 1-2 sentences of real, "
                f"factual context about thing_b into the body so they learn something "
                f"concrete. The conspiracy link between A and B remains absurdly satirical."
            )

    user_prompt = (
        f"Generate a satirical conspiracy theory connecting:\n"
        f"  A: {thing_a}\n"
        f"  B: {thing_b}\n"
        f"{news_block}"
        f"{interests_block}\n\n"
        f"Call the emit_conspiracy tool with the drop. If either input is a "
        f"private non-famous individual, call emit_refusal instead."
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
                    "title": {"type": "string", "description": "Dramatic ~6-word title."},
                    "body": {"type": "string", "description": "Full ~280-340 word conspiracy body with markdown bullets."},
                    "red_string_score": {"type": "integer", "minimum": 1, "maximum": 10},
                    "loop_back": {"type": "string", "description": "One-sentence tease implying the theory goes deeper."},
                    "disclaimer": {"type": "string", "description": "Short disclaimer that this is satirical fiction."},
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
                    "reason": {"type": "string", "description": "Playful one-liner asking them to pick a celebrity, brand, or cultural object."},
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
            # Ensure refused is always present as a boolean.
            out["refused"] = bool(out.get("refused", block.name == "emit_refusal"))
            return out
    # No tool_use block — treat as a soft refusal so the workflow stops.
    return {
        "refused": True,
        "reason": "oracle went silent — no structured output returned",
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
    """Persist the generated drop to the journal + mark the pair as used.
    Returns the stored drop (with id + ts).

    Args:
        source_a, source_b: headline items {source, id, title, url}.
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

    now = dt.datetime.now(dt.timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Tick lock: the cron fires twice a day, so if a drop was recorded within the
    # last 30 minutes, this call is the agent looping inside a single tick.
    # Refuse silently and return the already-aired drop so the workflow stops.
    def _parse_iso(s: str):
        if not s:
            return None
        try:
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            return dt.datetime.fromisoformat(s)
        except ValueError:
            return None

    lock_cutoff = now - dt.timedelta(minutes=30)
    for existing in reversed(data["drops"]):
        parsed = _parse_iso(existing.get("ts", ""))
        if parsed and parsed >= lock_cutoff:
            return {
                "refused": True,
                "reason": "tick_lock: drop already aired this cron tick",
                "existing_drop_id": existing.get("id"),
                "existing_title": existing.get("title"),
                "existing_ts": existing.get("ts"),
            }

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
def subscriber_count_tool() -> dict:
    """Return {count: int}. For log line only — actual send goes to the
    paired phone via linq_send_message (no fan-out)."""
    import json
    import os
    import tempfile
    from pathlib import Path

    override = os.environ.get("CONSPIRACYYY_SUBSCRIBERS")
    p = Path(override) if override else Path(tempfile.gettempdir()) / "conspiracyyy_subscribers.json"
    if not p.exists():
        return {"count": 0}
    try:
        data = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {"count": 0}
    subs = [s for s in data.get("subscribers", []) if s.get("phone")]
    return {"count": len(subs)}


@ara.tool
def format_drop_for_sms(drop_json: str) -> str:
    """Return the formatted iMessage body for a drop.

    Args:
        drop_json: JSON string of the drop to send (the object returned by
            record_drop).
    """
    import json

    try:
        drop = json.loads(drop_json) if isinstance(drop_json, str) else dict(drop_json)
    except (json.JSONDecodeError, TypeError):
        drop = {}

    SMS_MAX = 900
    title = (drop.get("title") or "LIVEWIRE 🔴").strip()
    body = (drop.get("body") or "").strip()
    loop = (drop.get("loop_back") or "").strip()
    score = drop.get("red_string_score")
    score_line = f"red string score: {score}/10" if score is not None else ""

    trimmed = body if len(body) <= SMS_MAX else body[:SMS_MAX].rsplit(" ", 1)[0] + "…"

    parts = [title, "", trimmed]
    if loop:
        parts += ["", f"— {loop}"]
    if score_line:
        parts += ["", score_line]
    parts += [
        "",
        "reply MORE for another · LAST for the latest · STOP to unsubscribe",
        "🧵 livewire · 100% satirical fiction",
    ]
    return "\n".join(parts).strip()


@ara.tool
def get_aggregated_interests_tool() -> dict:
    """Return the union of all subscribers' interests (deduped, lowercased).

    Used by the cron workflow to drive FORCED COLLISION: pair one headline
    that matches an audience interest with one that doesn't, so subscribers
    learn about new subject areas.

    Returns: {"interests": [str, ...], "subscriber_count": int}
    """
    import json
    import os
    import tempfile
    from pathlib import Path

    override = os.environ.get("CONSPIRACYYY_SUBSCRIBERS")
    p = Path(override) if override else Path(tempfile.gettempdir()) / "conspiracyyy_subscribers.json"
    if not p.exists():
        return {"interests": [], "subscriber_count": 0}
    try:
        data = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {"interests": [], "subscriber_count": 0}
    subs = [s for s in data.get("subscribers", []) if s.get("phone")]
    bag: list = []
    seen: set = set()
    for s in subs:
        for i in (s.get("interests") or []):
            t = str(i).strip().lower()
            if t and t not in seen:
                seen.add(t)
                bag.append(t)
    return {"interests": bag, "subscriber_count": len(subs)}


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

    Args:
        interests: lowercased audience interests (strings).
        hn_items / reddit_items / nyt_items / bbc_items / wikipedia_items:
            lists of headline dicts {source, id, title, url, ...}. Any may be
            empty or omitted.

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

    # Sensitive-topic blocklist: drop these headlines from consideration
    # entirely. Keyword match on the title — coarse but adequate, since the
    # downstream generator also has a prompt-level refusal rule.
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
    # Strip sensitive headlines from every pool before any matching runs.
    pools = [(n, [x for x in p if not _is_sensitive(x.get("title", ""))]) for n, p in pools]
    pools = [(n, p) for n, p in pools if p]

    if not ints or not pools:
        return {"aligned": None, "foreign": None, "fallback": True}

    # Try every ordered (aligned_source, foreign_source) pair where sources
    # differ. Prefer different domains (e.g. NYT aligned + Reddit foreign) so
    # the collision feels genuine.
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

    # Fallback: same-pool split.
    for name, pool in pools:
        aligned = next((x for x in pool if _matches_any(x.get("title", ""), ints)), None)
        foreign = next((x for x in pool if not _matches_any(x.get("title", ""), ints)), None)
        if aligned and foreign and aligned.get("id") != foreign.get("id"):
            a = dict(aligned); a["source_side"] = name
            f = dict(foreign); f["source_side"] = name
            return {"aligned": a, "foreign": f, "fallback": False}

    return {"aligned": None, "foreign": None, "fallback": True}


# ---------- AUTOMATION ---------------------------------------------------


CRON_SYSTEM_PROMPT = """You are the RED STRING ORACLE running the scheduled wire service for
LIVEWIRE. You do NOT converse with users in this role — twice a day
you produce ONE drop and send it to the paired phone. Execute the workflow
below, in order, without skipping steps.

HARD RULE — ONE DROP PER RUN:
This entire invocation produces exactly ONE `record_drop` and exactly ONE
`linq_send_message` call. After step 9 you MUST stop calling tools. Your
next and final output is the single-line reply described in step 11. Do
NOT generate "bonus" drops, alternate angles, variations on the same event,
or multi-part sends. If today's news only supports one angle, one drop is
the correct output. More is a bug.

If `record_drop` returns `{"refused": true, "reason": "tick_lock…"}`, that
means a drop already aired this cron tick — stop immediately and reply
`wire silent: tick_lock (already aired <existing_title>)`. Do not send
anything.

ETHICS — NON-NEGOTIABLE:
- Only public figures / brands / places / cultural objects.
- No real crimes, no real relationships, no defamation.
- Every drop carries a disclaimer. Silence is better than a bad drop.

DATES:
Don't prefix evidence bullets with a date. Only mention dates when they're
actually relevant (e.g. the headline explicitly references an event on that
day). When you do use a date, write it plainly — never with ## or █ censor
blocks. Prefer items published TODAY; if everything is 2+ days old, still
pick the freshest available.

WORKFLOW:

1. Call `fetch_recent_headlines()` to get today's items from HN, Reddit,
   NYT, BBC, and Wikipedia's In-The-News feed. Expect keys: hackernews,
   reddit, nyt, bbc, wikipedia. Any may be empty if that source failed.
2. Call `get_journal_state()` to see which headline ids and pair hashes have
   already aired. Never reuse a headline id. Never reuse a pair.
3. Call `get_aggregated_interests_tool()` to get the union of all
   subscribers' interests.
4. FORCED COLLISION: if interests is non-empty, call
   `pick_collision_pair_tool(interests, hn_items, reddit_items,
   nyt_items, bbc_items, wikipedia_items)` passing all five lists. If it
   returns a non-fallback pair whose ids are BOTH not in used_headline_ids,
   use it — `aligned` becomes thing_a (interest-adjacent, what the audience
   already knows), `foreign` becomes thing_b (the thing they'll learn about).
   Otherwise fall through to step 5.
5. FALLBACK: pick ONE item from one source and ONE from a DIFFERENT source
   such that:
   - Neither id is in used_headline_ids.
   - The two items are topically UNRELATED — different domains, different
     vibes. NYT + Reddit, BBC + HN, Wikipedia + Reddit, etc. all beat
     same-source pairs.
   - Both items clearly reference at least one public figure, brand, place,
     or cultural object. If an item is about a private individual, skip it.
   - HARD SKIP any headline about: mass shootings, terrorism, war
     casualties, suicide, sexual assault, child abuse, domestic violence,
     hate crimes, genocide, active humanitarian crises, fatal accidents,
     missing persons, overdose deaths, or individual tragedies. These are
     off-limits for the satire — never pick them, never try to riff on
     them. If both fetched buckets are dominated by sensitive news, stop
     silently with reason "no safe pair available this tick".
6. From each chosen headline, extract the canonical public entity. That's
   `thing_a` / `thing_b`. Keep the raw headline text as `context_a` /
   `context_b`.
7. Call `generate_conspiracy_tool(thing_a, thing_b, context_a, context_b,
   interests=<aggregated interests>)`. Passing interests tells the generator
   to weave teaching context about thing_b into the body. If it returns
   refused=true, go back to step 4/5 and pick a different pair (max 3
   retries). If still refused, stop silently.
8. Call `record_drop(...)` with the full drop + the two source items
   (each {source, id, title, url}).
9. Call `format_drop_for_sms(drop_json=<record_drop's JSON return>)`, then
   call `linq_send_message` ONCE with the formatted text.
10. Call `subscriber_count_tool()` for the log line.
11. Reply with a single line: `aired "<title>" → paired phone (N on list)`
    (or `wire silent: <reason>` if you stopped). That reply is the cron run
    output, not a user-facing message.
"""


ara.Automation(
    "conspiracyyy-wire",
    system_instructions=CRON_SYSTEM_PROMPT,
    tools=[
        fetch_recent_headlines,
        get_journal_state,
        get_aggregated_interests_tool,
        pick_collision_pair_tool,
        generate_conspiracy_tool,
        record_drop,
        subscriber_count_tool,
        format_drop_for_sms,
    ],
    allow_connector_tools=True,
    required_env=["ANTHROPIC_API_KEY"],
    entrypoint="scheduled wire scan — pick a pair, generate, send",
)

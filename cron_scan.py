"""
Conspiracyyy — the 24/7 wire service (conspiracyyy-wire).

Scheduled Ara automation. Runs every 2 hours:

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
    """Fetch today's trending items from Hacker News and Reddit (stdlib only).

    Returns dict with keys "hackernews" and "reddit", each a list of up to 20
    items shaped {source, id, title, url, ts, summary}.
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
    context_a: str,
    context_b: str,
) -> dict:
    """Generate the satirical red-string conspiracy connecting two public
    figures/brands/places/cultural objects — grounded in today's real news.

    Returns: {refused, title, body, red_string_score, loop_back, disclaimer}
             or {refused: true, reason: ...}
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

    # Tick lock: the cron fires every 2h, so if a drop was recorded within the
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
    title = (drop.get("title") or "CONSPIRACYYY 🔴").strip()
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
        "🧵 conspiracyyy · 100% satirical fiction",
    ]
    return "\n".join(parts).strip()


# ---------- AUTOMATION ---------------------------------------------------


CRON_SYSTEM_PROMPT = """You are the RED STRING ORACLE running the scheduled wire service for
CONSPIRACYYY. You do NOT converse with users in this role — every 2 hours
you produce ONE drop and send it to the paired phone. Execute the workflow
below, in order, without skipping steps.

HARD RULE — ONE DROP PER RUN:
This entire invocation produces exactly ONE `record_drop` and exactly ONE
`linq_send_message` call. After step 8 you MUST stop calling tools. Your
next and final output is the single-line reply described in step 10. Do
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

WORKFLOW:

1. Call `fetch_recent_headlines()` to get today's items from HN and Reddit.
2. Call `get_journal_state()` to see which headline ids and pair hashes have
   already aired. Never reuse a headline id. Never reuse a pair.
3. Pick ONE item from the HN list and ONE from the Reddit list such that:
   - Neither id is in used_headline_ids.
   - The two items are topically UNRELATED — different domains, different
     vibes. Tech + pop-culture beats tech + tech. The absurdity comes from
     distance.
   - Both items clearly reference at least one public figure, brand, place,
     or cultural object. If an item is about a private individual, skip it.
4. From each chosen headline, extract the canonical public entity — a
   celebrity name, a brand, a product, a place. That's `thing_a` / `thing_b`.
   Keep the raw headline text as `context_a` / `context_b`.
5. Call `generate_conspiracy_tool(thing_a, thing_b, context_a, context_b)`.
   If it returns refused=true, go back to step 3 and pick a different pair
   (max 3 retries total). If still refused, stop silently — do not send.
6. Call `record_drop(...)` with the full drop + the two source items
   (each {source, id, title, url}). This makes the drop show up on the live
   wire feed at the landing page.
7. Call `format_drop_for_sms(drop_json=<record_drop's JSON return>)` to get
   the message text.
8. Call `linq_send_message` ONCE with the formatted text. The runtime
   delivers it to the single paired phone (there is no recipient argument —
   one send per cron tick, not a fan-out loop).
9. Call `subscriber_count_tool()` for the log line.
10. Reply with a single line: `aired "<title>" → paired phone (N on list)`
    (or `wire silent: <reason>` if you stopped). That reply is the cron run
    output, not a user-facing message.
"""


ara.Automation(
    "conspiracyyy-wire",
    system_instructions=CRON_SYSTEM_PROMPT,
    tools=[
        fetch_recent_headlines,
        get_journal_state,
        generate_conspiracy_tool,
        record_drop,
        subscriber_count_tool,
        format_drop_for_sms,
    ],
    allow_connector_tools=True,
    required_env=["ANTHROPIC_API_KEY"],
    entrypoint="scheduled wire scan — pick a pair, generate, send",
)

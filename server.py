"""
Livewire — local web server.

Serves index.html and exposes the JSON API the frontend calls. Uses the
Anthropic SDK directly for fast in-browser generation, and hits the Ara run API
for iMessage delivery via the `linq_send_message` connector.

Run:
    python server.py
    # open http://127.0.0.1:8787

Env vars (put in .env.local next to this file, or export):
    ANTHROPIC_API_KEY — required for conspiracy generation
    ARA_APP_ID        — required for iMessage send (printed by `ara deploy app.py`)
    ARA_RUNTIME_KEY   — required for iMessage send (starts with `ak_app_...`)
    ARA_API_BASE      — optional, defaults to https://api.ara.so
    PORT              — optional, defaults to 8787
"""
from __future__ import annotations

import datetime as dt
import json
import os
import random
import re
import sys
import threading
import time
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent

# Make `from tools import ...` work when running this file directly.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import broadcast as _broadcast  # noqa: E402
from tools import journal as _journal      # noqa: E402
from tools import news_sources as _news     # noqa: E402


# ---------- tiny .env loader (no deps) ------------------------------------

def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


for fname in (".env", ".env.local"):
    _load_dotenv(ROOT / fname)


# ---------- anthropic (pip-install on demand) -----------------------------

try:
    import anthropic  # type: ignore
except ImportError:  # pragma: no cover
    import subprocess
    print("[setup] installing anthropic SDK…", file=sys.stderr)
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "anthropic"],
        check=True,
    )
    import anthropic  # type: ignore


CLAUDE_MODEL = os.environ.get("CONSPIRACY_MODEL", "claude-sonnet-4-5")


def _anthropic_client() -> "anthropic.Anthropic":
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    return anthropic.Anthropic(api_key=key)


# ---------- prompt --------------------------------------------------------

def _system_prompt() -> str:
    today = dt.date.today().isoformat()
    return f"""You are the RED STRING ORACLE — a chronically online, gossip-girl-meets-corkboard
conspiracy theorist who generates OBVIOUSLY SATIRICAL, dramatically unhinged
"conspiracy theories" connecting two public figures, celebrities, politicians,
fictional characters, brands, places, or cultural objects.

Today is {today}.

HARD RULES ABOUT DATES (ABSOLUTELY NO EXCEPTIONS):
- NEVER use "██", "▒▒", "##", "XX", "??", or ANY censor/redacted placeholder
  for any part of a date or time. Not for the year, not for the day, not for
  the hour. Not ever.
- Do NOT prefix evidence bullets with a bracketed timestamp like "[...date...]".
  Drop that format entirely. It is the cliché we are actively avoiding.
- Not every bullet needs a date. Mention a date ONLY when it is genuinely
  relevant to the point, and when you do, write it plainly (e.g. "{today}",
  "April 2024", "last Tuesday", "in 2019").
- If a bullet refers to something time-linked, work the date into the prose
  ("in July 2022, …") rather than as a bracketed prefix.

ETHICS — NON-NEGOTIABLE:
1. ONLY accept CLEARLY PUBLIC FIGURES or cultural objects. If either input looks
   like a private non-famous individual, refuse via the emit_refusal tool with
   a playful reason asking them to pick a celebrity instead.
2. NEVER allege real crimes. NEVER invent real relationships, affairs, or
   anything that could be mistaken for fact. NEVER be mean-spirited or
   defamatory. Do NOT invent quotes, Instagram stories, interviews, or private
   social-media activity as "evidence" — base evidence on publicly-known,
   obviously-fictional-when-connected elements (fashion, colors, numerology,
   release dates, song lyrics, menu items, etc.) or on REAL recent headlines
   provided to you as grounding.
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

STRUCTURE — READ ON A PHONE:
This is read on a phone. Punchy beats dense. Short beats long. Users get
a lot of these, so each one must be quick to read.
  - TOTAL body length: 110–160 words. Absolute ceiling: 180.
  - 3–5 evidence bullets. ONE claim per bullet. 8–16 words per bullet.
    Each bullet should read like a single text message.
  - Short sentences (under ~15 words). At most ONE em-dash per sentence.
    No nested parentheticals.
  - Blank line between sections:
      1. Hook — 1 short sentence.
      2. Blank line, then the bullets.
      3. Blank line, then a SHOCKING REVELATION line in ALL CAPS (one sentence).
      4. Blank line, then a 1-sentence loop-back conclusion.
  - Do NOT start bullets with bracketed timestamps. Start with a verb, a
    name, a location, or a vibe word — something concrete.

BULLET FORMATTING (STRICT):
Each bullet MUST start on its own line, preceded by a literal newline.
Use "• " as the marker. NEVER run bullets together inline with spaces
between them. The renderer expects one bullet per line. Example (good):

  • first claim here.
  • second claim here.
  • third claim here.

Not this (bad): "• first claim. • second claim. • third claim."

EXPLAIN UNFAMILIAR TERMS:
If you use a term that isn't everyday English — a policy name like
"pied-à-terre tax", an acronym, a niche cultural reference, legal/technical
jargon — include a 4–10 word plain-English gloss the first time you mention
it. Example: "the pied-à-terre tax (a levy on expensive second homes)".
Keep the gloss inline; do not burn a whole bullet on definitions.

VOICE: "sources say", "coincidence? I THINK NOT.", "follow the thread 🧵",
"the girls who get it, get it", "this is not a drill", "wake UP", em-dashes,
lowercase outbursts, dramatic line breaks. Sparingly use 🔴🧵📌👀.

OUTPUT:
Call emit_conspiracy with the full drop, or emit_refusal if either input is a
private individual. Do not reply in plain text."""


CONSPIRACY_TOOLS = [
    {
        "name": "emit_conspiracy",
        "description": "Emit the satirical red-string drop.",
        "input_schema": {
            "type": "object",
            "required": ["refused", "title", "body", "red_string_score", "loop_back", "disclaimer"],
            "properties": {
                "refused": {"type": "boolean"},
                "title": {"type": "string", "description": "Dramatic ~6-word title."},
                "body": {"type": "string", "description": "Full ~280–340 word conspiracy body with markdown bullets."},
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


RATING_TOOLS = [{
    "name": "emit_rating",
    "description": "Rate a conspiracy on the red-string scale.",
    "input_schema": {
        "type": "object",
        "required": ["red_string_score", "verdict"],
        "properties": {
            "red_string_score": {"type": "integer", "minimum": 1, "maximum": 10},
            "verdict": {"type": "string", "description": "One short chronically-online sentence."},
        },
    },
}]


RANDOM_POOL_A = [
    "Zendaya", "Taylor Swift", "Timothée Chalamet", "Beyoncé", "Harry Styles",
    "Pedro Pascal", "Rihanna", "Lana Del Rey", "Keanu Reeves", "Dolly Parton",
    "Shrek", "the Michelin Man", "Big Bird", "the Geico Gecko", "Garfield",
    "Oprah", "Martha Stewart", "Gordon Ramsay", "Bob Ross", "Mr. Beast",
]
RANDOM_POOL_B = [
    "Olive Garden", "the moon", "the Denver airport", "Costco hot dogs",
    "Crocs", "the Eras Tour", "the Mariana Trench", "IKEA meatballs",
    "the color beige", "Waffle House", "Mercury in retrograde",
    "the Bermuda Triangle", "Trader Joe's parking lots", "Roombas",
    "the year 2004", "Labubus", "the Stanley cup craze",
    "the number 47", "girl dinner", "corn mazes",
]


# ---------- news grounding cache -----------------------------------------

_NEWS_CACHE: dict = {"ts": 0.0, "bundle": {}}
_NEWS_LOCK = threading.Lock()
_NEWS_TTL_SECONDS = 300  # 5 min


def _fetch_news_bundle() -> dict:
    """Return a cached bundle of {source: [items]} across all 5 sources."""
    with _NEWS_LOCK:
        now = time.time()
        if now - _NEWS_CACHE["ts"] < _NEWS_TTL_SECONDS and _NEWS_CACHE["bundle"]:
            return _NEWS_CACHE["bundle"]
        try:
            bundle = _news.fetch_all(limit_per=15)
        except Exception:  # pragma: no cover — never kill a request over news
            bundle = {}
        _NEWS_CACHE["bundle"] = bundle or {}
        _NEWS_CACHE["ts"] = now
        return _NEWS_CACHE["bundle"]


_SEARCH_CACHE: dict = {}  # {query: (ts, [items])}
_SEARCH_LOCK = threading.Lock()
_SEARCH_TTL_SECONDS = 300  # 5 min


_PICK_PAIR_TOOL = [{
    "name": "pick_pair",
    "description": "Pick the MOST INTERESTING interest-aligned headline and one clearly foreign headline, by id.",
    "input_schema": {
        "type": "object",
        "required": ["aligned_id", "foreign_id"],
        "properties": {
            "aligned_id": {"type": "string", "description": "id of the single MOST INTERESTING headline semantically related to any listed interest. \"Tech\" should match AI / chips / Claude / programming / startups / hardware, not just literal 'tech'. \"Fashion\" should match designers / runway / brand drops, not only the word 'fashion'. Prefer recent, juicy, or culturally-loaded items over boring meta-news."},
            "foreign_id": {"type": "string", "description": "id of a headline that is clearly unrelated to any listed interest — something the audience would genuinely learn about. Pick the most interesting foreign option too."},
            "aligned_reason": {"type": "string", "description": "1-sentence why the aligned pick matches AND why it's interesting."},
        },
    },
}]


def _llm_pick_interest_pair(interests: list[str], headlines: list[dict]) -> tuple[dict | None, dict | None]:
    """Ask Claude to semantically pair an interest-aligned headline with a
    foreign one. Returns (aligned, foreign) items from the input list, or
    (None, None) if it can't decide."""
    if not interests or len(headlines) < 2:
        return (None, None)
    try:
        client = _anthropic_client()
    except RuntimeError:
        return (None, None)

    # Compact the candidate list to keep prompt cheap.
    candidates = headlines[:30]
    lines = [f"{h['id']} [{h.get('source','?')}] {h['title']}" for h in candidates]
    user = (
        "AUDIENCE INTERESTS: " + ", ".join(interests) + "\n\n"
        "HEADLINES (id, source, title):\n" + "\n".join(lines) + "\n\n"
        "Call pick_pair with two ids from the list above. aligned_id must "
        "be the headline most SEMANTICALLY related to any of the interests "
        "(e.g. 'tech' matches AI / chips / programming / Claude / startups, "
        "not only literal 'tech'). foreign_id must be clearly unrelated to "
        "any interest — something the audience would learn about.\n\n"
        "HARD EXCLUSIONS — never pick a headline about: mass shootings, "
        "terrorism, war casualties, suicide, sexual assault, child abuse, "
        "domestic violence, hate crimes, genocide, active humanitarian "
        "crises, fatal accidents, missing persons, overdose deaths, or any "
        "individual tragedy. Skip those entirely and pick from the rest. "
        "If EVERY headline is sensitive, pick the two least-sensitive by id "
        "and we'll filter downstream."
    )
    try:
        msg = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=300,
            tools=_PICK_PAIR_TOOL,
            tool_choice={"type": "tool", "name": "pick_pair"},
            messages=[{"role": "user", "content": user}],
        )
    except Exception:
        return (None, None)
    by_id = {h["id"]: h for h in candidates}
    for block in msg.content:
        if getattr(block, "type", None) == "tool_use":
            payload = dict(block.input or {})
            a = by_id.get(payload.get("aligned_id"))
            f = by_id.get(payload.get("foreign_id"))
            if a and f and a["id"] != f["id"]:
                return (a, f)
    return (None, None)


def _search_headlines(query: str, limit: int = 8) -> list[dict]:
    """Search HN Algolia + Reddit for recent items matching `query`. Cached
    per-query for 5 min. Used by interest-driven random generation so that
    thing_a is guaranteed to touch something the user actually cares about,
    even when today's trending feeds don't happen to mention it."""
    import hashlib
    import urllib.parse

    q = (query or "").strip()
    if not q:
        return []

    cache_key = q.lower()
    with _SEARCH_LOCK:
        hit = _SEARCH_CACHE.get(cache_key)
        if hit and time.time() - hit[0] < _SEARCH_TTL_SECONDS:
            return list(hit[1])

    qenc = urllib.parse.quote(q)
    results: list[dict] = []

    # HN Algolia by-date search — recent items only.
    try:
        req = urllib.request.Request(
            f"https://hn.algolia.com/api/v1/search_by_date?query={qenc}&tags=story&hitsPerPage={limit}",
            headers={"User-Agent": "livewire/0.1 (+https://ara.so)", "Accept-Encoding": "identity"},
        )
        with urllib.request.urlopen(req, timeout=6.0) as r:
            data = json.loads(r.read().decode("utf-8"))
        for h in data.get("hits", [])[:limit]:
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
    except (urllib.error.URLError, json.JSONDecodeError, Exception):
        pass

    # Reddit search — newest first.
    try:
        req = urllib.request.Request(
            f"https://www.reddit.com/search.json?q={qenc}&limit={limit}&sort=new",
            headers={"User-Agent": "livewire/0.1 (+https://ara.so)", "Accept-Encoding": "identity"},
        )
        with urllib.request.urlopen(req, timeout=6.0) as r:
            data = json.loads(r.read().decode("utf-8"))
        for c in data.get("data", {}).get("children", [])[:limit]:
            d = c.get("data", {})
            if not d or d.get("over_18"):
                continue
            title = (d.get("title") or "").strip()
            if not title:
                continue
            permalink = d.get("permalink") or ""
            fullname = d.get("name") or f"t3_{d.get('id','')}"
            results.append({
                "source": "reddit",
                "id": f"rdt_{fullname}",
                "title": title,
                "url": "https://www.reddit.com" + permalink if permalink else (d.get("url") or ""),
                "ts": "",
                "summary": (d.get("selftext") or "")[:500],
            })
    except (urllib.error.URLError, json.JSONDecodeError, Exception):
        pass

    with _SEARCH_LOCK:
        _SEARCH_CACHE[cache_key] = (time.time(), list(results))
    return results


_SENSITIVE_KEYWORDS = (
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


def _is_sensitive_headline(title: str) -> bool:
    t = (title or "").lower()
    return any(kw in t for kw in _SENSITIVE_KEYWORDS)


def _flat_headlines(bundle: dict, cap: int = 40) -> list[dict]:
    out: list[dict] = []
    for items in bundle.values():
        for it in items or []:
            title = (it.get("title") or "").strip()
            if title and not _is_sensitive_headline(title):
                out.append(it)
    random.shuffle(out)
    return out[:cap]


_STOPWORDS = {
    "the", "a", "an", "of", "to", "and", "or", "in", "on", "for", "with",
    "is", "are", "was", "were", "be", "been", "as", "at", "by", "from",
    "that", "this", "it", "its", "into", "over", "after", "before", "new",
    "says", "said", "will", "has", "have", "had", "not", "but", "about",
}


def _tokens(text: str) -> set:
    return {
        w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
        if len(w) > 2 and w not in _STOPWORDS
    }


def _matches_interest(text: str, interests: list) -> bool:
    t = _tokens(text)
    for i in interests:
        for w in _tokens(i):
            if w in t:
                return True
    return False


# ---------- output sanitizer ---------------------------------------------

_CENSOR_RE = re.compile(r"[█▓▒░]{1,}")
_DATE_HASHES_RE = re.compile(r"#{2,}")
_QUESTION_DATE_RE = re.compile(r"\?{2,}")
_BRACKET_TS_RE = re.compile(r"\[\s*[^\[\]]*(?:██|▒▒|##|XX|\?\?)[^\[\]]*\]", re.IGNORECASE)


def _sanitize_body(body: str) -> str:
    """Defensive net: if the model sneaks a censor block through, strip it.
    We drop any bracketed timestamp that contains ██/##/XX/??, and scrub
    bare censor runs. Keeps the rest of the bullet text intact."""
    if not body:
        return body
    # 1) Kill entire bracketed timestamp prefixes like "[██-██-2024 • ##:## EST] — "
    body = _BRACKET_TS_RE.sub("", body)
    # 2) Scrub bare censor/hash runs that might sit mid-sentence.
    body = _CENSOR_RE.sub("", body)
    body = _DATE_HASHES_RE.sub("", body)
    # 3) Clean up leading " — " fragments left behind by #1.
    body = re.sub(r"(^|\n)[\s•\-\u2022]*(?:—|-)\s+", r"\1• ", body)
    # 4) Collapse runs of whitespace introduced by the scrubs.
    body = re.sub(r"[ \t]{2,}", " ", body)
    return body


def _pick_random_pair(interests: list | None) -> dict:
    """Pick a (thing_a, thing_b) pair. When interests are given:
      - thing_a is GUARANTEED to touch something the user cares about. We
        first scan today's trending bundle for an interest-aligned headline.
        If nothing in trending matches, we SEARCH HN + Reddit for a random
        interest so alignment is always possible.
      - thing_b is biased foreign — something they'll learn about.
    Without interests: pick two random real headlines across all 5 sources.
    Falls through to static celebrity pools only if every source fails."""
    bundle = _fetch_news_bundle()
    headlines = _flat_headlines(bundle, cap=60)

    ints = [str(i).strip() for i in (interests or []) if str(i).strip()]

    # Interest path — aligned side is guaranteed to touch an interest.
    if ints:
        a_pick: dict | None = None
        b_pick: dict | None = None

        # 1) PRIMARY: Claude picks the MOST INTERESTING interest-aligned
        #    headline + a foreign counterpart. Catches semantic matches
        #    literal tokens would miss ("tech" → Claude / GPU / chips) and
        #    avoids boring literal-word hits.
        if headlines:
            a_llm, f_llm = _llm_pick_interest_pair(ints, headlines)
            if a_llm and f_llm:
                a_pick, b_pick = a_llm, f_llm

        # 2) Fallback: literal-token match (used only if Claude is unavailable
        #    or returned something unusable).
        if not a_pick or not b_pick:
            trending_aligned = [h for h in headlines if _matches_interest(h["title"], ints)]
            foreign_pool = [h for h in headlines if not _matches_interest(h["title"], ints)]
            if trending_aligned and foreign_pool:
                a_pick = random.choice(trending_aligned)
                b_pool = [h for h in foreign_pool if h.get("id") != a_pick.get("id")]
                if b_pool:
                    b_pick = random.choice(b_pool)

        # 3) Last resort: search HN + Reddit directly for an interest, when
        #    today's bundle has nothing relevant at all.
        if not a_pick:
            shuffled = list(ints)
            random.shuffle(shuffled)
            for interest in shuffled:
                search_hits = _search_headlines(interest, limit=8)
                if search_hits:
                    a_pick = random.choice(search_hits)
                    foreign_pool = [h for h in headlines if h.get("id") != a_pick.get("id")]
                    if foreign_pool:
                        b_pick = random.choice(foreign_pool)
                    break

        if a_pick and b_pick:
            return {
                "thing_a": a_pick["title"],
                "thing_b": b_pick["title"],
                "context_a": a_pick,
                "context_b": b_pick,
            }

    # No-interest path — two random real headlines.
    if len(headlines) >= 2:
        a_pick, b_pick = random.sample(headlines, 2)
        return {
            "thing_a": a_pick["title"],
            "thing_b": b_pick["title"],
            "context_a": a_pick,
            "context_b": b_pick,
        }

    # Absolute fallback: static pools (only when every news source failed).
    pool_all = RANDOM_POOL_A + RANDOM_POOL_B
    if ints:
        aligned = [x for x in pool_all if _matches_interest(x, ints)]
        foreign = [x for x in pool_all if not _matches_interest(x, ints)]
        if aligned and foreign:
            return {
                "thing_a": random.choice(aligned),
                "thing_b": random.choice(foreign),
            }
    return {
        "thing_a": random.choice(RANDOM_POOL_A),
        "thing_b": random.choice(RANDOM_POOL_B),
    }


def _format_context_block(ctx: dict | None, label: str) -> str:
    if not ctx:
        return ""
    title = (ctx.get("title") or "").strip()
    src = (ctx.get("source") or "").strip()
    ts = (ctx.get("ts") or "").strip()
    url = (ctx.get("url") or "").strip()
    summary = (ctx.get("summary") or "").strip()
    bits = [f"  {label} title: {title}"]
    if src:
        bits.append(f"  {label} source: {src}")
    if ts:
        bits.append(f"  {label} published: {ts}")
    if url:
        bits.append(f"  {label} url: {url}")
    if summary:
        bits.append(f"  {label} summary: {summary[:280]}")
    return "\n".join(bits)


def _format_trending_block(headlines: list[dict], limit: int = 10) -> str:
    if not headlines:
        return ""
    today = dt.date.today().isoformat()
    lines = [f"TRENDING HEADLINES RIGHT NOW (today is {today}) — you MAY weave one or two of these in as REAL-world absurd-sounding grounding:"]
    for h in headlines[:limit]:
        src = h.get("source") or "?"
        title = (h.get("title") or "").strip()
        if title:
            lines.append(f"  • [{src}] {title}")
    return "\n".join(lines)


def generate_conspiracy(
    thing_a: str,
    thing_b: str,
    deeper_context: str | None = None,
    interests: list | None = None,
    context_a: dict | None = None,
    context_b: dict | None = None,
    trending: list[dict] | None = None,
) -> dict:
    """Generate a drop via Anthropic tool-use — no JSON-string round-trip, so
    unescaped quotes in the body can't break the return value."""
    client = _anthropic_client()

    parts = [
        f"Generate a satirical conspiracy theory connecting:",
        f"  A: {thing_a}",
        f"  B: {thing_b}",
    ]
    ctx_a = _format_context_block(context_a, "A")
    ctx_b = _format_context_block(context_b, "B")
    if ctx_a or ctx_b:
        parts.append("\nGROUNDING CONTEXT (real recent info about A and/or B — reference these REAL facts instead of inventing social-media activity):")
        if ctx_a:
            parts.append(ctx_a)
        if ctx_b:
            parts.append(ctx_b)
    if trending:
        trending_block = _format_trending_block(trending, limit=8)
        if trending_block:
            parts.append("\n" + trending_block)
    if deeper_context:
        parts.append(
            "\nTHIS IS A 'GO DEEPER' REQUEST. The existing theory is below — "
            "escalate with a THIRD twist/connection that builds on it, introduce "
            "one new element (a number, a color, a location), and make it "
            "weirder while keeping the same voice.\n\n"
            f"EXISTING THEORY:\n{deeper_context}"
        )
    if interests:
        ilist = ", ".join(str(i) for i in interests if i)
        if ilist:
            parts.append(
                f"\nAUDIENCE INTERESTS (forced-collision mode): the reader is "
                f"into: {ilist}. Treat thing_a as within their world. Treat "
                f"thing_b as foreign to them — weave 1–2 sentences of real, "
                f"factual context about thing_b into the body so they learn "
                f"something concrete. The conspiracy link remains absurdly "
                f"satirical."
            )
    parts.append(
        "\nREMEMBER: no bracketed-timestamp evidence prefixes, no censor "
        "blocks (██/##/XX), no invented Instagram stories or interviews. "
        "Call emit_conspiracy with the drop, or emit_refusal if either input "
        "is a private non-famous individual."
    )

    msg = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2000,
        system=_system_prompt(),
        tools=CONSPIRACY_TOOLS,
        tool_choice={"type": "any"},
        messages=[{"role": "user", "content": "\n".join(parts)}],
    )
    for block in msg.content:
        if getattr(block, "type", None) == "tool_use":
            out = dict(block.input or {})
            out["refused"] = bool(out.get("refused", block.name == "emit_refusal"))
            if isinstance(out.get("body"), str):
                out["body"] = _sanitize_body(out["body"])
            return out
    return {
        "refused": True,
        "reason": "the oracle went silent — try again.",
    }


def rate_conspiracy(text_body: str) -> dict:
    client = _anthropic_client()
    msg = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=300,
        tools=RATING_TOOLS,
        tool_choice={"type": "tool", "name": "emit_rating"},
        messages=[{
            "role": "user",
            "content": (
                "Rate this conspiracy on the RED STRING SCALE (1=mild, "
                "10=fully unhinged) via emit_rating.\n\n"
                f"Theory:\n{text_body}"
            ),
        }],
    )
    for block in msg.content:
        if getattr(block, "type", None) == "tool_use":
            return dict(block.input or {})
    return {"red_string_score": 7, "verdict": "certified unhinged behavior 📌"}


def send_via_ara(phone: str, title: str, body: str) -> dict:
    app_id = os.environ.get("ARA_APP_ID")
    runtime_key = os.environ.get("ARA_RUNTIME_KEY")
    api_base = os.environ.get("ARA_API_BASE", "https://api.ara.so").rstrip("/")
    if not app_id or not runtime_key:
        return {
            "ok": False,
            "error": (
                "Ara is not wired up yet. Run `ara deploy app.py` and set "
                "ARA_APP_ID + ARA_RUNTIME_KEY in .env.local."
            ),
        }

    instruction = (
        f"Send this conspiracy to phone number {phone} via linq_send_message. "
        f"If the linq connector does not accept a 'to' argument, send it to "
        f"the paired phone route instead. The message body to send is:\n\n"
        f"{title}\n\n{body}\n\n"
        f"— generated by Livewire 🔴 (100% satirical fiction)"
    )

    payload = {
        "agent_id": "conspiracyyy-reply",
        "workflow_id": "conspiracyyy-reply",
        "warmup": False,
        "input": {
            "message": instruction,
            "phone": phone,
            "title": title,
            "body": body,
            "run_id": f"web-{random.randint(10**9, 10**10 - 1)}",
        },
    }

    url = f"{api_base}/v1/apps/{app_id}/run"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {runtime_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body_bytes = resp.read()
        data = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
        return {"ok": True, "ara_response": data}
    except urllib.error.HTTPError as e:  # pragma: no cover
        return {"ok": False, "error": f"Ara HTTP {e.code}: {e.read().decode('utf-8', 'ignore')}"}
    except Exception as e:  # pragma: no cover
        return {"ok": False, "error": f"Ara call failed: {e}"}


# ---------- HTTP handler --------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        sys.stderr.write(f"[{self.log_date_time_string()}] {format % args}\n")

    def do_OPTIONS(self) -> None:  # CORS preflight
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._serve_file(ROOT / "index.html", "text/html; charset=utf-8")
            return
        if path == "/healthz":
            self._send_json(200, {"ok": True})
            return
        if path == "/api/wire":
            drops = _journal.recent_drops(limit=15)
            stats = _journal.stats()
            stats["subscriber_count"] = _broadcast.subscriber_count()
            self._send_json(200, {"drops": drops, "stats": stats})
            return
        if path == "/api/stats":
            stats = _journal.stats()
            stats["subscriber_count"] = _broadcast.subscriber_count()
            # Next scheduled drop: top of the next even 2h slot (UTC).
            import datetime as _dt
            now = _dt.datetime.now(_dt.timezone.utc)
            next_hour = (now.hour // 2 + 1) * 2
            next_drop = now.replace(minute=0, second=0, microsecond=0)
            if next_hour >= 24:
                next_drop = (next_drop + _dt.timedelta(days=1)).replace(hour=0)
            else:
                next_drop = next_drop.replace(hour=next_hour)
            stats["next_drop_eta_seconds"] = int((next_drop - now).total_seconds())
            stats["next_drop_at"] = next_drop.strftime("%Y-%m-%dT%H:%M:%SZ")
            self._send_json(200, stats)
            return
        self.send_error(404, "Not found")

    def _serve_file(self, path: Path, content_type: str) -> None:
        if not path.exists():
            self.send_error(404, f"{path.name} not found")
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        try:
            if path == "/api/conspiracy":
                payload = self._read_json()
                a = (payload.get("thing_a") or "").strip()
                b = (payload.get("thing_b") or "").strip()
                if not a or not b:
                    return self._send_json(400, {"error": "thing_a and thing_b are required"})
                deeper = payload.get("deeper_context")
                interests = payload.get("interests") or None
                if isinstance(interests, list):
                    interests = [str(i).strip() for i in interests if str(i).strip()]
                    if not interests:
                        interests = None
                else:
                    interests = None
                trending = _flat_headlines(_fetch_news_bundle(), cap=20)
                result = generate_conspiracy(
                    a, b,
                    deeper_context=deeper,
                    interests=interests,
                    trending=trending,
                )
                return self._send_json(200, result)

            if path == "/api/rate":
                payload = self._read_json()
                body = (payload.get("body") or "").strip()
                if not body:
                    return self._send_json(400, {"error": "body is required"})
                return self._send_json(200, rate_conspiracy(body))

            if path == "/api/random":
                payload = self._read_json()
                interests = payload.get("interests") if isinstance(payload, dict) else None
                if isinstance(interests, list):
                    interests = [str(i).strip() for i in interests if str(i).strip()]
                    if not interests:
                        interests = None
                else:
                    interests = None
                pair = _pick_random_pair(interests)
                trending = _flat_headlines(_fetch_news_bundle(), cap=20)
                result = generate_conspiracy(
                    pair["thing_a"], pair["thing_b"],
                    interests=interests,
                    context_a=pair.get("context_a"),
                    context_b=pair.get("context_b"),
                    trending=trending,
                )
                # Don't leak full context objects back to the client as-is.
                result["_pair"] = {
                    "thing_a": pair["thing_a"],
                    "thing_b": pair["thing_b"],
                    "source_a": (pair.get("context_a") or {}).get("source"),
                    "source_b": (pair.get("context_b") or {}).get("source"),
                    "url_a": (pair.get("context_a") or {}).get("url"),
                    "url_b": (pair.get("context_b") or {}).get("url"),
                }
                return self._send_json(200, result)

            if path == "/api/send-imessage":
                payload = self._read_json()
                phone = (payload.get("phone") or "").strip()
                title = (payload.get("title") or "").strip()
                body = (payload.get("body") or "").strip()
                if not phone or not body:
                    return self._send_json(400, {"error": "phone and body are required"})
                return self._send_json(200, send_via_ara(phone, title, body))

            self.send_error(404, "Not found")
        except Exception as e:  # pragma: no cover
            import traceback
            traceback.print_exc()
            self._send_json(500, {"error": str(e)})


def main() -> None:
    port = int(os.environ.get("PORT", "8787"))
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("WARNING: ANTHROPIC_API_KEY not set — /api/conspiracy will 500.", file=sys.stderr)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"\n🔴 Livewire running at http://127.0.0.1:{port}\n", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down…", file=sys.stderr)
        httpd.server_close()


if __name__ == "__main__":
    main()

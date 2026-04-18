"""
Conspiracyyy — shared conspiracy-generation + rating module.

Used by:
  - the cron_scan.py agent (pairs two real news headlines)
  - the app.py reactive router (freeform "X + Y" requests)
  - server.py (landing page one-shot demo)

Exposes:
  - generate_conspiracy(thing_a, thing_b, *, context_a=None, context_b=None,
                        deeper_context=None) -> dict
  - rate_conspiracy(body) -> dict
  - SYSTEM_PROMPT (the shared Red String Oracle voice)
  - extract_entities(headline) -> str (heuristic for cron scan; Claude
    does the real work)

`generate_conspiracy` returns a dict with keys:
    refused, title, body, red_string_score, loop_back, disclaimer
    (or {refused: true, reason: ...} if the inputs are private individuals)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

# Try to pull anthropic off the environment; fall back to on-demand pip so
# this module works both inside the Ara sandbox (which pip-installs at tool
# call time per v1 pattern) and in server.py.
try:
    import anthropic  # type: ignore
except ImportError:  # pragma: no cover
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet",
         "--break-system-packages", "anthropic"],
        check=True,
    )
    import anthropic  # type: ignore


CLAUDE_MODEL = os.environ.get("CONSPIRACY_MODEL", "claude-sonnet-4-5")


SYSTEM_PROMPT = """\
You are the RED STRING ORACLE — a chronically online, gossip-girl-meets-corkboard
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

STRUCTURE (aim for ~280–340 words in the body):
  - Dramatic opening hook ("Okay. OKAY. Buckle up.")
  - 5–7 "EVIDENCE" bullets, each starting with a redacted timestamp like
    "[██-██-████ • ██:██ EST]" and escalating absurdity
  - A SHOCKING REVELATION line in ALL CAPS
  - Absurd conclusion that LOOPS BACK to the opening, implying the conspiracy
    goes even deeper

VOICE: "sources say", "coincidence? I THINK NOT.", "follow the thread 🧵",
"the girls who get it, get it", "this is not a drill", "wake UP", em-dashes,
lowercase outbursts, dramatic line breaks. Sparingly use 🔴🧵📌👀.
"""


USER_PROMPT_TEMPLATE = """Generate a satirical conspiracy theory connecting:
  A: {a}
  B: {b}
{news_context}
{extra}

Return ONLY a JSON object (no prose, no code fences) with this EXACT schema:
{{
  "refused": false,
  "title": "THE CONNECTION HAS BEEN ESTABLISHED 🔴 (a dramatic ~6 word title)",
  "body": "the full ~300-word conspiracy with markdown bullets for evidence",
  "red_string_score": 1-10 integer (how unhinged this theory is),
  "loop_back": "one-sentence tease that implies the theory goes even deeper",
  "disclaimer": "This is 100% made up for entertainment purposes. No figures named were involved in any of this nonsense."
}}

If EITHER input appears to be a private, non-famous individual (a random
personal name with no cultural presence), instead return:
{{"refused": true, "reason": "a playful one-liner asking them to pick a celebrity, brand, or cultural object instead"}}
"""


def _client() -> "anthropic.Anthropic":
    # Prefer ara.secret when running inside the sandbox; fall back to env.
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        try:
            import ara_sdk as ara  # type: ignore
            key = ara.secret("ANTHROPIC_API_KEY")
        except Exception:
            pass
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    return anthropic.Anthropic(api_key=key)


def _parse_json_loose(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        if t.startswith("json"):
            t = t[4:]
        t = t.strip().rstrip("`").strip()
    return json.loads(t)


def _news_block(context_a: str | None, context_b: str | None) -> str:
    if not context_a and not context_b:
        return ""
    lines = ["", "CONTEXT — these come from today's real news headlines:"]
    if context_a:
        lines.append(f"  A's headline: {context_a}")
    if context_b:
        lines.append(f"  B's headline: {context_b}")
    lines.append(
        "Use the headlines for flavour — reference specifics (a company, a "
        "release, a vibe) so the theory feels plausibly today — but invent "
        "ALL connections between them. Do not claim the headlines are "
        "related in reality; the connection is purely the absurd red string."
    )
    return "\n".join(lines)


def generate_conspiracy(
    thing_a: str,
    thing_b: str,
    *,
    context_a: str | None = None,
    context_b: str | None = None,
    deeper_context: str | None = None,
) -> dict[str, Any]:
    """Main generator. Returns the parsed JSON response (see module docstring)."""
    client = _client()
    extra = ""
    if deeper_context:
        extra = (
            "\nTHIS IS A 'GO DEEPER' REQUEST. The existing theory is below — "
            "escalate with a THIRD twist/connection that builds on it, "
            "introduce one new random element (a number, a color, a "
            "location), and make it weirder while keeping the same voice.\n\n"
            f"EXISTING THEORY:\n{deeper_context}\n"
        )

    prompt = USER_PROMPT_TEMPLATE.format(
        a=thing_a,
        b=thing_b,
        news_context=_news_block(context_a, context_b),
        extra=extra,
    )

    msg = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text

    try:
        return _parse_json_loose(text)
    except json.JSONDecodeError:
        return {
            "refused": False,
            "title": "THE CONNECTION HAS BEEN ESTABLISHED 🔴",
            "body": text,
            "red_string_score": 7,
            "loop_back": "but that's just what they WANT you to think…",
            "disclaimer": "This is 100% made up for entertainment purposes.",
        }


def rate_conspiracy(body: str) -> dict[str, Any]:
    client = _client()
    msg = client.messages.create(
        model=CLAUDE_MODEL,
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
    try:
        return _parse_json_loose(msg.content[0].text)
    except json.JSONDecodeError:
        return {"red_string_score": 7, "verdict": "certified unhinged behavior 📌"}


# ---------- Entity extraction helper (used by cron_scan) ------------------


def extract_entity_from_headline(headline: str) -> str:
    """Very rough first-pass: let the LLM handle the real work, but if we need
    a quick label for logs/UI we take the first capitalised noun phrase-ish
    chunk. The actual public-figure identification happens inside the agent's
    planning prompt — this is just a display fallback.
    """
    words = (headline or "").split()
    picks = []
    for w in words:
        w_clean = w.strip(",.:;!?\"'()[]")
        if w_clean and w_clean[:1].isupper() and w_clean.lower() not in {
            "the", "a", "an", "of", "and", "or", "in", "on", "for", "to",
        }:
            picks.append(w_clean)
            if len(picks) >= 3:
                break
    return " ".join(picks) or (headline[:40] if headline else "something")

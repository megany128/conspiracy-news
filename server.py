"""
Conspiracyyy — local web server.

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

import json
import os
import random
import sys
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


def _parse_json_loose(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        if t.startswith("json"):
            t = t[4:]
        t = t.strip().rstrip("`").strip()
    return json.loads(t)


def generate_conspiracy(thing_a: str, thing_b: str, deeper_context: str | None = None) -> dict:
    client = _anthropic_client()
    extra = ""
    if deeper_context:
        extra = (
            "THIS IS A 'GO DEEPER' REQUEST. The existing theory is below — "
            "escalate with a THIRD twist/connection that builds on it, introduce "
            "one new random element (a number, a color, a location), and make "
            "it weirder while keeping the same voice.\n\n"
            f"EXISTING THEORY:\n{deeper_context}\n"
        )
    msg = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": USER_PROMPT_TEMPLATE.format(a=thing_a, b=thing_b, extra=extra),
        }],
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


def rate_conspiracy(text_body: str) -> dict:
    client = _anthropic_client()
    msg = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=200,
        messages=[{
            "role": "user",
            "content": (
                "Rate this conspiracy on the RED STRING SCALE (1=mild, 10=fully "
                'unhinged). Return ONLY JSON: {"red_string_score": int, '
                '"verdict": "one short chronically-online sentence"}.\n\n'
                f"Theory:\n{text_body}"
            ),
        }],
    )
    try:
        return _parse_json_loose(msg.content[0].text)
    except json.JSONDecodeError:
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
        f"— generated by Conspiracyyy 🔴 (100% satirical fiction)"
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
                result = generate_conspiracy(a, b, deeper_context=deeper)
                return self._send_json(200, result)

            if path == "/api/rate":
                payload = self._read_json()
                body = (payload.get("body") or "").strip()
                if not body:
                    return self._send_json(400, {"error": "body is required"})
                return self._send_json(200, rate_conspiracy(body))

            if path == "/api/random":
                pair = {
                    "thing_a": random.choice(RANDOM_POOL_A),
                    "thing_b": random.choice(RANDOM_POOL_B),
                }
                result = generate_conspiracy(pair["thing_a"], pair["thing_b"])
                result["_pair"] = pair
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
    print(f"\n🔴 Conspiracyyy running at http://127.0.0.1:{port}\n", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down…", file=sys.stderr)
        httpd.server_close()


if __name__ == "__main__":
    main()

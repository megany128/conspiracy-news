"""
Livewire — subscriber list + drop formatting for broadcast.

The actual iMessage send is performed by the Ara agent's LLM calling the
runtime-provided `linq_send_message` connector tool — this module just
manages the subscriber list, a per-sender cooldown, and the text body that
gets sent.

Storage: `/tmp/conspiracyyy_subscribers.json` by default, override with
the `CONSPIRACYYY_SUBSCRIBERS` env var.

Schema:
    {
      "subscribers": [
        {
          "phone": "+15558675309",
          "subscribed_at": "2026-04-18T14:30:00Z",
          "last_more_at": "2026-04-18T14:32:00Z"   // optional
        }
      ]
    }
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

MORE_COOLDOWN_SECONDS = 60  # per-sender cooldown on MORE command


def subscribers_path() -> Path:
    override = os.environ.get("CONSPIRACYYY_SUBSCRIBERS")
    if override:
        return Path(override)
    return Path(tempfile.gettempdir()) / "conspiracyyy_subscribers.json"


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str) -> dt.datetime | None:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return dt.datetime.fromisoformat(s)
    except ValueError:
        return None


def normalize_phone(raw: str) -> str:
    """Keep only digits and a leading +. Good enough for dedupe."""
    if not raw:
        return ""
    raw = raw.strip()
    digits = re.sub(r"[^\d]", "", raw)
    if not digits:
        return ""
    if raw.startswith("+"):
        return "+" + digits
    # Assume US if exactly 10 digits.
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return "+" + digits


def _empty_state() -> dict[str, Any]:
    return {"subscribers": []}


def _load() -> dict[str, Any]:
    p = subscribers_path()
    if not p.exists():
        return _empty_state()
    try:
        data = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return _empty_state()
    data.setdefault("subscribers", [])
    return data


def _save_atomic(data: dict[str, Any]) -> None:
    p = subscribers_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.replace(p)


def _find(subs: list[dict], phone: str) -> dict | None:
    for s in subs:
        if s.get("phone") == phone:
            return s
    return None


# ---------- Public API ----------------------------------------------------


def add_subscriber(phone: str) -> dict[str, Any]:
    phone = normalize_phone(phone)
    if not phone:
        return {"ok": False, "error": "invalid phone"}
    data = _load()
    existing = _find(data["subscribers"], phone)
    if existing:
        return {"ok": True, "already_subscribed": True, "phone": phone}
    data["subscribers"].append({"phone": phone, "subscribed_at": _now_iso()})
    _save_atomic(data)
    return {"ok": True, "already_subscribed": False, "phone": phone,
            "subscriber_count": len(data["subscribers"])}


def remove_subscriber(phone: str) -> dict[str, Any]:
    phone = normalize_phone(phone)
    if not phone:
        return {"ok": False, "error": "invalid phone"}
    data = _load()
    before = len(data["subscribers"])
    data["subscribers"] = [s for s in data["subscribers"] if s.get("phone") != phone]
    removed = before - len(data["subscribers"])
    _save_atomic(data)
    return {"ok": True, "removed": bool(removed), "phone": phone,
            "subscriber_count": len(data["subscribers"])}


def list_subscribers() -> list[str]:
    data = _load()
    return [s["phone"] for s in data["subscribers"] if s.get("phone")]


def subscriber_count() -> int:
    return len(list_subscribers())


def check_more_cooldown(phone: str) -> dict[str, Any]:
    """Return {allowed: bool, wait_seconds: int}. Call before honoring MORE."""
    phone = normalize_phone(phone)
    data = _load()
    s = _find(data["subscribers"], phone)
    if not s:
        return {"allowed": True, "wait_seconds": 0}
    last = _parse_iso(s.get("last_more_at", ""))
    if not last:
        return {"allowed": True, "wait_seconds": 0}
    elapsed = (dt.datetime.now(dt.timezone.utc) - last).total_seconds()
    if elapsed >= MORE_COOLDOWN_SECONDS:
        return {"allowed": True, "wait_seconds": 0}
    return {"allowed": False,
            "wait_seconds": int(MORE_COOLDOWN_SECONDS - elapsed)}


def mark_more_fired(phone: str) -> None:
    phone = normalize_phone(phone)
    data = _load()
    s = _find(data["subscribers"], phone)
    if s:
        s["last_more_at"] = _now_iso()
        _save_atomic(data)


# ---------- Message formatting -------------------------------------------


SMS_MAX = 900  # comfortable iMessage-length; truncate well above the 160 SMS seg.


def format_drop_for_sms(drop: dict[str, Any], include_cta: bool = True) -> str:
    """Build the iMessage body sent to each subscriber."""
    title = (drop.get("title") or "LIVEWIRE 🔴").strip()
    body = (drop.get("body") or "").strip()
    loop = (drop.get("loop_back") or "").strip()
    score = drop.get("red_string_score")
    score_line = f"red string score: {score}/10\n" if score is not None else ""

    # Truncate the body so multi-segment SMS costs stay reasonable. iMessage
    # delivers full; SMS fallback will tail off at ~160 chars/seg anyway.
    trimmed_body = body if len(body) <= SMS_MAX else body[:SMS_MAX].rsplit(" ", 1)[0] + "…"

    parts = [title, "", trimmed_body]
    if loop:
        parts += ["", f"— {loop}"]
    parts += ["", score_line.rstrip()]
    if include_cta:
        parts += ["", "reply MORE for another · LAST for the latest · STOP to unsubscribe",
                  "🧵 livewire · 100% satirical fiction"]
    return "\n".join(p for p in parts if p is not None).strip()


if __name__ == "__main__":
    print("path:", subscribers_path())
    print(add_subscriber("555-867-5309"))
    print(list_subscribers())
    print(check_more_cooldown("555-867-5309"))

"""
Livewire — file-journal persistence.

Stores the rolling history of aired drops + de-dupe indexes so the cron scan
never re-airs the same headline pair. The journal is also the source of
truth for the live wire feed on the landing page.

Storage: a single JSON file. Default location is `/tmp/conspiracyyy_journal.json`
(the sandbox scratch dir used throughout the project). Override via the
`CONSPIRACYYY_JOURNAL` env var if you need a persistent volume.

Schema:
    {
      "drops": [
        {
          "id": "drop_20260418_1432",
          "ts": "2026-04-18T14:32:00Z",
          "title": "...",
          "body": "...",
          "loop_back": "...",
          "red_string_score": 8,
          "disclaimer": "...",
          "source_a": {"source": "...", "id": "...", "title": "...", "url": "..."},
          "source_b": {"source": "...", "id": "...", "title": "...", "url": "..."},
          "broadcast_count": 0
        },
        ...
      ],
      "used_headline_ids": {"<id>": "<first_seen_iso>"},
      "used_pair_hashes":  {"<hash>": "<first_seen_iso>"}
    }

- `drops` is capped at MAX_DROPS newest.
- `used_headline_ids` expires after HEADLINE_TTL_HOURS.
- `used_pair_hashes` expires after PAIR_TTL_DAYS.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

MAX_DROPS = 200
HEADLINE_TTL_HOURS = 24
PAIR_TTL_DAYS = 30


def journal_path() -> Path:
    override = os.environ.get("CONSPIRACYYY_JOURNAL")
    if override:
        return Path(override)
    return Path(tempfile.gettempdir()) / "conspiracyyy_journal.json"


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str) -> dt.datetime | None:
    if not s:
        return None
    try:
        # Accept both "...Z" and "...+00:00".
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return dt.datetime.fromisoformat(s)
    except ValueError:
        return None


def pair_hash(id_a: str, id_b: str) -> str:
    a, b = sorted([id_a or "", id_b or ""])
    return hashlib.sha1(f"{a}|{b}".encode("utf-8")).hexdigest()[:16]


def _empty_state() -> dict[str, Any]:
    return {"drops": [], "used_headline_ids": {}, "used_pair_hashes": {}}


def _load() -> dict[str, Any]:
    p = journal_path()
    if not p.exists():
        return _empty_state()
    try:
        data = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return _empty_state()
    # Fill in missing keys for forward-compat.
    for k, v in _empty_state().items():
        data.setdefault(k, v)
    return data


def _save_atomic(data: dict[str, Any]) -> None:
    p = journal_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.replace(p)


def _prune(data: dict[str, Any]) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    # Trim drops.
    drops = data.get("drops", [])
    if len(drops) > MAX_DROPS:
        data["drops"] = drops[-MAX_DROPS:]
    # Expire headline ids.
    cutoff_h = now - dt.timedelta(hours=HEADLINE_TTL_HOURS)
    data["used_headline_ids"] = {
        k: v for k, v in data.get("used_headline_ids", {}).items()
        if (_parse_iso(v) or now) >= cutoff_h
    }
    # Expire pair hashes.
    cutoff_p = now - dt.timedelta(days=PAIR_TTL_DAYS)
    data["used_pair_hashes"] = {
        k: v for k, v in data.get("used_pair_hashes", {}).items()
        if (_parse_iso(v) or now) >= cutoff_p
    }


# ---------- Public API ----------------------------------------------------


def get_state() -> dict[str, Any]:
    """Return a snapshot of the journal (for agent inspection / /api/wire)."""
    data = _load()
    _prune(data)
    return data


def seen_headline(headline_id: str) -> bool:
    if not headline_id:
        return False
    state = get_state()
    return headline_id in state["used_headline_ids"]


def seen_pair(id_a: str, id_b: str) -> bool:
    state = get_state()
    return pair_hash(id_a, id_b) in state["used_pair_hashes"]


def filter_unused(items: Iterable[dict]) -> list[dict]:
    state = get_state()
    used = state["used_headline_ids"]
    return [it for it in items if it.get("id") and it["id"] not in used]


def record_drop(drop: dict[str, Any]) -> dict[str, Any]:
    """Persist a drop and mark its source headlines + pair as used.

    `drop` should include:
      - id, ts, title, body, loop_back, red_string_score, disclaimer
      - source_a, source_b (each: {source, id, title, url})
      - broadcast_count (defaults to 0 if missing)
    """
    drop = dict(drop)  # shallow copy to avoid mutating caller's ref
    drop.setdefault("ts", _now_iso())
    drop.setdefault("broadcast_count", 0)
    if not drop.get("id"):
        ts = drop["ts"].replace(":", "").replace("-", "").replace("T", "_")[:13]
        drop["id"] = f"drop_{ts}_{hashlib.sha1((drop.get('title','') + drop['ts']).encode()).hexdigest()[:6]}"

    data = _load()
    data["drops"].append(drop)

    now = _now_iso()
    for side in ("source_a", "source_b"):
        src = drop.get(side) or {}
        hid = src.get("id")
        if hid:
            data["used_headline_ids"][hid] = now

    a_id = (drop.get("source_a") or {}).get("id") or ""
    b_id = (drop.get("source_b") or {}).get("id") or ""
    if a_id and b_id:
        data["used_pair_hashes"][pair_hash(a_id, b_id)] = now

    _prune(data)
    _save_atomic(data)
    return drop


def bump_broadcast_count(drop_id: str, delta: int = 1) -> None:
    data = _load()
    for d in data.get("drops", []):
        if d.get("id") == drop_id:
            d["broadcast_count"] = int(d.get("broadcast_count", 0)) + delta
            break
    _save_atomic(data)


def recent_drops(limit: int = 15) -> list[dict]:
    """Newest first (for the wire feed)."""
    data = _load()
    drops = list(data.get("drops", []))
    drops.sort(key=lambda d: d.get("ts", ""), reverse=True)
    return drops[:limit]


def last_drop() -> dict[str, Any] | None:
    items = recent_drops(limit=1)
    return items[0] if items else None


def stats() -> dict[str, Any]:
    """Counts for the landing page header."""
    data = _load()
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    today_count = sum(
        1 for d in data.get("drops", []) if (d.get("ts") or "").startswith(today)
    )
    return {
        "total_drops": len(data.get("drops", [])),
        "today_count": today_count,
    }


if __name__ == "__main__":
    # Smoke test: record a fake drop and read it back.
    import pprint
    fake = {
        "title": "TEST CONSPIRACY 🔴",
        "body": "test body",
        "loop_back": "tbd",
        "red_string_score": 7,
        "disclaimer": "100% satirical test.",
        "source_a": {"source": "hackernews", "id": "hn_test_a", "title": "A", "url": "u1"},
        "source_b": {"source": "reddit", "id": "rdt_test_b", "title": "B", "url": "u2"},
    }
    print("recording:", record_drop(fake)["id"])
    pprint.pp(stats())
    pprint.pp(recent_drops(limit=3))

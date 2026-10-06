"""Append-only JSON Lines audit log (planning.md Section 8).

The log is the single source of truth: current content state (status, appeal
links) is rebuilt by replaying it with replay_state() at startup.
"""

import hashlib
import json
import os
import threading
import uuid
from datetime import datetime, timezone

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit_log.jsonl")

_lock = threading.Lock()


def new_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def append_entry(entry, path=LOG_PATH):
    """Adds log_id/timestamp if missing, appends one line, returns the entry."""
    entry.setdefault("log_id", new_id("log"))
    entry.setdefault("timestamp", utc_now())
    line = json.dumps(entry, ensure_ascii=False)
    with _lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    return entry


def read_entries(path=LOG_PATH):
    """All entries, oldest first. Skips a corrupt line rather than failing the read."""
    if not os.path.exists(path):
        return []
    entries = []
    with _lock:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return entries


def replay_state(path=LOG_PATH):
    """Rebuilds {content_id: state} from classification and appeal events."""
    state = {}
    for e in read_entries(path):
        event = e.get("event_type")
        cid = e.get("content_id")
        if event == "classification" and cid:
            signals = e.get("signals", {})
            state[cid] = {
                "content_id": cid,
                "log_id": e["log_id"],
                "creator_id": e.get("creator_id"),
                "attribution": e.get("attribution"),
                "confidence_score": e.get("confidence_score"),
                "signal_scores": {
                    "stylometric": signals.get("stylometric", {}).get("score"),
                    "llm_judgment": signals.get("llm_judgment", {}).get("score"),
                },
                "status": e.get("status", "classified"),
                "appeal_id": None,
                "appeal_reasoning": None,
            }
        elif event == "appeal_submitted" and cid in state:
            state[cid].update(status=e["new_status"], appeal_id=e["appeal_id"],
                              appeal_reasoning=e.get("appeal_reasoning"))
        elif event == "appeal_resolved" and cid in state:
            state[cid]["status"] = e["new_status"]
    return state

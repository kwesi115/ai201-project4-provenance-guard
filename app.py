"""Provenance Guard: AI content attribution API (see planning.md)."""

import threading
import time

from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import audit_log
from labels import get_label
from scoring import LIKELY_HUMAN, BothSignalsFailed, score_submission
from signals import is_non_prose, llm_judgment_score, looks_like_poem, stylometric_score, word_count

app = Flask(__name__)
app.json.sort_keys = False

limiter = Limiter(get_remote_address, app=app, default_limits=[], storage_uri="memory://",
                  headers_enabled=True)

# planning.md Section 6 validation limits
CONTENT_TYPES = {"poem", "story", "blog", "other"}
MIN_SUBMIT_WORDS = 10
MAX_TEXT_CHARS = 10_000
MAX_TITLE_CHARS = 200
APPEAL_REASONING_MIN = 20
APPEAL_REASONING_MAX = 2000
APPEAL_MAX_EVIDENCE = 5
EVIDENCE_TYPES = {"draft", "link", "note"}
LOG_DEFAULT_LIMIT = 50
LOG_MAX_LIMIT = 200
LOG_EVENT_TYPES = {"classification", "appeal_submitted", "appeal_resolved", "classification_failed"}

# Current content state, rebuilt from the audit log so it survives restarts.
STATE = audit_log.replay_state()
_state_lock = threading.Lock()


def error_response(status, error, message, **extra):
    return jsonify({"error": error, "message": message, **extra}), status


def client_ip_hash():
    return audit_log.sha256(request.remote_addr or "unknown")


# --- Rate limiting (planning.md Section 7) ---------------------------------

def creator_or_ip():
    """Per-key limit: creator_id from the JSON body when present, otherwise the IP."""
    body = request.get_json(silent=True)
    if isinstance(body, dict) and isinstance(body.get("creator_id"), str) and body["creator_id"].strip():
        return f"creator:{body['creator_id'].strip()}"
    return f"ip:{get_remote_address()}"


def global_key():
    return "global"


@app.errorhandler(429)
def rate_limited(exc):
    retry_after = 60
    current = getattr(limiter, "current_limit", None)
    if current is not None and getattr(current, "reset_at", None):
        retry_after = max(1, int(current.reset_at - time.time()))
    what = "submissions" if request.path == "/submit" else "requests"
    response, status = error_response(429, "rate_limited",
                                      f"Too many {what}. Try again in {retry_after} seconds.",
                                      retry_after=retry_after)
    response.headers["Retry-After"] = str(retry_after)
    return response, status


# --- GET / --------------------------------------------------------------------

@app.get("/")
def index():
    return jsonify({
        "service": "Provenance Guard",
        "status": "ok",
        "endpoints": {
            "POST /submit": "Submit text for attribution (JSON: text, creator_id, optional content_type, title)",
            "POST /appeal": "Appeal a label (JSON: content_id, creator_reasoning, optional creator_id, evidence)",
            "GET /log": "Audit log, newest first (query: content_id, event_type, limit, since)",
        },
    })


# --- POST /submit -----------------------------------------------------------

@app.post("/submit")
@limiter.limit("10/minute;100/day", key_func=creator_or_ip)
@limiter.limit("20/minute", key_func=get_remote_address)
@limiter.limit("25/minute", key_func=global_key)  # protects the Groq quota for everyone
def submit():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return error_response(400, "invalid_request", "Request body must be a JSON object")

    for field in ("text", "creator_id"):
        value = body.get(field)
        if not isinstance(value, str) or not value.strip():
            return error_response(400, "invalid_request", f"{field} is required and must be a non-empty string")
    text = body["text"]
    creator_id = body["creator_id"].strip()
    if len(creator_id) > 64:
        return error_response(400, "invalid_request", "creator_id must be 64 characters or fewer")

    content_type = body.get("content_type")
    if content_type is not None and content_type not in CONTENT_TYPES:
        return error_response(400, "invalid_request", f"content_type must be one of {sorted(CONTENT_TYPES)}")
    title = body.get("title")
    if title is not None and (not isinstance(title, str) or len(title) > MAX_TITLE_CHARS):
        return error_response(400, "invalid_request", f"title must be a string of {MAX_TITLE_CHARS} characters or fewer")

    if len(text) > MAX_TEXT_CHARS:
        return error_response(413, "too_large", "Text must be 10,000 characters or fewer")
    wc = word_count(text)
    if wc < MIN_SUBMIT_WORDS:
        return error_response(400, "invalid_request", "Please submit at least 10 words")

    # Edge-case checks (planning.md Section 5)
    flags = []
    if content_type == "poem":
        flags.append("poem_mode")
    elif content_type is None and looks_like_poem(text):
        content_type = "poem"
        flags += ["poem_mode", "poem_mode_auto"]
    content_type = content_type or "other"
    if is_non_prose(text):
        flags.append("non_prose")

    sig_a = stylometric_score(text, content_type)
    sig_b = llm_judgment_score(text, content_type)
    signals_log = {
        "stylometric": {"score": sig_a["score"], "features": sig_a["features"], "error": sig_a["error"]},
        "llm_judgment": {"score": sig_b["score"], "rationale": sig_b["rationale"], "model": sig_b["model"],
                         "latency_ms": sig_b["latency_ms"], "error": sig_b["error"]},
    }

    try:
        result = score_submission(sig_a["score"], sig_b["score"], wc, content_type, flags)
    except BothSignalsFailed:
        audit_log.append_entry({
            "event_type": "classification_failed",
            "creator_id": creator_id,
            "text_sha256": audit_log.sha256(text),
            "word_count": wc,
            "signals": signals_log,
            "client_ip_hash": client_ip_hash(),
        })
        return error_response(503, "classification_unavailable",
                              "We couldn't check this right now. Please try again shortly.")

    label = get_label(result["attribution"])
    content_id = audit_log.new_id("cnt")
    entry = audit_log.append_entry({
        "event_type": "classification",
        "content_id": content_id,
        "creator_id": creator_id,
        "content_type": content_type,
        "word_count": wc,
        "text_sha256": audit_log.sha256(text),
        "text_excerpt": text[:120],
        "signals": signals_log,
        "weights": result["weights"],
        "combined_raw": result["combined_raw"],
        "disagreement": result["disagreement"],
        "length_factor": result["length_factor"],
        "confidence_score": result["confidence_score"],
        "attribution": result["attribution"],
        "label_text": label["label_text"],
        "flags": result["flags"],
        "degraded": result["degraded"],
        "scoring_version": result["scoring_version"],
        "status": "classified",
        "appeal_filed": False,  # as of writing; GET /log adds the current value
        "client_ip_hash": client_ip_hash(),
    })

    with _state_lock:
        STATE[content_id] = {
            "content_id": content_id,
            "log_id": entry["log_id"],
            "creator_id": creator_id,
            "attribution": result["attribution"],
            "confidence_score": result["confidence_score"],
            "signal_scores": {"stylometric": sig_a["score"], "llm_judgment": sig_b["score"]},
            "status": "classified",
            "appeal_id": None,
            "appeal_reasoning": None,
        }

    def rounded(x):
        return None if x is None else round(x, 2)

    return jsonify({
        "content_id": content_id,
        "status": "classified",
        "attribution": result["attribution"],
        "confidence_score": rounded(result["confidence_score"]),
        "label": label["label"],
        "label_text": label["label_text"],
        "appealable": result["attribution"] != LIKELY_HUMAN,
        "signals": {
            "stylometric": {"score": rounded(sig_a["score"]), "error": sig_a["error"]},
            "llm_judgment": {"score": rounded(sig_b["score"]), "rationale": sig_b["rationale"],
                             "error": sig_b["error"]},
        },
        "flags": result["flags"],
        "degraded": result["degraded"],
        "word_count": wc,
        "scoring_version": result["scoring_version"],
        "created_at": entry["timestamp"],
    })


# --- POST /appeal (planning.md Section 4) ------------------------------------

def validate_evidence(evidence):
    if evidence is None:
        return []
    if not isinstance(evidence, list) or len(evidence) > APPEAL_MAX_EVIDENCE:
        raise ValueError(f"evidence must be a list of at most {APPEAL_MAX_EVIDENCE} items")
    cleaned = []
    for item in evidence:
        if not isinstance(item, dict) or item.get("type") not in EVIDENCE_TYPES:
            raise ValueError(f"each evidence item needs a type of {sorted(EVIDENCE_TYPES)}")
        description, url = item.get("description"), item.get("url")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("each evidence item needs a description")
        if url is not None and not isinstance(url, str):
            raise ValueError("evidence url must be a string or null")
        cleaned.append({"type": item["type"], "description": description.strip(), "url": url})
    return cleaned


@app.post("/appeal")
@limiter.limit("3/hour;10/day", key_func=creator_or_ip)
@limiter.limit("10/hour", key_func=get_remote_address)
def appeal():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return error_response(400, "invalid_request", "Request body must be a JSON object")

    content_id = body.get("content_id")
    if not isinstance(content_id, str) or not content_id.strip():
        return error_response(400, "invalid_request", "content_id is required and must be a non-empty string")
    content_id = content_id.strip()

    # creator_reasoning per the M5 brief; "reasoning" (planning.md's name) also accepted.
    reasoning = body.get("creator_reasoning", body.get("reasoning"))
    if not isinstance(reasoning, str) or not reasoning.strip():
        return error_response(400, "invalid_request", "creator_reasoning is required and must be a non-empty string")
    reasoning = reasoning.strip()
    if not APPEAL_REASONING_MIN <= len(reasoning) <= APPEAL_REASONING_MAX:
        return error_response(400, "invalid_request",
                              f"creator_reasoning must be {APPEAL_REASONING_MIN} to {APPEAL_REASONING_MAX} characters")

    creator_id = body.get("creator_id")
    if creator_id is not None and (not isinstance(creator_id, str) or not creator_id.strip()):
        return error_response(400, "invalid_request", "creator_id must be a non-empty string if provided")
    try:
        evidence = validate_evidence(body.get("evidence"))
    except ValueError as exc:
        return error_response(400, "invalid_request", str(exc))

    with _state_lock:
        content = STATE.get(content_id)
        if content is None:
            return error_response(404, "not_found", f"No submission found with content_id {content_id}")
        # Ownership is checked only when creator_id is sent (no accounts in this project).
        if creator_id is not None and creator_id.strip() != content["creator_id"]:
            return error_response(403, "forbidden", "Only the creator who submitted this content can appeal it")
        if content["status"] == "under_review":
            return error_response(409, "appeal_exists",
                                  "An appeal for this content is already under review. No new appeal was created.",
                                  appeal_id=content["appeal_id"])
        if content["status"] != "classified":
            return error_response(409, "appeal_closed", "This content's appeal has already been reviewed.")
        if content["attribution"] == LIKELY_HUMAN:
            return error_response(400, "not_appealable",
                                  "This piece is labeled likely human-written, so there is nothing to appeal.")

        appeal_id = audit_log.new_id("apl")
        entry = audit_log.append_entry({
            "event_type": "appeal_submitted",
            "appeal_id": appeal_id,
            "content_id": content_id,
            "original_log_id": content["log_id"],
            "creator_id": content["creator_id"],
            "original_attribution": content["attribution"],
            "original_confidence_score": content["confidence_score"],
            "original_signal_scores": content["signal_scores"],
            "appeal_reasoning": reasoning,
            "evidence": evidence,
            "previous_status": "classified",
            "new_status": "under_review",
            "status": "under_review",
            "reviewer_note": None,
            "decision": None,
            "client_ip_hash": client_ip_hash(),
        })
        content.update(status="under_review", appeal_id=appeal_id, appeal_reasoning=reasoning)

    return jsonify({
        "appeal_id": appeal_id,
        "content_id": content_id,
        "status": "under_review",
        "message": "Thanks. A person will review this label. The original result stays on record until the review is done.",
        "submitted_at": entry["timestamp"],
    }), 201


# --- GET /log (planning.md Section 6) ----------------------------------------

@app.get("/log")
@limiter.limit("60/minute", key_func=get_remote_address)
def get_log():
    # NOTE: a real system would require authentication/authorization here;
    # the log exposes creator IDs and decision details.
    try:
        limit = int(request.args.get("limit", LOG_DEFAULT_LIMIT))
    except ValueError:
        return error_response(400, "invalid_request", "limit must be an integer")
    if not 1 <= limit <= LOG_MAX_LIMIT:
        return error_response(400, "invalid_request", f"limit must be between 1 and {LOG_MAX_LIMIT}")
    event_type = request.args.get("event_type")
    if event_type is not None and event_type not in LOG_EVENT_TYPES:
        return error_response(400, "invalid_request", f"event_type must be one of {sorted(LOG_EVENT_TYPES)}")
    content_id = request.args.get("content_id")
    since = request.args.get("since")

    entries = audit_log.read_entries()
    if content_id:
        entries = [e for e in entries if e.get("content_id") == content_id]
    if event_type:
        entries = [e for e in entries if e.get("event_type") == event_type]
    if since:
        entries = [e for e in entries if e.get("timestamp", "") >= since]
    entries = list(reversed(entries))[:limit]

    # Stored entries are never modified; classification entries are annotated
    # with the content's current status so appeals are visible here.
    with _state_lock:
        for e in entries:
            if e.get("event_type") == "classification" and e.get("content_id") in STATE:
                current = STATE[e["content_id"]]
                e["current_status"] = current["status"]
                e["appeal_filed"] = current["appeal_id"] is not None
                e["appeal_id"] = current["appeal_id"]
                e["appeal_reasoning"] = current["appeal_reasoning"]

    return jsonify({"count": len(entries), "entries": entries})


if __name__ == "__main__":
    app.run(port=5000)

# Provenance Guard
DEMO:https://youtu.be/lP1PedfCuxI

## Overview

Provenance Guard is a Flask API for a writing platform. A creator submits a piece of writing (a poem, a story excerpt, a blog post) and the API estimates whether it was written by a person or with an AI tool. It returns a confidence score and a short plain-language label that readers and the author can see. If the author disagrees with the label, they can appeal, and a person reviews it. Every decision and every appeal goes into an audit log.

The system is built around one rule: calling a person's own writing "AI" does more harm than missing a piece of AI writing. So when the evidence is weak, short, or contradictory, the system says "Origin unclear" instead of guessing. The full design is in [`planning.md`](planning.md).

**Current status:** in the current build, no submission reaches the "Likely AI-assisted" label. The Groq model planning.md named was retired, and the replacement model never scores text above about 0.60. See [Known limitations](#known-limitations).

## Setup and running

Requires Python 3.10 or newer (developed on 3.13) and a free Groq API key from https://console.groq.com.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
```

Create a `.env` file in the repo root (it is gitignored):

```
GROQ_API_KEY=your_key_here
```

Start the server:

```bash
python app.py
```

It listens on `http://localhost:5000`. The audit log is written to `audit_log.jsonl` in the repo root and is replayed on startup, so content status survives restarts.

Example requests:

```bash
# Submit a piece of writing
curl -s -X POST http://localhost:5000/submit -H "Content-Type: application/json" \
  -d '{"text": "The sun dipped below the horizon, painting the sky in hues of amber and rose. I sat on the porch, coffee in hand, watching the neighborhood slowly go quiet.", "creator_id": "test-user-1"}' | python -m json.tool

# Appeal a label (use a content_id from a /submit response)
curl -s -X POST http://localhost:5000/appeal -H "Content-Type: application/json" \
  -d '{"content_id": "cnt_8361ccaf75ce", "creator_reasoning": "I wrote this myself from personal experience. I am a non-native English speaker and my writing style may appear more formal than typical."}' | python -m json.tool

# Read the audit log (newest first), optionally filtered
curl -s "http://localhost:5000/log?limit=5" | python -m json.tool
curl -s "http://localhost:5000/log?content_id=cnt_8361ccaf75ce" | python -m json.tool
```

Test scripts:

| Script | What it does | Calls Groq? |
|--------|--------------|-------------|
| `test_signal.py` | Runs Signal A (stylometric) alone on 4 samples and prints every feature | No |
| `test_scoring.py` | Checks `scoring.py` against the worked examples in planning.md, plus the failure and gate rules | No |
| `test_signals.py` | Runs both signals and the scoring on 10 inputs and prints comparison tables | Yes |

## Architecture

The diagram from planning.md:

```
                         ┌──────────────────────┐
  Client ───────────────▶│  Flask-Limiter       │──── 429 if over limit
  (creator / reviewer)   │  (per key/IP/global) │
                         └──────────┬───────────┘
              ┌─────────────────────┼─────────────────────────────┐
              ▼                     ▼                             ▼
      ┌──────────────┐      ┌──────────────┐               ┌──────────────┐
      │ POST /submit │      │ POST /appeal │               │  GET /log    │
      └──────┬───────┘      └──────┬───────┘               └──────┬───────┘
             │ validate,           │ validate, check              │ filter &
             │ edge-case checks    │ ownership + status           │ read
     ┌───────┴────────┐            ▼                              │
     ▼                ▼     ┌──────────────────┐                  │
┌───────────┐  ┌────────────┐│ Status update    │                  │
│ Signal A  │  │ Signal B   ││ classified →     │                  │
│ stylometr.│  │ Groq LLM   ││ under_review     │                  │
│ (local)   │  │ (8s timeout│└────────┬─────────┘                  │
└─────┬─────┘  └─────┬──────┘         │                            │
      └──────┬───────┘                │                            │
             ▼                        │                            │
   ┌───────────────────┐              │                            │
   │ Score combiner    │              │                            │
   │ weights, disagree-│              │                            │
   │ ment + length     │              │                            │
   │ shrink, gates     │              │                            │
   └─────────┬─────────┘              │                            │
             ▼                        │                            │
   ┌───────────────────┐              │                            │
   │ Label generator   │              │                            │
   │ (3 fixed texts)   │              │                            │
   └─────────┬─────────┘              │                            │
             ▼                        ▼                            │
   ┌─────────────────────────────────────────────┐                 │
   │        Audit log  (audit_log.jsonl)         │◀────────────────┘
   │  append-only, replayed into state at start  │
   └─────────┬──────────────────────┬────────────┘
             ▼                      ▼
     JSON response           ┌──────────────────┐
     (score + label)         │  Review queue    │◀── GET /appeals
                             │  (open appeals)  │    POST /appeals/<id>/resolve
                             └──────────────────┘
```

**Submitting.** A request first passes the rate limiter. It is then validated and checked for edge cases: word count, poem detection, and text that isn't prose. Signal A (`signals.stylometric_score`) runs locally, then Signal B (`signals.llm_judgment_score`) calls Groq with an 8 second timeout. `scoring.score_submission` combines the two, shrinks the result toward 0.5 for disagreement and short length, and applies the AI gates. `labels.get_label` picks one of three fixed texts, and the decision is appended to the audit log before the response goes back.

**Appealing.** The creator posts an appeal for a `content_id`. The app checks that the content exists, that it isn't already under review, and that it isn't labeled likely human. If a `creator_id` is sent, it must match the submitter. The status moves from `classified` to `under_review`, and an `appeal_submitted` entry is appended that links to the original classification by `original_log_id`. The review queue in the diagram is planned but not built yet (see [Spec reflection](#spec-reflection)), so for now open appeals are found through `GET /log`.

Modules:

| File | Responsibility |
|------|----------------|
| `app.py` | Flask routes, validation, rate limits, in-memory content state |
| `signals.py` | Signal A, Signal B, and the poem / non-prose detectors |
| `scoring.py` | Weights, calibration, thresholds, and category mapping (all constants at the top) |
| `labels.py` | The three label texts, imported thresholds |
| `audit_log.py` | JSON Lines append, read, and state replay |

## API reference

All request and response bodies are JSON. IDs are a prefix (`cnt_`, `apl_`, `log_`) plus 12 hex characters. Timestamps are UTC ISO 8601. Every example below is real output from this build.

### POST /submit

| Field | Required | Rules |
|-------|----------|-------|
| `text` | yes | at least 10 words, at most 10,000 characters |
| `creator_id` | yes | non-empty string, at most 64 characters |
| `content_type` | no | `poem`, `story`, `blog`, or `other` (default `other`). If it is omitted, poem mode can still turn on automatically. |
| `title` | no | string, at most 200 characters |

Request:

```json
{"text": "My grandmother kept a jar of buttons on the windowsill above her sink. Not for sewing. ...", "creator_id": "readme-demo", "content_type": "blog", "title": "long_clearly_human"}
```

Response `200`:

```json
{
    "content_id": "cnt_8fcb76c7466c",
    "status": "classified",
    "attribution": "likely_human",
    "confidence_score": 0.11,
    "label": "Likely human-written",
    "label_text": "Likely human-written. Our automated check found writing patterns that are typical of human writing. This is an estimate, not a guarantee.",
    "appealable": false,
    "signals": {
        "stylometric": {
            "score": 0.09,
            "error": null
        },
        "llm_judgment": {
            "score": 0.12,
            "rationale": "The narrative is detailed, personal, and contains nuanced memories and emotional context typical of human writing",
            "error": null
        }
    },
    "flags": [],
    "degraded": false,
    "word_count": 183,
    "scoring_version": "v1",
    "created_at": "2026-10-06T02:01:55Z"
}
```

`confidence_score` is the calibrated score described in [Confidence scoring](#confidence-scoring). `appealable` is false only for `likely_human`. `flags` can contain `short_text`, `poem_mode`, `poem_mode_auto`, `signal_disagreement`, `non_prose`, and `single_signal`. `degraded` is true when one signal failed and the result rests on the other alone.

Errors:

| Code | When | Real response |
|------|------|---------------|
| 400 | missing or empty field | `{"error":"invalid_request","message":"text is required and must be a non-empty string"}` |
| 400 | under 10 words | `{"error":"invalid_request","message":"Please submit at least 10 words"}` |
| 400 | bad `content_type` | `{"error":"invalid_request","message":"content_type must be one of ['blog', 'other', 'poem', 'story']"}` |
| 413 | over 10,000 characters | `{"error":"too_large","message":"Text must be 10,000 characters or fewer"}` |
| 429 | rate limit hit | `{"error":"rate_limited","message":"Too many submissions. Try again in 51 seconds.","retry_after":51}` |
| 503 | both signals failed | `{"error": "classification_unavailable", "message": "We couldn't check this right now. Please try again shortly."}` (from the code; not triggered in testing, because Signal A has not failed) |

### POST /appeal

| Field | Required | Rules |
|-------|----------|-------|
| `content_id` | yes | must exist |
| `creator_reasoning` | yes | 20 to 2,000 characters. `reasoning` is also accepted. |
| `creator_id` | no | if sent, must match the submitter |
| `evidence` | no | up to 5 items of `{"type": "draft" \| "link" \| "note", "description": "...", "url": "..." or null}` |

Request:

```json
{"content_id": "cnt_8361ccaf75ce", "creator_reasoning": "I wrote this myself from personal experience. I am a non-native English speaker and my writing style may appear more formal than typical."}
```

Response `201`:

```json
{
    "appeal_id": "apl_22137c3f9922",
    "content_id": "cnt_8361ccaf75ce",
    "status": "under_review",
    "message": "Thanks. A person will review this label. The original result stays on record until the review is done.",
    "submitted_at": "2026-10-06T01:58:08Z"
}
```

Errors:

| Code | When | Real response |
|------|------|---------------|
| 400 | missing reasoning | `{"error":"invalid_request","message":"creator_reasoning is required and must be a non-empty string"}` |
| 400 | content is `likely_human` | `{"error":"not_appealable","message":"This piece is labeled likely human-written, so there is nothing to appeal."}` |
| 403 | `creator_id` doesn't match | `{"error":"forbidden","message":"Only the creator who submitted this content can appeal it"}` |
| 404 | unknown `content_id` | `{"error":"not_found","message":"No submission found with content_id cnt_doesnotexist"}` |
| 409 | appeal already open | `{"error":"appeal_exists","message":"An appeal for this content is already under review. No new appeal was created.","appeal_id":"apl_22137c3f9922"}` |
| 429 | rate limit hit | same shape as `/submit`, with "Too many requests" |

### GET /log

Query parameters, all optional: `content_id`, `event_type` (`classification`, `appeal_submitted`, `appeal_resolved`, `classification_failed`), `limit` (default 50, max 200), `since` (ISO timestamp). Returns `{"count": n, "entries": [...]}`, newest first. A bad `limit` or `event_type` returns 400. See [Audit log](#audit-log) for a real response.

This endpoint has no authentication. A real deployment would need it, because the log exposes creator IDs and decision details.

## Detection signals

Both signals return a float from 0 to 1, where 1 means "looks AI-generated" and 0 means "looks human-written". A signal that fails returns `score: null` and an `error` string instead of a guessed number.

### Signal A: stylometric heuristics (`stylometric_score`)

**What it measures:** five surface statistics of the writing, each mapped to a 0 to 1 AI-score and averaged with weights:

| Feature | What is counted | Maps to AI-score | Prose weight | Poem weight |
|---------|-----------------|------------------|--------------|-------------|
| Burstiness | spread of sentence lengths (std / mean) | 0.25 or less = 1.0, 0.60 or more = 0.0 | 0.35 | 0.50 |
| Lexical uniformity | std of type-token ratio across 50-word windows (100+ words only) | 0.03 or less = 1.0, 0.08 or more = 0.0 | 0.20 | 0 |
| Stock phrases | hits per 100 words from a fixed list ("delve", "tapestry", "it's important to note", "in conclusion", ...) | 1+ per 100 words = 1.0 | 0.20 | 0.30 |
| Repetition | share of word trigrams that appear more than once | 0.15 or more = 1.0 | 0.10 | 0 |
| Contractions | contractions per 100 words | 0 = 1.0, 2 or more = 0.0 | 0.15 | 0.20 |

If a feature can't be computed (for example, lexical uniformity on a text under 100 words), it is skipped and the remaining weights are rescaled. Poem mode splits on lines instead of sentences and turns off the two features that poetry trips by design (repetition and vocabulary steadiness).

**Why I chose it:** it is free, instant, and deterministic, and every number can be shown to a reviewer. Most importantly, it fails in different ways than an LLM does, so it works as an independent check.

**Output:** `{"signal": "stylometric", "score": 0.85, "features": {...}, "error": null}`

**Known weaknesses:** formal writing, non-native English, and short casual writing all look "uniform" to it (see [Known limitations](#known-limitations)). It is also easy to game: an adversary can add a few short sentences and contractions to lower the score.

### Signal B: LLM judgment via Groq (`llm_judgment_score`)

**What it measures:** an overall judgment of whether the text reads as AI-generated. It picks up things the counts can't, like generic content, no concrete personal detail, and formulaic structure.

**Why I chose it:** it reads for meaning, not just counts, and it explains itself in one sentence that a reviewer can check. Groq is fast (300 to 1,400 ms per call in testing) and free at this volume.

**How it is called:** model `openai/gpt-oss-120b` with `reasoning_effort: "low"`, temperature 0, JSON mode, a maximum of 150 output tokens, and input cut to 6,000 characters. The timeout is 8 seconds, with one retry only on HTTP 429 or 5xx, inside a 10 second total budget. The system prompt tells the model to be cautious, to stay near 0.5 when evidence is weak, and to ignore any instructions inside the `<submission>` tags.

**Output:** `{"signal": "llm_judgment", "score": 0.6, "rationale": "...", "model": "openai/gpt-oss-120b", "latency_ms": 599, "error": null}`. The response is parsed defensively. Invalid JSON, a missing `ai_probability`, a non-number, or a boolean are all treated as failures (checked in testing, along with a bad API key giving `http_401` and a tiny timeout giving `timeout`). Out-of-range values are clamped.

**Known weaknesses:** in this build the model never goes above about 0.60, even on obviously generic AI text. Earlier LLM detectors are also known to over-flag non-native English. Its results can drift between runs: one human sample scored 0.15, then 0.12. And prompt injection is reduced by the delimiters but not ruled out.

### Why two signals beat one

Results from `test_signals.py` ("agree" means both scores are on the same side of 0.5):

| Input | Words | Signal A | Signal B | Difference | Agree |
|-------|------:|---------:|---------:|-----------:|:-----:|
| clearly_ai | 43 | 0.71 | 0.60 | 0.11 | yes |
| clearly_human | 55 | 0.02 | 0.15 | 0.13 | yes |
| borderline_formal_human | 43 | 0.62 | 0.20 | 0.42 | NO |
| borderline_edited_ai | 39 | 0.27 | 0.20 | 0.07 | yes |
| long_clearly_ai | 169 | 0.85 | 0.60 | 0.25 | yes |
| long_clearly_human | 183 | 0.09 | 0.15 | 0.06 | yes |
| m3_casual_human | 52 | 0.41 | 0.12 | 0.29 | yes |
| m3_generic_ai | 47 | 0.69 | 0.60 | 0.09 | yes |
| m3_very_short | 5 | 0.62 | 0.20 | 0.42 | NO |
| m3_poem | 39 | 0.50 | 0.30 | 0.20 | NO |

The three disagreements are all cases where Signal A is fooled by form and Signal B reads the content:

- **The formal economics paragraph** gets 0.62 from Signal A. It has even sentence lengths and no contractions, which is exactly what Signal A treats as AI-like. Signal B sees specific domain content and gives it 0.20.
- **The 5-word comment** ("Loved this. Will read again.") gets 0.62 from Signal A only because two equal-length sentences look perfectly "uniform".
- **The poem** sits at 0.50 on Signal A because its equal-length lines max out burstiness.

In each case, using Signal A alone would push human writing toward the AI side. Signal B pulls it back.

It works the other way too. Signal B gives every AI sample exactly 0.60, so on its own it can't tell a 43-word AI paragraph from a 169-word AI blog post. Signal A does separate them (0.71 vs 0.85), and that keeps the long AI post at 0.68 instead of letting it drift into the low end of the uncertain band.

The scoring also uses the gap between the two signals: the bigger the disagreement, the more the score is pulled toward 0.5.

## Confidence scoring

All of this is in `scoring.py`, with every constant named at the top of the file and a comment pointing to the planning.md section it came from.

**Combining.** `combined = 0.40 * A + 0.60 * B` for prose, and `0.25 * A + 0.75 * B` for poems. Signal B gets more weight because it judges content and Signal A only measures form. Signal A keeps enough weight that one overconfident LLM call can't decide the result alone. Poems shift weight further toward Signal B, because short repeated lines are part of the form and Signal A misreads them.

**Calibrating.** The combined score is pulled toward 0.5 when the evidence is weaker:

1. Disagreement: `g = 1 - 0.5 * |A - B|`. Two fully opposite signals halve the distance from 0.5.
2. Length: `L = 0.4` under 50 words, `0.6` for 50 to 149 words, `1.0` for 150 or more.
3. `score = 0.5 + (combined - 0.5) * g * L`, then clamped to 0.03 to 0.97 so the system never claims certainty.

If one signal fails, the other is used alone with an extra halving: `score = 0.5 + (single - 0.5) * 0.5 * L`. The result is flagged `single_signal` and can never be labeled AI. If both fail, the API returns 503 and no label.

**Thresholds** (both boundaries inclusive):

| Category | Condition |
|----------|-----------|
| `likely_ai` | score at least 0.80, **and** both signals at least 0.60, **and** at least 150 words, **and** both signals worked |
| `likely_human` | score at most 0.35, and at least 50 words |
| `uncertain` | everything else, including anything under 50 words or flagged `non_prose` |

**Why the AI threshold is stricter.** The AI band starts 0.30 above neutral. The human band starts only 0.15 below it. On top of that, "AI" also needs both signals to agree on their own, a long enough text, and no failed signal. A wrong "AI" label can cost a writer their reputation or standing on the platform, while a wrong "human" label lets one AI piece through unflagged. Those harms aren't equal, so the evidence required isn't either.

### Two real examples

Both were submitted to `/submit` with `content_type: "blog"`.

**Higher confidence: `long_clearly_human` (183 words).**

> My grandmother kept a jar of buttons on the windowsill above her sink. Not for sewing. She hadn't sewn anything since 1987, she told me once, when she gave up on a pair of curtains and bought some at Sears instead. The buttons were just there. [...] It's on my windowsill now. I haven't sorted them yet.

| Signal A | Signal B | Combined confidence | Category | Label |
|---------:|---------:|--------------------:|----------|-------|
| 0.09 | 0.12 | **0.11** | `likely_human` | Likely human-written |

Both signals agree strongly. Signal A sees very uneven sentence lengths (burstiness CV 0.72) and frequent contractions (5.5 per 100 words), and Signal B notes "detailed, personal, and contains nuanced memories". The text is over 150 words, so there's no length discount. On an earlier run, Signal B gave 0.15 and the confidence was 0.14, which is still well inside the human band.

**Lower confidence: `long_clearly_ai` (169 words).**

> In today's fast-paced world, productivity has become more important than ever. Whether you are a student, a professional, or an entrepreneur, managing your time effectively can make a significant difference in achieving your goals. It is important to note that productivity is not about working harder, but about working smarter. [...] By implementing these strategies consistently, you can unlock your full potential and achieve lasting success.

| Signal A | Signal B | Combined confidence | Category | Label |
|---------:|---------:|--------------------:|----------|-------|
| 0.85 | 0.60 | **0.68** | `uncertain` | Origin unclear |

This is a deliberately generic AI-style post, and Signal A scores it high: stock phrases, uniform sentences, few contractions. Signal B only gives it 0.60 ("characteristics common in AI-generated content, though it could also be human-written"). That gives `combined = 0.40 * 0.85 + 0.60 * 0.60 = 0.70`, and the 0.25 disagreement shrinks it to 0.68. It's the highest score any test input reached, but it is below 0.80, so the label is "Origin unclear". This is a false negative. The design accepts those when evidence is short of strong, but here it happens because of the Signal B ceiling, not because the evidence was truly weak.

The full `/submit` response for this example:

```json
{
    "content_id": "cnt_f06f8b2e0fad",
    "status": "classified",
    "attribution": "uncertain",
    "confidence_score": 0.68,
    "label": "Origin unclear",
    "label_text": "Origin unclear. Our automated check couldn't tell whether this piece was written by a person, with AI help, or a mix of both. This is common for short pieces, poetry, and personal writing styles, and it does not suggest anything was done wrong. If you are the author, you can ask for a person to review it.",
    "appealable": true,
    "signals": {
        "stylometric": {
            "score": 0.85,
            "error": null
        },
        "llm_judgment": {
            "score": 0.6,
            "rationale": "The text is a well-structured, generic blog post with smooth flow and typical productivity advice, characteristics common in AI\u2011generated content, though it could also be human\u2011written.",
            "error": null
        }
    },
    "flags": [],
    "degraded": false,
    "word_count": 169,
    "scoring_version": "v1",
    "created_at": "2026-10-06T02:01:54Z"
}
```

### How I tested whether scores mean anything

`test_scoring.py` checks the math against the worked examples in planning.md (every one reproduces within 0.01), plus the gate rules: 49 words is always uncertain, 149 words is never AI, one failed signal is never AI, and both failed raises the 503 path. All 15 checks pass.

`test_signals.py` runs the real signals. The four required test inputs:

| Input | Words | Signal A | Signal B | Confidence | Category | Expected | Met? |
|-------|------:|---------:|---------:|-----------:|----------|----------|:----:|
| clearly_ai | 43 | 0.71 | 0.60 | 0.55 | uncertain | likely AI | no |
| clearly_human | 55 | 0.02 | 0.15 | 0.27 | likely_human | likely human | yes |
| borderline_formal_human | 43 | 0.62 | 0.20 | 0.46 | uncertain | uncertain | yes |
| borderline_edited_ai | 39 | 0.27 | 0.20 | 0.40 | uncertain | uncertain | yes |

Three of the four inputs are under 50 words, so the short-text rule forces them to uncertain whatever the signals say. That's why I added two 150+ word samples (in the examples above): to test whether the categories can be reached at all. The human one reaches `likely_human`. The AI one doesn't, for the Signal B reason above.

The ordering still means something even when the category doesn't change. Across the 10 inputs, the AI-style texts score 0.55 to 0.68, the human texts score 0.11 to 0.36, and the borderline and short texts sit in between at 0.40 to 0.46. No human text came close to the AI band.

### What I'd change for a real deployment

- **Fix Signal B's ceiling first.** That means rewording the prompt so strong evidence can use the full range, or switching models. Then rerun the tests before touching any thresholds.
- **Calibrate on a real labeled set.** That means hundreds of pieces per genre, including non-native writers and poetry, and fitting the thresholds to measured false-positive rates instead of hand-picked numbers.
- **Pin and log the model version, and recheck scores when it changes.** The original model disappeared during this project.
- **Track how often reviewers overturn the label, by content type.** A high overturn rate on poems would mean poem mode needs work.

## Transparency labels

The exact text from `labels.py`. Score ranges come from the thresholds imported from `scoring.py`. "AI gates" means: both signals score at least 0.60, the text is at least 150 words, and neither signal failed.

| Variant | Score range | Label text |
|---------|-------------|------------|
| High-confidence AI (`likely_ai`) | s >= 0.80 and all AI gates pass | Likely AI-assisted. Our automated check found writing patterns that are common in AI-generated text. This is an estimate, not a final decision, and automated checks can be wrong. If you are the author and wrote this yourself, you can ask for a person to review it. |
| Uncertain (`uncertain`) | 0.35 < s < 0.80, any AI gate fails, or under 50 words | Origin unclear. Our automated check couldn't tell whether this piece was written by a person, with AI help, or a mix of both. This is common for short pieces, poetry, and personal writing styles, and it does not suggest anything was done wrong. If you are the author, you can ask for a person to review it. |
| High-confidence human (`likely_human`) | s <= 0.35 and word_count >= 50 | Likely human-written. Our automated check found writing patterns that are typical of human writing. This is an estimate, not a guarantee. |

Defined for use while an appeal is open, though not shown anywhere yet (see [Spec reflection](#spec-reflection)): *"The author has asked for a review of this label."*

Design choices:

- **Plain words.** No "detected", "flagged", "probability", "algorithm", or "model". Each label opens with a short headline a reader can take in at a glance.
- **"AI-assisted", not "AI-generated".** The label describes the patterns the check found ("found writing patterns that are common in..."), not what the writer did. That keeps it from reading as an accusation.
- **The uncertain label says it "does not suggest anything was done wrong".** An earlier draft said "No conclusion has been drawn". I changed it because a writer could read that as "still under suspicion".
- **Every label says "estimate".** So nobody reads it as a verdict.
- **The two labels that can be appealed tell the author they can ask for a person to review it.** The human label doesn't, because there's nothing to dispute.

## Appeals workflow

**Who can appeal:** the creator who submitted the piece. There are no accounts in this project, so the appeal is accepted from anyone with the `content_id`. If a `creator_id` is included, it must match the submitter, or the request gets a 403. Only `likely_ai` and `uncertain` results can be appealed, and only one appeal per piece can be open.

**What they submit:** `content_id`, `creator_reasoning` (20 to 2,000 characters), and optionally up to 5 `evidence` items such as drafts, version-history links, or notes on how they wrote it.

**What happens:**

1. The content status changes from `classified` to `under_review`.
2. The original classification entry is never changed.
3. An `appeal_submitted` entry is appended. It records `original_log_id` (the classification it disputes), the original category, the confidence and both signal scores, the reasoning and evidence, and `previous_status` / `new_status`.
4. `GET /log` then shows the classification entry with `current_status: "under_review"`, `appeal_filed: true`, and the reasoning attached, so the whole chain can be found by `content_id`.

There is no automatic re-classification. A person decides.

**What a reviewer would see** (from planning.md; the queue endpoint itself isn't built yet):

- The appeal and its age, plus the creator, content type, and title
- The full text and word count
- The original category, score, and label
- Each signal's score with all Signal A features and Signal B's rationale
- Which flags fired
- The scoring version and model
- The creator's reasoning and evidence, and how many earlier appeals they've filed

Real appeal request and response: see [POST /appeal](#post-appeal). The resulting log entries are in the [Audit log](#audit-log) section.

## Rate limiting

Implemented with Flask-Limiter (in-memory storage). Limits are from planning.md Section 7:

| Endpoint | Per creator (`creator_id`, or IP if missing) | Per IP | Global |
|----------|---------------------------------------------|--------|--------|
| `POST /submit` | 10/minute, 100/day | 20/minute | 25/minute |
| `POST /appeal` | 3/hour, 10/day | 10/hour | none |
| `GET /log` | n/a | 60/minute | none |

Reasoning:

- **Real creators** publish a few pieces a day. Even someone tweaking a draft and resubmitting stays under 10 a minute, and 100 a day is far above any real posting habit but blocks scripted bulk use.
- **Flooding:** every `/submit` makes a Groq API call. The global 25/minute cap keeps one client from using up the app's Groq quota and pushing every user into single-signal (degraded) mode.
- **Probing the detector:** someone resubmitting small edits to find what gets past the check is slowed to 10 tries a minute and 100 a day.
- **Appeals** take a human reviewer's time, so they're the scarcest resource. 3 an hour is plenty for real disputes, and only one open appeal per piece is allowed.
- `GET /log` is cheap and read-only, but it's still capped to prevent scraping.
- The per-IP limit always applies too, so rotating fake `creator_id`s doesn't get around the per-creator limit.

Requests that fail validation still count toward the limits. A 429 response is JSON with a `Retry-After` header:

```json
{"error": "rate_limited", "message": "Too many submissions. Try again in 51 seconds.", "retry_after": 51}
```

### Rate limit evidence

12 requests to `POST /submit` from the same creator within one minute, right after a server restart:

```
$ for i in $(seq 1 12); do curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:5000/submit -H "Content-Type: application/json" -d '{"text": "This is a test submission for rate limit testing purposes only.", "creator_id": "ratelimit-test"}'; done
200
200
200
200
200
200
200
200
200
200
429
429
```

## Audit log

**Storage:** `audit_log.jsonl`, one JSON object per line, append-only. Writes go through a lock so lines can't interleave. On startup, `audit_log.replay_state()` rebuilds each piece's current status from the log, so the log is the only source of truth. The log keeps a SHA-256 hash and the first 120 characters of the text, not the full text. Client IPs are stored only as hashes.

**Classification entry** (`event_type: "classification"`):

| Field | Type | Notes |
|-------|------|-------|
| `log_id`, `timestamp`, `event_type` | string | |
| `content_id`, `creator_id`, `content_type` | string | |
| `word_count` | int | |
| `text_sha256`, `text_excerpt` | string | |
| `signals.stylometric` | object | `score` (float or null), `features`, `error` |
| `signals.llm_judgment` | object | `score`, `rationale`, `model`, `latency_ms`, `error` |
| `weights` | object | weights used for this content type |
| `combined_raw`, `disagreement`, `length_factor`, `confidence_score` | float | each step of the calibration; `disagreement` is null if one signal failed |
| `attribution`, `label_text` | string | |
| `flags` | string[] | |
| `degraded` | bool | |
| `scoring_version`, `status` | string | `status` is `"classified"` when written |
| `appeal_filed` | bool | `false` when written |
| `client_ip_hash` | string | |

When the log is read, `GET /log` adds `current_status`, `appeal_filed`, `appeal_id`, and `appeal_reasoning` to classification entries. These reflect the current state, and nothing stored on disk changes.

**Appeal entry** (`event_type: "appeal_submitted"`): `log_id`, `timestamp`, `appeal_id`, `content_id`, `original_log_id`, `creator_id`, `original_attribution`, `original_confidence_score`, `original_signal_scores`, `appeal_reasoning`, `evidence`, `previous_status`, `new_status`, `status`, `reviewer_note` (null), `decision` (null), `client_ip_hash`.

**Failure entry** (`event_type: "classification_failed"`): `creator_id`, `text_sha256`, `word_count`, both signals' errors, and `client_ip_hash`. No `content_id` is issued.

### Sample

Real output from `GET /log`: 3 of the 17 entries at the time, newest first. It shows an appeal, the classification it links to through `original_log_id` (note `current_status: "under_review"` and the filled-in `appeal_reasoning`), and an unrelated classification.

```json
{
  "count": 3,
  "entries": [
    {
      "event_type": "appeal_submitted",
      "appeal_id": "apl_22137c3f9922",
      "content_id": "cnt_8361ccaf75ce",
      "original_log_id": "log_adb3c3109b1f",
      "creator_id": "m5-tester",
      "original_attribution": "uncertain",
      "original_confidence_score": 0.4579411235803137,
      "original_signal_scores": {
        "stylometric": 0.6177325581395349,
        "llm_judgment": 0.2
      },
      "appeal_reasoning": "I wrote this myself from personal experience. I am a non-native English speaker and my writing style may appear more formal than typical.",
      "evidence": [],
      "previous_status": "classified",
      "new_status": "under_review",
      "status": "under_review",
      "reviewer_note": null,
      "decision": null,
      "client_ip_hash": "12ca17b49af2289436f303e0166030a21e525d266e209267433801a8fd4071a0",
      "log_id": "log_fd0849a06e4b",
      "timestamp": "2026-10-06T01:58:08Z"
    },
    {
      "event_type": "classification",
      "content_id": "cnt_d20c9c022e02",
      "creator_id": "m5-tester",
      "content_type": "blog",
      "word_count": 183,
      "text_sha256": "94460c65e23dd6556ab5e9367f8a4728794d71242965644cc026111be2c0e764",
      "text_excerpt": "My grandmother kept a jar of buttons on the windowsill above her sink. Not for sewing. She hadn't sewn anything since 19",
      "signals": {
        "stylometric": {
          "score": 0.0937258300203048,
          "features": {
            "burstiness_cv": 0.7236,
            "lexical_uniformity_std": 0.0566,
            "stock_phrase_rate": 0.0,
            "repetition_rate": 0.0,
            "contraction_rate": 5.4645,
            "burstiness_score": 0.0,
            "lexical_uniformity_score": 0.4686,
            "stock_phrase_score": 0.0,
            "repetition_score": 0.0,
            "contraction_score": 0.0
          },
          "error": null
        },
        "llm_judgment": {
          "score": 0.15,
          "rationale": "The detailed, personal narrative with specific memories and emotional nuance suggests human authorship.",
          "model": "openai/gpt-oss-120b",
          "latency_ms": 507,
          "error": null
        }
      },
      "weights": {
        "stylometric": 0.4,
        "llm_judgment": 0.6
      },
      "combined_raw": 0.12749033200812193,
      "disagreement": 0.0562741699796952,
      "length_factor": 1.0,
      "confidence_score": 0.1379716681959493,
      "attribution": "likely_human",
      "label_text": "Likely human-written. Our automated check found writing patterns that are typical of human writing. This is an estimate, not a guarantee.",
      "flags": [],
      "degraded": false,
      "scoring_version": "v1",
      "status": "classified",
      "appeal_filed": false,
      "client_ip_hash": "12ca17b49af2289436f303e0166030a21e525d266e209267433801a8fd4071a0",
      "log_id": "log_b29d15ea1275",
      "timestamp": "2026-10-06T01:57:59Z",
      "current_status": "classified",
      "appeal_id": null,
      "appeal_reasoning": null
    },
    {
      "event_type": "classification",
      "content_id": "cnt_8361ccaf75ce",
      "creator_id": "m5-tester",
      "content_type": "other",
      "word_count": 43,
      "text_sha256": "00d78ebfc0168a3dcbcd2a34e2fed3feb9c17dc185264e66ed8184492340ad59",
      "text_excerpt": "The relationship between monetary policy and asset price inflation has been extensively studied in the literature. Centr",
      "signals": {
        "stylometric": {
          "score": 0.6177325581395349,
          "features": {
            "burstiness_cv": 0.2558,
            "stock_phrase_rate": 0.0,
            "repetition_rate": 0.0,
            "contraction_rate": 0.0,
            "burstiness_score": 0.9834,
            "stock_phrase_score": 0.0,
            "repetition_score": 0.0,
            "contraction_score": 1.0
          },
          "error": null
        },
        "llm_judgment": {
          "score": 0.2,
          "rationale": "The formal academic tone and specific economic content suggest a human author, with only a modest chance of AI generation.",
          "model": "openai/gpt-oss-120b",
          "latency_ms": 360,
          "error": null
        }
      },
      "weights": {
        "stylometric": 0.4,
        "llm_judgment": 0.6
      },
      "combined_raw": 0.36709302325581394,
      "disagreement": 0.41773255813953486,
      "length_factor": 0.4,
      "confidence_score": 0.4579411235803137,
      "attribution": "uncertain",
      "label_text": "Origin unclear. Our automated check couldn't tell whether this piece was written by a person, with AI help, or a mix of both. This is common for short pieces, poetry, and personal writing styles, and it does not suggest anything was done wrong. If you are the author, you can ask for a person to review it.",
      "flags": [
        "short_text"
      ],
      "degraded": false,
      "scoring_version": "v1",
      "status": "classified",
      "appeal_filed": true,
      "client_ip_hash": "12ca17b49af2289436f303e0166030a21e525d266e209267433801a8fd4071a0",
      "log_id": "log_adb3c3109b1f",
      "timestamp": "2026-10-06T01:57:52Z",
      "current_status": "under_review",
      "appeal_id": "apl_22137c3f9922",
      "appeal_reasoning": "I wrote this myself from personal experience. I am a non-native English speaker and my writing style may appear more formal than typical."
    }
  ]
}
```

## Known limitations

1. **Obvious AI writing is labeled "Origin unclear", never "Likely AI-assisted".**
   - planning.md specified `llama-3.3-70b-versatile`, which Groq retired during the project. With the spec's cautious prompt, its replacement `gpt-oss-120b` returned exactly 0.60 or 0.62 for every AI sample across repeated runs.
   - `likely_ai` needs a combined score of 0.80. With B stuck at 0.60, even a perfect A = 1.0 gives at most `0.4 + 0.36 = 0.76` before any shrinking.
   - In the same test, `qwen/qwen3.8-27b` with the same prompt gave the long AI sample 0.95, so this is specific to the model. In practice the system currently has two categories: human and unclear.

2. **Formal writing and non-native English look AI-like to Signal A.**
   - Burstiness and contractions both reward varied, casual writing. The economics paragraph got a burstiness score of 0.98 and a contraction score of 1.0, so Signal A gave it 0.62 even though it was human-written.
   - It stayed "uncertain" only because Signal B disagreed and the text was short.
   - A long formal essay by a careful writer could reach the AI band if Signal B also leaned AI. Then the only remaining safeguard is the rule that both signals must be at least 0.60.

3. **Short casual human writing is read as uniform.**
   - Burstiness needs a few sentences of different lengths to mean anything. The 52-word casual faucet story had a burstiness score of 0.94 (CV 0.27), and the 5-word "Loved this. Will read again." scored 0.62 overall.
   - The length rules keep these from being labeled AI, but they also mean short pieces can almost never get a confident result either way.

4. **Repetitive poems confuse Signal A even in poem mode.**
   - Poem mode turns off repetition and vocabulary steadiness, but it still measures burstiness on lines. A poem with equal-length lines scores 1.0 there, so the refrain poem sat at 0.50.
   - Auto-detection only kicks in when `content_type` is omitted, at least 3 lines, and 60% or more of them under 8 words. A poem with long lines submitted as `other` gets the prose rules.

5. **Lightly edited AI text passes as human.**
   - `borderline_edited_ai` got 0.27 and 0.20 from the two signals. Its contractions ("I've") and a mixed sentence rhythm fooled Signal A, and the personal framing fooled Signal B.
   - It was only "uncertain" because it was 39 words long. A longer version would likely be labeled `likely_human`.
   - The design accepts this kind of false negative, but it means the system can't catch AI drafts that a person has touched up.

6. **Smaller gaps:**
   - `'s` is counted as a contraction, so possessives ("today's") lower Signal A's AI-score.
   - Text that isn't in English isn't detected.
   - The stock-phrase list is short and fixed.
   - Scores can drift a few hundredths between runs even at temperature 0.

## Spec reflection

**Where planning.md guided the build.** The scoring section was written as a recipe: exact weights, a 6-step calibration, inclusive thresholds, and a table of worked examples. As a result:

- `scoring.py` is close to a line-by-line translation of it.
- `test_scoring.py` uses the worked-examples table as its test cases.
- The single-signal rule ("one signal is never enough to label something AI") was decided before any code existed, so the failure path never had to be invented under deadline.

[KWESI: add your own example of where the plan helped, if you have a different one.]

**Where the build differs from planning.md** (found by comparing the two):

| planning.md | Built | Why |
|-------------|-------|-----|
| Groq model `llama-3.3-70b-versatile` | `openai/gpt-oss-120b` with `reasoning_effort: "low"` | The original model returns 404 `model_not_found`. gpt-oss-120b was the largest general model left on the account. |
| Clearly AI samples should reach `likely_ai` (Section 2 acceptance criteria) | None do | The Signal B ceiling described in Known limitations. Not fixed in this build. |
| Appeal field `reasoning`, `creator_id` required | `creator_reasoning` (with `reasoning` accepted too), `creator_id` optional with ownership checked only when sent | The Milestone 5 test command sent `creator_reasoning` and no `creator_id`. |
| Append-only log, status read from `GET /content/<id>` | Log is still append-only, but `GET /log` adds `current_status`, `appeal_filed`, and `appeal_reasoning` to classification entries when the log is read | The milestone required the log itself to show `under_review` after an appeal, without rewriting stored entries. |
| `GET /content/<id>`, `GET /appeals` review queue, `POST /appeals/<id>/resolve`, `submissions.jsonl` full-text store, under-review label suffix shown to readers | Not built (the suffix text exists in `labels.py` but nothing displays it) | Outside the Milestone 5 scope. |
| 15-sample test set | 10 inputs | 4 were given in the Milestone 4 brief, 4 were written to the Milestone 3 brief, and 2 long ones were added so the categories could be reached. |
| `signal_disagreement` flag (no threshold given) | Fires at a gap of 0.5 or more | planning.md didn't give a number. |

[KWESI: in your own words, pick one of these divergences (the model swap is the most significant) and explain why it happened and what you'd do differently in the plan next time.]

## AI usage

I used an AI coding assistant (Claude Code) through each milestone, giving it the relevant planning.md sections as context. [KWESI: confirm this, and add any other AI tools you used.]

**Instance 1: [KWESI: short title, e.g. "Writing planning.md"]**
- What I directed it to do: [KWESI: ...]
- What it produced: [KWESI: ...]
- What I revised or overrode: [KWESI: ...]

**Instance 2: [KWESI: short title, e.g. "Handling the retired Groq model"]**
- What I directed it to do: [KWESI: ...]
- What it produced: [KWESI: ...]
- What I revised or overrode: [KWESI: ...]

[KWESI: add a third instance if you have one.]

"""Confidence scoring: combine both signals into a calibrated score and category.

Every constant below comes from planning.md; the comment says which section.
"""

SCORING_VERSION = "v1"  # Section 2, "Thresholds" (logged with every classification)

# Section 1, "Combination Method"
WEIGHTS_PROSE = {"stylometric": 0.40, "llm_judgment": 0.60}
WEIGHTS_POEM = {"stylometric": 0.25, "llm_judgment": 0.75}

# Section 2, step 2: g = 1 - DISAGREEMENT_PENALTY * |a - b|
DISAGREEMENT_PENALTY = 0.5

# Section 2, step 3: length factor L
MIN_WORDS_FOR_LABEL = 50     # word_count < 50 -> forced uncertain
MIN_WORDS_FULL_LENGTH = 150  # word_count >= 150 -> L = 1.0
LENGTH_FACTOR_UNDER_50 = 0.4
LENGTH_FACTOR_50_TO_149 = 0.6
LENGTH_FACTOR_FULL = 1.0

# Section 1, "Failure and timeout handling": extra shrink when one signal fails
SINGLE_SIGNAL_SHRINK = 0.5

# Section 2, step 5: never claim certainty
SCORE_FLOOR = 0.03
SCORE_CEILING = 0.97

# Section 2, "Thresholds" (both inclusive)
AI_THRESHOLD = 0.80          # likely_ai needs s >= 0.80 ...
AI_GATE_PER_SIGNAL = 0.60    # ... and a >= 0.60 and b >= 0.60 ...
                             # ... and word_count >= MIN_WORDS_FULL_LENGTH and not degraded
HUMAN_THRESHOLD = 0.35       # likely_human needs s <= 0.35 and word_count >= MIN_WORDS_FOR_LABEL

# Not given as a number in planning.md: Section 5 edge case 5 logs
# "signal_disagreement" when b <= 0.10 and a >= 0.70; this threshold covers that
# case (d >= 0.60) and any other strong disagreement.
DISAGREEMENT_FLAG_THRESHOLD = 0.5

LIKELY_AI = "likely_ai"
UNCERTAIN = "uncertain"
LIKELY_HUMAN = "likely_human"


class BothSignalsFailed(Exception):
    """Section 1: both signals failed -> HTTP 503, no label issued."""


def clamp(value, low, high):
    return max(low, min(high, value))


def length_factor(word_count):
    if word_count < MIN_WORDS_FOR_LABEL:
        return LENGTH_FACTOR_UNDER_50
    if word_count < MIN_WORDS_FULL_LENGTH:
        return LENGTH_FACTOR_50_TO_149
    return LENGTH_FACTOR_FULL


def score_submission(a, b, word_count, content_type="other", flags=()):
    """Combine signal scores (None = that signal failed) into a scoring result.

    `flags` carries submission-level flags from the edge-case checks
    (e.g. "non_prose", "poem_mode"); scoring adds its own.
    """
    if a is None and b is None:
        raise BothSignalsFailed()

    flags = list(flags)
    weights = WEIGHTS_POEM if content_type == "poem" else WEIGHTS_PROSE
    L = length_factor(word_count)
    degraded = a is None or b is None

    if degraded:
        single = a if b is None else b
        combined, d = single, None
        s = 0.5 + (single - 0.5) * SINGLE_SIGNAL_SHRINK * L
        flags.append("single_signal")
    else:
        combined = weights["stylometric"] * a + weights["llm_judgment"] * b
        d = abs(a - b)
        g = 1 - DISAGREEMENT_PENALTY * d
        s = 0.5 + (combined - 0.5) * g * L
        if d >= DISAGREEMENT_FLAG_THRESHOLD:
            flags.append("signal_disagreement")

    s = clamp(s, SCORE_FLOOR, SCORE_CEILING)

    if word_count < MIN_WORDS_FOR_LABEL:
        flags.append("short_text")

    forced_uncertain = word_count < MIN_WORDS_FOR_LABEL or "non_prose" in flags
    if forced_uncertain:
        attribution = UNCERTAIN
    elif (s >= AI_THRESHOLD and not degraded and a >= AI_GATE_PER_SIGNAL
          and b >= AI_GATE_PER_SIGNAL and word_count >= MIN_WORDS_FULL_LENGTH):
        attribution = LIKELY_AI
    elif s <= HUMAN_THRESHOLD:
        attribution = LIKELY_HUMAN
    else:
        attribution = UNCERTAIN

    return {
        "confidence_score": s,
        "attribution": attribution,
        "combined_raw": combined,
        "disagreement": d,
        "length_factor": L,
        "weights": dict(weights),
        "degraded": degraded,
        "flags": flags,
        "scoring_version": SCORING_VERSION,
    }

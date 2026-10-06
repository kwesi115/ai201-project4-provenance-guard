"""Transparency labels (planning.md Section 3). Text is copied word for word."""

from scoring import AI_THRESHOLD, HUMAN_THRESHOLD, LIKELY_AI, LIKELY_HUMAN, UNCERTAIN

LABEL_LIKELY_AI = (
    "Likely AI-assisted. Our automated check found writing patterns that are common in "
    "AI-generated text. This is an estimate, not a final decision, and automated checks can be "
    "wrong. If you are the author and wrote this yourself, you can ask for a person to review it."
)
LABEL_UNCERTAIN = (
    "Origin unclear. Our automated check couldn't tell whether this piece was written by a "
    "person, with AI help, or a mix of both. This is common for short pieces, poetry, and "
    "personal writing styles, and it does not suggest anything was done wrong. If you are the "
    "author, you can ask for a person to review it."
)
LABEL_LIKELY_HUMAN = (
    "Likely human-written. Our automated check found writing patterns that are typical of "
    "human writing. This is an estimate, not a guarantee."
)

# Section 4: suffix shown while an appeal is open.
UNDER_REVIEW_SUFFIX = "The author has asked for a review of this label."

LABELS = {
    LIKELY_AI: {"label": "Likely AI-assisted", "label_text": LABEL_LIKELY_AI,
                "score_range": f"s >= {AI_THRESHOLD:.2f} and all AI gates pass"},
    UNCERTAIN: {"label": "Origin unclear", "label_text": LABEL_UNCERTAIN,
                "score_range": f"{HUMAN_THRESHOLD:.2f} < s < {AI_THRESHOLD:.2f}, any AI gate fails, "
                               f"or under 50 words"},
    LIKELY_HUMAN: {"label": "Likely human-written", "label_text": LABEL_LIKELY_HUMAN,
                   "score_range": f"s <= {HUMAN_THRESHOLD:.2f} and word_count >= 50"},
}


def get_label(attribution, under_review=False):
    """Returns {"label", "label_text"} for an attribution category from scoring.py.

    The category already encodes the thresholds and gates, so the label is
    chosen from it rather than re-deriving it from the raw score.
    """
    entry = LABELS[attribution]
    text = entry["label_text"]
    if under_review:
        text = f"{text} {UNDER_REVIEW_SUFFIX}"
    return {"label": entry["label"], "label_text": text}

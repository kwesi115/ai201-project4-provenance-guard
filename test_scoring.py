"""Milestone 4: check scoring.py against planning.md (no API calls)."""

from scoring import BothSignalsFailed, score_submission

# planning.md Section 2 worked examples (prose, >= 150 words): a, b, expected s, expected label
WORKED_EXAMPLES = [
    (0.85, 0.95, 0.89, "likely_ai"),
    (0.70, 0.90, 0.79, "uncertain"),
    (0.20, 0.90, 0.58, "uncertain"),
    (0.40, 0.15, 0.28, "likely_human"),
    (0.25, 0.10, 0.19, "likely_human"),
]


def check(name, condition):
    print(f"{'PASS' if condition else 'FAIL'}  {name}")
    return condition


if __name__ == "__main__":
    ok = True
    for a, b, want_s, want_label in WORKED_EXAMPLES:
        r = score_submission(a, b, word_count=200)
        ok &= check(f"a={a} b={b}: s={r['confidence_score']:.4f} (want {want_s}) {r['attribution']}",
                    abs(r["confidence_score"] - want_s) <= 0.01 and r["attribution"] == want_label)

    # Planning.md Section 2, poem log example: a=0.71, b=0.55 -> 0.5828 uncertain
    r = score_submission(0.71, 0.55, word_count=164, content_type="poem")
    ok &= check(f"poem example s={r['confidence_score']:.4f} (want 0.5828)", abs(r["confidence_score"] - 0.5828) < 1e-4)

    r = score_submission(None, 0.99, word_count=300)
    ok &= check(f"Signal A failed, strong b: {r['attribution']} degraded={r['degraded']}",
                r["attribution"] != "likely_ai" and r["degraded"])
    r = score_submission(0.99, None, word_count=300)
    ok &= check(f"Signal B failed, strong a: {r['attribution']} s={r['confidence_score']:.3f}",
                r["attribution"] != "likely_ai" and "single_signal" in r["flags"])
    try:
        score_submission(None, None, word_count=300)
        ok &= check("both failed raises", False)
    except BothSignalsFailed:
        ok &= check("both failed raises BothSignalsFailed (-> 503)", True)

    r = score_submission(0.95, 0.95, word_count=49)
    ok &= check(f"49 words, strong AI signals -> {r['attribution']}", r["attribution"] == "uncertain")
    r = score_submission(0.95, 0.95, word_count=149)
    ok &= check(f"149 words, strong AI signals -> {r['attribution']}", r["attribution"] != "likely_ai")
    r = score_submission(0.05, 0.05, word_count=50)
    ok &= check(f"50 words, strong human signals -> {r['attribution']} s={r['confidence_score']:.3f}",
                r["attribution"] == "likely_human")
    r = score_submission(0.55, 0.95, word_count=300)
    ok &= check(f"a=0.55 below per-signal gate -> {r['attribution']} s={r['confidence_score']:.3f}",
                r["attribution"] != "likely_ai")
    r = score_submission(0.05, 0.05, word_count=300, flags=["non_prose"])
    ok &= check(f"non_prose forced -> {r['attribution']}", r["attribution"] == "uncertain")
    r = score_submission(1.0, 1.0, word_count=300)
    ok &= check(f"clamp ceiling: s={r['confidence_score']}", r["confidence_score"] == 0.97)

    print("\nALL PASSED" if ok else "\nSOME CHECKS FAILED")

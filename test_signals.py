"""Milestone 4: run both signals plus scoring on the test inputs and print tables."""

import sys

from scoring import score_submission
from signals import llm_judgment_score, looks_like_poem, stylometric_score, word_count
from test_signal import SAMPLES as M3_SAMPLES

sys.stdout.reconfigure(encoding="utf-8")

M4_SAMPLES = {
    "clearly_ai": (
        "other",
        "Artificial intelligence represents a transformative paradigm shift in modern society. "
        "It is important to note that while the benefits of AI are numerous, it is equally essential "
        "to consider the ethical implications. Furthermore, stakeholders across various sectors must "
        "collaborate to ensure responsible deployment.",
    ),
    "clearly_human": (
        "other",
        "ok so i finally tried that new ramen place downtown and honestly? underwhelming. the broth "
        "was fine but they put WAY too much sodium in it and i was thirsty for like three hours after. "
        "my friend got the spicy version and said it was better. probably won't go back unless someone "
        "drags me there",
    ),
    "borderline_formal_human": (
        "other",
        "The relationship between monetary policy and asset price inflation has been extensively "
        "studied in the literature. Central banks face a fundamental tension between their mandate "
        "for price stability and the unintended consequences of prolonged low interest rates on "
        "equity and real estate valuations.",
    ),
    "borderline_edited_ai": (
        "other",
        "I've been thinking a lot about remote work lately. There are genuine tradeoffs, flexibility "
        "and no commute on one side, isolation and blurred work-life boundaries on the other. Studies "
        "show productivity varies widely by individual and role type.",
    ),
}

# 150+ word samples (planning.md Section 2 test set) so likely_ai / likely_human are reachable.
LONG_SAMPLES = {
    "long_clearly_ai": (
        "blog",
        "In today's fast-paced world, productivity has become more important than ever. Whether you "
        "are a student, a professional, or an entrepreneur, managing your time effectively can make "
        "a significant difference in achieving your goals. It is important to note that productivity "
        "is not about working harder, but about working smarter. One effective strategy is to "
        "prioritize your tasks based on their urgency and importance. By focusing on high-impact "
        "activities, you can maximize your output while minimizing stress. Additionally, setting "
        "clear and achievable goals provides a sense of direction and motivation. Another key aspect "
        "of productivity is maintaining a healthy work-life balance. Taking regular breaks, getting "
        "enough sleep, and engaging in physical activity can help you stay energized and focused "
        "throughout the day. Furthermore, leveraging digital tools such as task managers and "
        "calendar apps can streamline your workflow and keep you organized. In conclusion, "
        "productivity is a journey rather than a destination. By implementing these strategies "
        "consistently, you can unlock your full potential and achieve lasting success.",
    ),
    "long_clearly_human": (
        "blog",
        "My grandmother kept a jar of buttons on the windowsill above her sink. Not for sewing. "
        "She hadn't sewn anything since 1987, she told me once, when she gave up on a pair of "
        "curtains and bought some at Sears instead. The buttons were just there. Hundreds of them. "
        "Brass ones off my grandfather's army coat, little pearly ones, a wooden toggle shaped like "
        "a fish. When I was seven I'd dump the whole jar out on her kitchen table and sort them by "
        "color while she made tea that I wasn't allowed to drink. She'd tell me where some of them "
        "came from, and I'm pretty sure about half of the stories were made up. The fish toggle was "
        "from a sailor she almost married. Or a coat she found on a bus. It changed. Last month my "
        "aunt cleaned out the house and asked if I wanted anything. I said the jar. She laughed, then "
        "she didn't, and then she mailed it to me wrapped in four of my grandmother's dish towels. "
        "It's on my windowsill now. I haven't sorted them yet.",
    ),
}


def fmt(x):
    return "  err" if x is None else f"{x:5.2f}"


def run(samples):
    rows = []
    for name, (content_type, text) in samples.items():
        if content_type != "poem" and looks_like_poem(text):
            content_type = "poem"
        a = stylometric_score(text, content_type)
        b = llm_judgment_score(text, content_type)
        wc = word_count(text)
        flags = ["poem_mode"] if content_type == "poem" else []
        r = score_submission(a["score"], b["score"], wc, content_type, flags)
        rows.append((name, wc, a, b, r))
    return rows


if __name__ == "__main__":
    all_samples = {**M4_SAMPLES, **LONG_SAMPLES, **{f"m3_{k}": v for k, v in M3_SAMPLES.items()}}
    rows = run(all_samples)

    print("Signal comparison (agree = both on the same side of 0.5)")
    print(f"{'input':26} {'words':>5} {'sig1 A':>6} {'sig2 B':>6} {'|A-B|':>6}  agree")
    for name, wc, a, b, r in rows:
        if a["score"] is None or b["score"] is None:
            agree, diff = "n/a", "  n/a"
        else:
            agree = "yes" if (a["score"] >= 0.5) == (b["score"] >= 0.5) else "NO"
            diff = f"{abs(a['score'] - b['score']):6.2f}"
        print(f"{name:26} {wc:5} {fmt(a['score']):>6} {fmt(b['score']):>6} {diff}  {agree}")

    print("\nScoring")
    print(f"{'input':26} {'sig1 A':>6} {'sig2 B':>6} {'conf':>5}  {'category':13} flags")
    for name, wc, a, b, r in rows:
        print(f"{name:26} {fmt(a['score']):>6} {fmt(b['score']):>6} {r['confidence_score']:5.2f}  "
              f"{r['attribution']:13} {','.join(r['flags'])}")

    print("\nSignal B rationales / errors")
    for name, wc, a, b, r in rows:
        print(f"  {name}: {b['error'] or b['rationale']}  ({b['latency_ms']} ms)")

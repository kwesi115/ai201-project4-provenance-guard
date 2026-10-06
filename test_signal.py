"""Milestone 3: call Signal A (stylometric) directly on sample texts."""

import sys

from signals import stylometric_score, word_count

sys.stdout.reconfigure(encoding="utf-8")

SAMPLES = {
    "casual_human": (
        "other",
        "So I finally fixed the leaky faucet. Took three trips to the hardware store, "
        "which is two more than any reasonable person needs. The guy at the counter "
        "kept calling me 'chief.' I don't know why. Anyway, it's done, my hands smell "
        "like plumber's putty, and I'm never doing that again.",
    ),
    "generic_ai": (
        "other",
        "In today's fast-paced world, effective time management is essential for success. "
        "It is important to note that prioritizing tasks can significantly improve productivity. "
        "Additionally, setting clear goals helps individuals maintain focus and motivation. "
        "In conclusion, developing strong time management skills is a testament to personal growth.",
    ),
    "very_short": ("other", "Loved this. Will read again."),
    "poem": (
        "poem",
        "the tide comes in, the tide goes out\n"
        "and still I wait, and still I wait\n"
        "the gulls are loud, the gulls are loud\n"
        "my father's boat is late, is late\n"
        "the tide comes in, the tide goes out",
    ),
}

if __name__ == "__main__":
    for name, (content_type, text) in SAMPLES.items():
        result = stylometric_score(text, content_type)
        score = "None" if result["score"] is None else f"{result['score']:.3f}"
        print(f"{name:14} words={word_count(text):3}  score={score}  error={result['error']}")
        for key, value in result["features"].items():
            print(f"    {key:26} {value}")

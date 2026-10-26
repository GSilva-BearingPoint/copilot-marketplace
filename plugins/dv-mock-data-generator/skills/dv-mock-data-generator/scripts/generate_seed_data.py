"""Generates references/seed-data/company-names.txt: pronounceable, distinct brand-like names.

Deterministic for a given --seed so the committed file is reproducible. Run:
    python scripts\\generate_seed_data.py --count 6000
"""
from __future__ import annotations

import argparse
import random
from pathlib import Path

OUTPUT = Path(__file__).resolve().parent.parent / "references" / "seed-data" / "company-names.txt"
ONSETS = ("b", "br", "c", "cl", "d", "dr", "f", "fl", "g", "gr", "h", "k", "l", "m", "n", "p", "pr",
          "r", "s", "st", "sv", "t", "tr", "v", "w", "z", "al", "el", "or", "ar", "ev", "in")
VOWELS = ("a", "e", "i", "o", "u", "a", "e", "o", "ia")
CODAS = ("", "", "l", "n", "r", "s", "t", "x", "m", "nd", "rt", "lt", "ro", "ra", "na", "ta", "mo", "vo", "to", "ner", "va", "lia")


def build(count: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    names: set[str] = set()
    attempts = 0
    while len(names) < count and attempts < count * 200:
        attempts += 1
        syllables = rng.choice((2, 2, 3))
        word = "".join(rng.choice(ONSETS) + rng.choice(VOWELS) for _ in range(syllables)) + rng.choice(CODAS)
        if 5 <= len(word) <= 10:
            names.add(word.capitalize())
    if len(names) < count:
        raise RuntimeError(f"Only generated {len(names)} unique names.")
    return sorted(names, key=lambda _: rng.random())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=6000)
    parser.add_argument("--seed", type=int, default=20261016)
    args = parser.parse_args()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text("\n".join(build(args.count, args.seed)) + "\n", encoding="utf-8")
    print(f"Wrote {args.count} names to {OUTPUT}")


if __name__ == "__main__":
    main()

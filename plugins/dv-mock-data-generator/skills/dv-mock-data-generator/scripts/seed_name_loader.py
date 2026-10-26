from __future__ import annotations

import argparse
from functools import lru_cache
from pathlib import Path
import re
import unicodedata


DEFAULT_SEED_DIR = Path(__file__).resolve().parent.parent / "references" / "seed-names"
BASE_SURNAMES = (
    "Adams", "Allen", "Almeida", "Anderson", "Baker", "Bennett", "Brooks", "Brown",
    "Campbell", "Carvalho", "Clark", "Collins", "Cook", "Cooper", "Costa", "Davis",
    "Edwards", "Evans", "Fernandes", "Ferreira", "Foster", "Garcia", "Gomes", "Gray",
    "Green", "Hall", "Harris", "Hill", "Howard", "Hughes", "Jackson", "Johnson",
    "Jones", "Kelly", "King", "Lee", "Lewis", "Lopes", "Martin", "Martinez",
    "Miller", "Mitchell", "Moore", "Morgan", "Morris", "Murphy", "Nelson", "Oliveira",
    "Parker", "Pereira", "Phillips", "Powell", "Price", "Reed", "Reyes", "Richardson",
    "Roberts", "Robinson", "Rodrigues", "Rogers", "Ross", "Russell", "Sanchez", "Santos",
    "Scott", "Silva", "Smith", "Stewart", "Taylor", "Thomas", "Thompson", "Torres",
    "Turner", "Walker", "Ward", "Watson", "White", "Williams", "Wilson", "Wood",
    "Wright", "Young", "Mendes", "Moreira", "Nunes", "Ribeiro", "Rocha", "Sousa",
    "Teixeira", "Correia", "Cunha", "Dias", "Marques", "Monteiro", "Pinto", "Ramos",
    "Cardoso", "Castro", "Melo", "Neves",
)
SURNAME_SUFFIXES = (
    "son", "sen", "man", "mann", "ton", "ley", "ly", "ford", "wood", "well",
    "field", "brook", "stone", "berg", "berger", "stein", "strom", "lund", "holm", "dal",
    "dahl", "by", "mark", "mont", "court", "worth", "more", "ridge", "hart", "shaw",
    "ski", "sky", "vic", "vich", "ov", "ova", "ev", "ez", "es", "is",
)


def read_names(path: Path) -> tuple[str, ...]:
    if not path.is_file():
        raise FileNotFoundError(f"Seed-name file not found: {path}")
    names = tuple(
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    if not names:
        raise ValueError(f"Seed-name file is empty: {path}")
    return names


def surname_stem(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    letters = re.sub(r"[^A-Za-z]", "", ascii_value)
    return letters[:10].capitalize()


def generate_last_names(
    count: int,
    source_names: tuple[str, ...],
) -> tuple[str, ...]:
    if count <= 0:
        raise ValueError("Last-name count must be positive.")
    candidates: list[str] = []
    seen: set[str] = set()

    def add(candidate: str) -> None:
        key = candidate.casefold()
        if candidate and candidate.isalpha() and key not in seen:
            seen.add(key)
            candidates.append(candidate)

    for surname in BASE_SURNAMES:
        add(surname)
    for source in source_names:
        stem = surname_stem(source)
        if len(stem) < 3:
            continue
        for suffix in SURNAME_SUFFIXES:
            if stem.casefold().endswith(suffix):
                continue
            add(f"{stem}{suffix}")
            if len(candidates) >= count:
                return tuple(candidates)

    if count > len(candidates):
        raise ValueError(
            f"Requested {count} last names, but only {len(candidates)} unique single-token "
            "names could be generated."
        )
    return tuple(candidates[:count])


def write_last_names(
    seed_dir: Path = DEFAULT_SEED_DIR,
    target_count: int | None = None,
    overwrite: bool = False,
) -> Path:
    first_names = read_names(seed_dir / "first-names.txt")
    middle_names = read_names(seed_dir / "middle-names.txt")
    count = target_count or max(len(first_names), 10000)
    output = seed_dir / "last-names.txt"
    if output.exists() and not overwrite:
        existing = read_names(output)
        if len(existing) >= count:
            return output
        raise FileExistsError(
            f"{output} has {len(existing)} rows; expected {count}. Use --overwrite."
        )
    generated = generate_last_names(count, first_names + middle_names)
    output.write_text("\n".join(generated) + "\n", encoding="utf-8")
    return output


@lru_cache(maxsize=4)
def load_seed_names(seed_dir: str | Path = DEFAULT_SEED_DIR) -> dict[str, tuple[str, ...]]:
    directory = Path(seed_dir)
    return {
        "first": read_names(directory / "first-names.txt"),
        "middle": read_names(directory / "middle-names.txt"),
        "last": read_names(directory / "last-names.txt"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate and validate text-file name seeds used by test-data generation."
    )
    parser.add_argument("--seed-dir", type=Path, default=DEFAULT_SEED_DIR)
    parser.add_argument("--generate-last-names", action="store_true")
    parser.add_argument("--target-count", type=int)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    if args.generate_last_names:
        output = write_last_names(
            seed_dir=args.seed_dir,
            target_count=args.target_count,
            overwrite=args.overwrite,
        )
        print(f"Last-name seed written: {output}")

    names = load_seed_names(args.seed_dir)
    print(
        "Seed-name rows: "
        f"first={len(names['first'])}, "
        f"middle={len(names['middle'])}, "
        f"last={len(names['last'])}"
    )


if __name__ == "__main__":
    main()

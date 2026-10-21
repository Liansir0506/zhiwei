"""Offline lexical-neighbor screen for new source cards; not an independence verdict.

Usage: python -m scripts.screen_holdout_source_overlap --legacy F:/heima/myproject/eval_sets/v4-r3-tiered.json
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import unicodedata
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "data/candidates/stage4-holdout-source-review-r1/raw"
OLD_RAW_DIR = ROOT / "data/candidates/v4-quality-r1-final/raw"
PROTOCOL_DIR = ROOT / "data/candidates/stage4-protocol-notes-r1/raw"
OLD_V3 = ROOT / "eval_sets/v3.json"
WINDOW = 420
STRIDE = 300


def normalized(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    text = re.sub(r"https?://\S+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def windows(text: str) -> list[tuple[int, str]]:
    text = normalized(text)
    if not text:
        return []
    return [(start, text[start:start + WINDOW]) for start in range(0, len(text), STRIDE)]


def tree_digest(directory: Path) -> tuple[int, str]:
    files = sorted(path for path in directory.glob("*") if path.suffix in {".md", ".csv"})
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.encode("utf-8"))
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return len(files), digest.hexdigest()


def old_units(raw_dir: Path, protocol_dir: Path, old_v3: Path, legacy: Path) -> list[dict]:
    units = []
    for directory, kind in ((raw_dir, "candidate_raw"), (protocol_dir, "protocol_dev")):
        for path in sorted(directory.glob("*")):
            if path.suffix == ".md":
                body = path.read_text(encoding="utf-8")
                if kind == "protocol_dev":
                    body = "\n".join(body.splitlines()[4:-1])
                for offset, snippet in windows(body):
                    units.append({"kind": kind, "source": path.name, "unit": f"char:{offset}",
                                  "text": snippet})
            elif path.suffix == ".csv":
                with path.open(encoding="utf-8-sig", newline="") as stream:
                    for row_number, row in enumerate(csv.DictReader(stream), start=2):
                        body = " ".join(str(value or "") for value in row.values())
                        for offset, snippet in windows(body):
                            units.append({"kind": kind, "source": path.name,
                                          "unit": f"row:{row_number}:char:{offset}", "text": snippet})
    for path, kind in ((old_v3, "legacy_v3_query"), (legacy, "legacy_r3_query")):
        for case in json.loads(path.read_text(encoding="utf-8"))["cases"]:
            query = normalized(case.get("query", ""))
            if query:
                units.append({"kind": kind, "source": path.name, "unit": str(case["id"]), "text": query})
    return units


def screen(source_dir: Path, raw_dir: Path, protocol_dir: Path,
           old_v3: Path, legacy: Path, *, top_k: int = 5) -> dict:
    if top_k < 1:
        raise ValueError("top_k must be positive")
    cards = sorted(source_dir.glob("*.md"))
    if len(cards) != 2:
        raise ValueError("expected two candidate source cards")
    units = old_units(raw_dir, protocol_dir, old_v3, legacy)
    if not units:
        raise ValueError("empty historical comparison set")
    # Only the factual body is screened; citations and repeated safety boilerplate
    # would otherwise make unrelated protocol notes look artificially similar.
    card_bodies = [normalized(" ".join(path.read_text(encoding="utf-8").splitlines()[4:6]))
                   for path in cards]
    if any(not body for body in card_bodies):
        raise ValueError("empty source card body")
    vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(2, 4),
                                 max_features=60000, sublinear_tf=True, dtype=np.float32)
    matrix = vectorizer.fit_transform([unit["text"] for unit in units] + card_bodies)
    scores = (matrix[:-len(cards)] @ matrix[-len(cards):].T).toarray()
    matches = {}
    for card_index, card in enumerate(cards):
        indices = np.argsort(-scores[:, card_index], kind="stable")
        nearest = []
        seen = set()
        for index in indices:
            unit = units[int(index)]
            key = (unit["kind"], unit["source"])
            if key in seen:
                continue
            seen.add(key)
            nearest.append({"kind": unit["kind"], "source": unit["source"],
                            "unit": unit["unit"], "cosine": round(float(scores[index, card_index]), 4),
                            "snippet": unit["text"][:180]})
            if len(nearest) >= top_k:
                break
        matches[card.name] = nearest
    paths = {"legacy_r3": legacy, "legacy_v3": old_v3}
    raw_count, raw_hash = tree_digest(raw_dir)
    protocol_count, protocol_hash = tree_digest(protocol_dir)
    return {
        "audit_kind": "lexical_neighbor_screen_not_semantic_or_independence_approval",
        "input_hashes": {name: hashlib.sha256(path.read_bytes()).hexdigest()
                         for name, path in paths.items()},
        "source_card_hashes": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in cards},
        "corpus_manifests": {"candidate_raw": {"files": raw_count, "sha256": raw_hash},
                             "protocol_dev": {"files": protocol_count, "sha256": protocol_hash}},
        "old_units": len(units), "comparison_scope": ["candidate_raw_md_csv", "protocol_dev_md",
                                                       "legacy_v3_queries", "legacy_r3_queries"],
        "method": "NFKC casefold; char 2-4gram TF-IDF cosine; 420-char windows/300 stride; one best unit per file",
        "matches": matches,
        "conclusion": "review hints only; low cosine cannot establish semantic independence",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy", required=True, type=Path)
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    print(json.dumps(screen(SOURCE_DIR, OLD_RAW_DIR, PROTOCOL_DIR, OLD_V3, args.legacy,
                            top_k=args.top_k), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

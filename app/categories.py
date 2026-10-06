"""Five-category deterministic taxonomy for the MVP."""
from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import List, Tuple


def load_taxonomy(path: str = "config/themes.json") -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def classify(text: str, taxonomy: dict) -> Tuple[str, List[str], str]:
    normalized = "".join(
        character for character in unicodedata.normalize("NFD", text.casefold())
        if unicodedata.category(character) != "Mn"
    )
    matching = []
    for category in taxonomy["categories"]:
        terms = [term for term in category["terms"] if term in normalized]
        if terms:
            matching.append((category["id"], terms))
    if not matching:
        return None, [], "nenhuma regra temática obteve suporte"
    # Explicit policy: regulation wins for a regulatory ETF event; market is secondary.
    identifiers = [item[0] for item in matching]
    primary = "politics_regulation" if "politics_regulation" in identifiers else identifiers[0]
    secondary = [identifier for identifier in identifiers if identifier != primary][:1]
    matched_terms = next(terms for identifier, terms in matching if identifier == primary)
    return primary, secondary, "termos correspondentes: " + ", ".join(matched_terms)

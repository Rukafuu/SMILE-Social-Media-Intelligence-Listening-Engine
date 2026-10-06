"""Conservative lexical event association for the synthetic MVP dataset."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class EventMatch:
    topic_id: str
    title: str
    reason: str


RULES = (
    ("token-aurora-protocol", "Token Aurora fictício cresce após anúncio de protocolo", ("token aurora", "protocolo")),
    ("bitcoin-etf-stable", "Bitcoin permanece estável após sessão de ETFs fictícios", ("bitcoin", "etf")),
    ("regulator-crypto-etf", "Regulador fictício aprova consulta sobre ETF cripto", ("regulador", "etf")),
    ("exchange-aurora-listing", "Exchange Aurora anuncia listagem fictícia", ("exchange aurora", "listagem")),
    ("exchange-aurora-incident", "Exchange Aurora sofre incidente fictício", ("exchange aurora", "incidente")),
    ("horizonte-crypto-sponsorship", "Clube Horizonte fecha patrocínio cripto fictício", ("clube horizonte", "patrocínio")),
    ("market-rates-crypto-etf", "Mercado debate juros e ETF cripto fictício", ("mercado", "juros", "etf")),
    ("novachain-audit-rumor", "Rumor sobre auditoria da NovaChain", ("novachain", "auditoria")),
    ("old-orbit-announcement", "Anúncio antigo da Orbit recircula", ("orbit", "anúncio antigo")),
)


def normalize_for_family(content: str) -> str:
    # Families measure copies. Similarity-based near-copy association is added
    # separately; unique wording about the same event stays a distinct family.
    return " ".join(content.casefold().split())


def match_event(content: str) -> Optional[EventMatch]:
    normalized = normalize_for_family(content)
    for topic_id, title, required_terms in RULES:
        if all(term in normalized for term in required_terms):
            return EventMatch(topic_id, title, "aliases e ação/objeto compatíveis")
    # External hashtag samples do not carry the synthetic scenario vocabulary.
    # Keep the fallback intentionally narrow: it is an observed discussion
    # cluster, not a claim that every post describes one real-world event.
    if "bitcoin" in normalized or "#btc" in normalized or " btc " in normalized:
        return EventMatch("external-bitcoin-discussion", "Bitcoin — discussão pública observada",
                          "agrupamento heurístico externo por entidade; não implica um evento confirmado")
    return None

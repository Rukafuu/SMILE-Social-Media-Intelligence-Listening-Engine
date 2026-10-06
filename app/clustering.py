"""Conservative event rules: entity plus action; no scenario labels as input."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class EventMatch:
    topic_id: str
    title: str
    reason: str


def normalize_for_family(content: str) -> str:
    return " ".join(content.casefold().split())


def lexical(content: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", normalize_for_family(content))
                   if unicodedata.category(c) != "Mn")


# Alternative phrases allow paraphrases; action separates events of one entity.
RULES = (
    ("token-aurora-protocol", "Token Aurora — anúncio de protocolo", ("token aurora", "aurora token"), ("protocolo", "protocol", "rede", "atualizacao")),
    ("bitcoin-etf-stable", "Bitcoin — discussão de ETFs", ("bitcoin", "btc"), ("etf", "fundo negociado")),
    ("regulator-crypto-etf", "Regulador — consulta sobre ETF cripto", ("regulador", "autoridade reguladora"), ("etf", "fundo cripto")),
    ("exchange-aurora-listing", "Exchange Aurora — listagem", ("exchange aurora", "corretora aurora"), ("listagem", "listar", "negociacao")),
    ("exchange-aurora-incident", "Exchange Aurora — incidente", ("exchange aurora", "corretora aurora"), ("incidente", "ataque", "invasao", "falha de seguranca")),
    ("horizonte-crypto-sponsorship", "Clube Horizonte — patrocínio cripto", ("clube horizonte",), ("patrocinio", "patrocinador")),
    ("market-rates-crypto-etf", "Mercado — juros e ETF cripto", ("mercado",), ("juros",)),
    ("novachain-audit-rumor", "NovaChain — rumor de auditoria", ("novachain",), ("auditoria", "relatorio de auditor")),
    ("old-orbit-announcement", "Orbit — anúncio recirculado", ("orbit",), ("anuncio", "comunicado")),
    ("pumplet-campaign", "Pumplet — campanha de token", ("pumplet",), ("token",)),
)


def match_event(content: str) -> Optional[EventMatch]:
    text = lexical(content)
    for topic_id, title, entities, actions in RULES:
        if any(term in text for term in entities) and any(term in text for term in actions):
            return EventMatch(topic_id, title, "entidade e ação compatíveis por regras lexicais")
    if re.search(r"\b(bitcoin|btc)\b", text):
        return EventMatch("external-bitcoin-discussion", "Bitcoin — discussão pública observada",
                          "agrupamento heurístico externo por entidade; não confirma um evento")
    return None

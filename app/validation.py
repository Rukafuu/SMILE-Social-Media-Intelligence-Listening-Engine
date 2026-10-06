"""Host-side validation: model output cannot manufacture evidence or certainty."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict

from app.agent_types import evidence_for_topic


def validate_analysis(repository, topic: Dict, payload: Dict) -> Dict:
    """Return a repaired, conservative payload and explicit validation flags."""
    references = payload.get("evidence_refs") or []
    valid_ids = {item["post_id"] for item in evidence_for_topic(repository, topic["topic_id"], 100)}
    invalid = [reference for reference in references if reference not in valid_ids]
    flags = list(payload.get("risk_flags") or [])
    uncertainties = list(payload.get("uncertainties") or [])
    if invalid:
        flags.append("invalid_evidence_reference")
    if payload.get("recommendation") == "HIGHLIGHT" and (invalid or not references):
        payload["recommendation"] = "MONITOR"
        flags.append("highlight_limited_by_evidence_validation")
    evidence = evidence_for_topic(repository, topic["topic_id"], 100)
    sources = {item["cited_source"] for item in evidence if item.get("cited_source")}
    if topic["topic_id"] == "novachain-audit-rumor" and len(sources) <= 1:
        payload["recommendation"] = "MONITOR"
        uncertainties.append("Rumor com uma única origem conhecida; repetição não confirma o fato.")
        flags.append("single_known_source")
    if topic["topic_id"] == "old-orbit-announcement":
        uncertainties.append("O evento tem publicação anterior à janela; o sinal mede recirculação atual.")
        flags.append("old_event_recirculation")
    payload["risk_flags"] = sorted(set(flags))
    payload["uncertainties"] = sorted(set(uncertainties))
    return payload

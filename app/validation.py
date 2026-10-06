"""Schema, consulted-reference and exact-excerpt validation on the host."""
from __future__ import annotations

from app.agent_types import evidence_for_topic


class ValidationError(ValueError):
    pass


MODEL_KEYS = {"recommendation", "summary", "claims", "evidence_refs", "uncertainties", "risk_flags"}


def validate_schema(payload):
    if not isinstance(payload, dict) or set(payload) != MODEL_KEYS:
        raise ValidationError("expected the six documented model output fields")
    if payload["recommendation"] not in {"HIGHLIGHT", "MONITOR", "DISCARD"}:
        raise ValidationError("invalid recommendation")
    if not isinstance(payload["summary"], str) or not 1 <= len(payload["summary"]) <= 1800:
        raise ValidationError("summary must be nonempty text up to 1800 characters")
    for field in ("evidence_refs", "uncertainties", "risk_flags"):
        if not isinstance(payload[field], list) or len(payload[field]) > 16 or any(not isinstance(x, str) or len(x) > 500 for x in payload[field]):
            raise ValidationError(field + " must be a bounded list of strings")
    if not isinstance(payload["claims"], list) or len(payload["claims"]) > 8:
        raise ValidationError("claims must be a list with at most eight entries")
    for claim in payload["claims"]:
        if not isinstance(claim, dict) or set(claim) != {"text", "status", "evidence_refs"}:
            raise ValidationError("claim must contain text, status and evidence_refs")
        if not isinstance(claim["text"], str) or not 1 <= len(claim["text"]) <= 280:
            raise ValidationError("claim text must be bounded")
        if claim["status"] not in {"observed", "unconfirmed", "confirmed_in_simulation"}:
            raise ValidationError("claim status is not allowed")
        if not isinstance(claim["evidence_refs"], list) or len(claim["evidence_refs"]) > 8 or any(not isinstance(x, str) for x in claim["evidence_refs"]):
            raise ValidationError("claim references must be a bounded list of strings")
    return payload


def validate_analysis(repository, topic, payload, consulted=None):
    """Only cited excerpts actually read can become observations, never inferred facts."""
    window = topic.get("analysis_window") or {}
    evidence = consulted if consulted is not None else evidence_for_topic(
        repository, topic["topic_id"], 8, window.get("start"), window.get("end"), topic.get("metrics_version"))
    valid = {item["ref"]: item for item in evidence}
    refs = payload.get("evidence_refs") or []
    flags = set(payload.get("risk_flags") or [])
    uncertainties = set(payload.get("uncertainties") or [])
    invalid = [ref for ref in refs if ref not in valid]
    if invalid:
        flags.add("invalid_evidence_reference")
    # Never invent or attach a reference to compensate for a bad model output.
    payload["evidence_refs"] = list(dict.fromkeys(ref for ref in refs if ref in valid))
    for claim in payload.get("claims") or []:
        claim_refs = claim["evidence_refs"]
        supported = bool(claim_refs) and all(ref in valid and ref in payload["evidence_refs"] for ref in claim_refs)
        excerpt = supported and all(claim["text"] in valid[ref]["content"] for ref in claim_refs)
        claim["evidence_refs"] = list(dict.fromkeys(ref for ref in claim_refs if ref in valid and ref in payload["evidence_refs"]))
        if not excerpt:
            claim["status"] = "unconfirmed"
            flags.add("unsupported_claim")
            uncertainties.add("Há interpretação sem trecho literal de suporte; ela não confirma o fato.")
        if claim["status"] == "confirmed_in_simulation" and not (
            excerpt and all(valid[ref].get("is_synthetic") and valid[ref].get("platform") == "synthetic" and
                            valid[ref].get("source_kind") == "synthetic_official" for ref in claim_refs)):
            claim["status"] = "unconfirmed"
            flags.add("unsupported_confirmation")
    circulation = topic.get("circulation") or {}
    flags.update(circulation.get("risk_flags") or [])
    if payload.get("analysis_mode") == "openrouter" and not payload.get("evidence_refs"):
        flags.add("no_consulted_evidence")
    if flags & {"invalid_evidence_reference", "unsupported_claim", "unsupported_confirmation", "no_consulted_evidence"}:
        if payload.get("recommendation") == "HIGHLIGHT":
            payload["recommendation"] = "MONITOR"
        flags.add("highlight_limited_by_evidence_validation")
    score = topic.get("score")
    if score is None:
        payload["recommendation"] = "MONITOR"
        uncertainties.add("Histórico/cobertura insuficientes para calcular tendência conclusiva.")
    elif score < 35:
        payload["recommendation"] = "DISCARD"
    elif score < 70 and payload.get("recommendation") == "HIGHLIGHT":
        payload["recommendation"] = "MONITOR"
    authority = any(item.get("source_kind") == "synthetic_official" for item in evidence)
    if flags & {"single_known_source", "contradictory_content", "old_event_recirculation", "high_author_concentration"}:
        if payload.get("recommendation") == "HIGHLIGHT" and not (authority and "old_event_recirculation" not in flags and "high_author_concentration" not in flags):
            payload["recommendation"] = "MONITOR"
    if "contradictory_content" in flags:
        uncertainties.add("Há textos contraditórios na janela; a divergência exige revisão humana.")
    if "single_known_source" in flags:
        uncertainties.add("Uma única origem declarada não representa confirmação independente.")
    if "old_event_recirculation" in flags:
        uncertainties.add("O sinal atual mede circulação de um evento anterior à janela.")
    uncertainties.add("Autores distintos não comprovam origens independentes; região e independência permanecem desconhecidas.")
    if payload.get("analysis_mode") == "openrouter":
        # Retain model prose for audit, while the primary summary is reproducible.
        payload["model_summary"] = payload["summary"]
        payload["model_summary_status"] = "unverified_interpretation"
    n = topic.get("N", 0)
    payload["summary"] = "Na janela analisada, foram observados %s posts de %s autores. Score: %s; estágio: %s. Evidências consultadas: %s. As publicações não confirmam, por si, as afirmações que contêm." % (n, topic.get("U", 0), score if score is not None else "indisponível", topic.get("stage", "unknown"), len(evidence))
    payload["risk_flags"] = sorted(flags)
    payload["uncertainties"] = sorted(uncertainties)
    return payload

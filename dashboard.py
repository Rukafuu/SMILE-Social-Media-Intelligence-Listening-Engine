"""Local Streamlit review console for collected social data."""
import json
import os
from datetime import datetime, timezone

import streamlit as st

from app.repository import Repository
from app.agent_types import evidence_for_topic
from app.categories import load_taxonomy
from app.collectors import MastodonHashtagFeed, SourceAccessError, TransientCollectionError, collect_all
from app.settings import load_local_env


# External collection is the product-facing default. The deterministic dataset
# remains available only when a demo explicitly sets CRYPTOBR_DATABASE.
load_local_env()
DATABASE = os.getenv("CRYPTOBR_DATABASE", "data/mastodon_public.sqlite3")


def repository():
    """Create the connection in the current Streamlit script thread.

    Streamlit reruns can execute in a different thread; caching a sqlite3
    connection across those reruns raises ProgrammingError before the dashboard
    can render. SQLite remains the source of truth, so a short-lived local
    connection is the safer dashboard boundary.
    """
    instance = Repository(DATABASE)
    instance.initialize()
    return instance


def main():
    st.set_page_config(page_title="CryptoBR Social Intelligence", layout="wide")
    st.title("CryptoBR Social Intelligence")
    store = repository()
    profile = store.data_profile()
    total_posts = profile["total_posts"]
    synthetic_posts = profile["synthetic_posts"]
    if total_posts and synthetic_posts == total_posts:
        st.caption("Dados de demonstração — todos os posts deste banco são sintéticos.")
    elif total_posts and not synthetic_posts:
        st.caption("Dados externos capturados — cobertura limitada à fonte e consulta declaradas abaixo; não representa discussão global.")
    elif total_posts:
        st.caption("Conjunto misto — inclui posts sintéticos e externos; compare apenas recortes de mesma origem e cobertura.")
    else:
        st.caption("Banco sem posts coletados.")
    with st.sidebar:
        st.divider()
        st.subheader("Coleta pública")
        hashtag = st.text_input("Hashtag", value="bitcoin", help="Sem #. A coleta usa uma página pública da instância.")
        instance = st.text_input("Instância Mastodon", value=os.getenv("MASTODON_BASE_URL", "https://mastodon.social"))
        if st.button("Buscar agora", type="primary", use_container_width=True):
            try:
                normalized_tag = hashtag.strip().lstrip("#")
                if not normalized_tag:
                    raise ValueError("informe uma hashtag")
                source_key = "mastodon-public:%s:%s:%s" % (
                    instance.rstrip("/"), normalized_tag.casefold(), datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"),
                )
                result = collect_all(store, MastodonHashtagFeed(instance, token=None), source_key,
                                     query=normalized_tag, max_pages=1)
                st.success("Busca concluída: %s novos posts." % result["inserted"])
                st.rerun()
            except (ValueError, SourceAccessError, TransientCollectionError) as error:
                st.error("Não foi possível coletar a hashtag: " + str(error))
    rows = store.dashboard_topics()
    collection = store.latest_collection_run()
    if collection:
        coverage = json.loads(collection["coverage"] or "{}")
        st.caption(
            "Coleta mais recente: %s · %s · %s posts inseridos · concluída em %s"
            % (collection["source_key"], collection["status"], collection["inserted_posts"],
               collection["finished_at"] or "em andamento")
        )
        if coverage:
            st.caption("Cobertura declarada: " + json.dumps(coverage, ensure_ascii=False))
        if collection["status"] in {"failed", "interrupted"}:
            st.warning("A última coleta não terminou normalmente; os dados exibidos podem estar defasados.")
    if not rows:
        st.info("Ainda não há análise. Gere o dataset, colete e execute `python -m app.cli analyze --with-agent`.")
        return
    categories = ["Todas"] + sorted({row["primary_category"] for row in rows if row["primary_category"]})
    selected = st.sidebar.selectbox("Categoria", categories)
    secondary = ["Todas"] + sorted({category for row in rows for category in json.loads(row["secondary_categories"] or "[]")})
    selected_secondary = st.sidebar.selectbox("Categoria secundária", secondary)
    filtered = [row for row in rows if (selected == "Todas" or row["primary_category"] == selected)
                and (selected_secondary == "Todas" or selected_secondary in json.loads(row["secondary_categories"] or "[]"))]
    st.sidebar.caption("Filtros secundários não alteram o ranking global; o mesmo topic_id não é somado duas vezes.")
    st.subheader("Ranking: score versus volume")
    score_order = {row["topic_id"]: index for index, row in enumerate(rows, start=1)}
    volume_order = {row["topic_id"]: index for index, row in enumerate(
        sorted(rows, key=lambda item: (-item["post_count"], item["title"])), start=1
    )}
    display = [{"Rank score": score_order[row["topic_id"]], "Rank volume": volume_order[row["topic_id"]],
                "Tema": row["title"], "Categoria": row["primary_category"], "Score": row["score"],
                "Estágio": row["stage"], "Posts": row["post_count"], "Autores": row["author_count"],
                "HHI": round(row["hhi"], 3) if row["hhi"] is not None else None,
                "Recomendação": row["recommendation"] or "sem análise"} for row in filtered]
    st.dataframe(display, use_container_width=True, hide_index=True)
    labels = {row["topic_id"]: row["title"] for row in filtered}
    topic_id = st.selectbox("Ver assunto", list(labels), format_func=labels.get)
    row = next(item for item in filtered if item["topic_id"] == topic_id)
    left, right, third = st.columns(3)
    left.metric("Score", "—" if row["score"] is None else "%.2f" % row["score"])
    right.metric("Contribuições / baseline", "%s / %s" % (row["capped_contributions"], row["baseline"]))
    third.metric("Autores distintos", row["author_count"])
    st.json({"componentes": json.loads(row["components"]), "modo_de_análise": row["analysis_mode"],
             "atualizado_em": row["updated_at"]})
    taxonomy = load_taxonomy()
    category_ids = [item["id"] for item in taxonomy["categories"]]
    st.subheader("Correção de classificação")
    with st.form("classification-%s" % topic_id):
        reviewer = st.text_input("Revisor da classificação", key="classification-reviewer-" + topic_id)
        primary = st.selectbox("Categoria principal", ["Sem categoria"] + category_ids,
                               index=(category_ids.index(row["primary_category"]) + 1)
                               if row["primary_category"] in category_ids else 0)
        allowed_secondary = ["Nenhuma"] + [item for item in category_ids if item != primary]
        current_secondary = json.loads(row["secondary_categories"] or "[]")
        secondary = st.selectbox("Categoria secundária", allowed_secondary,
                                 index=allowed_secondary.index(current_secondary[0])
                                 if current_secondary and current_secondary[0] in allowed_secondary else 0)
        if st.form_submit_button("Salvar correção"):
            try:
                store.override_topic_classification(
                    topic_id, None if primary == "Sem categoria" else primary,
                    [] if secondary == "Nenhuma" else [secondary], reviewer, taxonomy["version"],
                )
                st.success("Correção registrada; ela prevalece nas próximas análises.")
            except ValueError as error:
                st.error(str(error))
    evidence = evidence_for_topic(store, topic_id, 8)
    if evidence:
        st.subheader("Evidências capturadas")
        for item in evidence:
            st.write("%s — %s" % (item["post_id"], item["content"]))
            if item["source_url"].startswith(("https://", "http://")):
                st.link_button("Abrir referência", item["source_url"], key="source-" + item["post_id"])
            else:
                st.caption("Referência local: " + item["source_url"])
    if row["alert_id"]:
        alert = json.loads(row["payload"])
        st.subheader("Sugestão automática")
        st.write(alert.get("summary", "Sem resumo."))
        st.caption("Recomendação: %s · modo: %s" % (row["recommendation"], row["analysis_mode"]))
        if alert.get("uncertainties"):
            st.warning("Incertezas: " + "; ".join(alert["uncertainties"]))
        if alert.get("risk_flags"):
            st.error("Riscos: " + "; ".join(alert["risk_flags"]))
        st.subheader("Decisão humana")
        with st.form("review-%s" % row["alert_id"]):
            reviewer = st.text_input("Revisor")
            decision = st.selectbox("Decisão", ["APPROVE", "REJECT", "EDIT"])
            summary = st.text_area("Resumo revisado (opcional)")
            submitted = st.form_submit_button("Registrar revisão")
            if submitted:
                try:
                    store.save_review(row["alert_id"], decision, summary, reviewer)
                    st.success("Revisão registrada no histórico.")
                except ValueError as error:
                    st.error(str(error))
        reviews = store.reviews_for_alert(row["alert_id"])
        if reviews:
            st.dataframe([dict(item) for item in reviews], use_container_width=True, hide_index=True)
    else:
        st.info("Ainda não há sugestão automática para este assunto.")


if __name__ == "__main__":
    main()

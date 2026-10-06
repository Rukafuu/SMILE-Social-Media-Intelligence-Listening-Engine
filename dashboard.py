"""Local Streamlit review console for the synthetic CryptoBR MVP."""
import json
import os

import streamlit as st

from app.repository import Repository


DATABASE = os.getenv("CRYPTOBR_DATABASE", "data/cryptobr.sqlite3")


@st.cache_resource
def repository():
    instance = Repository(DATABASE)
    instance.initialize()
    return instance


def main():
    st.set_page_config(page_title="CryptoBR Social Intelligence", layout="wide")
    st.title("CryptoBR Social Intelligence")
    st.caption("Protótipo local — todos os posts e alegações exibidos nesta instância são sintéticos.")
    store = repository()
    rows = store.dashboard_topics()
    if not rows:
        st.info("Ainda não há análise. Gere o dataset, colete e execute `python -m app.cli analyze --with-agent`.")
        return
    categories = ["Todas"] + sorted({row["primary_category"] for row in rows if row["primary_category"]})
    selected = st.sidebar.selectbox("Categoria", categories)
    filtered = [row for row in rows if selected == "Todas" or row["primary_category"] == selected]
    st.sidebar.caption("Filtros secundários não alteram o ranking global; o mesmo topic_id não é somado duas vezes.")
    st.subheader("Ranking por tendência")
    display = [{"Tema": row["title"], "Categoria": row["primary_category"], "Score": row["score"],
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

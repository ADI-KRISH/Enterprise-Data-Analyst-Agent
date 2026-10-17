"""Streamlit dashboard: asks the FastAPI service, renders answer, chart, table and trace.

The API key comes from this process's environment (API_KEY), never from the browser.
"""

from __future__ import annotations

import os

import httpx
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

API_URL = os.environ.get("API_URL", "http://localhost:8000").rstrip("/")
API_KEY = os.environ.get("API_KEY", "")
MAX_QUESTION_CHARS = 500
EXAMPLES = [
    "total revenue last quarter",
    "revenue by region last quarter",
    "top 5 products by revenue",
    "why did revenue decrease in South India last quarter?",
    "monthly revenue trend for the last 12 months",
]


def ask(question: str) -> tuple[int, dict]:
    r = httpx.post(f"{API_URL}/analyze", json={"question": question},
                   headers={"X-API-Key": API_KEY}, timeout=200)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"error": "Unexpected response from the API."}


def render(resp: dict) -> None:
    if resp.get("refused"):
        st.warning(resp["answer"])
        return
    if resp.get("needs_clarification"):
        st.info(resp.get("clarification_question") or resp.get("answer"))
        return
    st.markdown(resp.get("answer") or "")
    if resp.get("grounded") is False:
        st.caption("⚠️ Some figures in this answer could not be verified against the query results.")
    if resp.get("time_range"):
        st.caption(f"Period: {resp['time_range'].get('label')} (relative dates resolve against the latest data date)")
    viz = resp.get("visualization")
    if viz and viz.get("figure"):
        st.plotly_chart(go.Figure(viz["figure"]), use_container_width=True)
    if resp.get("columns"):
        st.dataframe(pd.DataFrame(resp["data"], columns=resp["columns"]), use_container_width=True)
    with st.expander("SQL"):
        for q in resp.get("queries") or []:
            st.caption(f"{q['purpose']} — {q['row_count']} rows")
            st.code(q["sql"], language="sql")
    with st.expander("Trace"):
        st.dataframe(pd.DataFrame([e for e in resp.get("trace", []) if e.get("event") == "node"]),
                     use_container_width=True)
        st.caption(f"latency {resp.get('latency_ms', 0) / 1000:.1f}s · request {resp.get('request_id')}")


def main() -> None:
    st.set_page_config(page_title="Enterprise Data Analyst Agent", layout="wide")
    st.title("Enterprise Data Analyst Agent")
    st.caption("Synthetic Indian retail data (not real). Answers come from executed read-only SQL.")
    if "q" not in st.session_state:
        st.session_state.q = ""
    cols = st.columns(len(EXAMPLES))
    for col, ex in zip(cols, EXAMPLES):
        if col.button(ex, use_container_width=True):
            st.session_state.q = ex
    question = st.text_input("Ask a business question", key="q", max_chars=MAX_QUESTION_CHARS)
    if st.button("Analyze", type="primary") and question.strip():
        with st.spinner("Analyzing…"):
            try:
                status, resp = ask(question.strip())
            except httpx.HTTPError:
                st.error("Could not reach the API.")
                return
        if status != 200:
            st.error(f"{resp.get('error', 'Request failed.')} (request {resp.get('request_id', '-')})")
            return
        render(resp)


main()

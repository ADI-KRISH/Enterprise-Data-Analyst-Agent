"""Streamlit dashboard: ask a question, see the answer, chart, data, SQL and agent trace.

    streamlit run dashboard/streamlit_app.py
Talks to the FastAPI service (API_URL, default http://localhost:8000).
"""

from __future__ import annotations

import os

import httpx
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

API_URL = os.getenv("API_URL", "http://localhost:8000")
EXAMPLES = [
    "What was total revenue last quarter?",
    "Top 5 product categories by revenue this year",
    "Show monthly revenue trend for the last 12 months",
    "Why did South India revenue drop last quarter?",
    "What is the online share of revenue by region?",
    "What is our customer churn rate?",
]

st.set_page_config(page_title="Enterprise Data Analyst Agent", page_icon="📊", layout="wide")
st.title("📊 Enterprise Data Analyst Agent")
st.caption("Synthetic Indian retail data (not real). Every number in an answer comes from an executed SQL query.")

with st.sidebar:
    st.subheader("Try an example")
    for ex in EXAMPLES:
        if st.button(ex, use_container_width=True):
            st.session_state["question"] = ex
    st.divider()
    try:
        health = httpx.get(f"{API_URL}/health", timeout=5).json()
        st.success(f"API {health['status']} · DB {'up' if health['database'] else 'down'}")
    except Exception:
        st.error(f"API not reachable at {API_URL}")

question = st.text_input("Ask a business question", key="question", placeholder="e.g. Which region grew fastest last quarter?")

if st.button("Analyze", type="primary") and question.strip():
    with st.spinner("Planning, querying and verifying…"):
        try:
            resp = httpx.post(f"{API_URL}/analyze", json={"question": question}, timeout=300)
            resp.raise_for_status()
            result = resp.json()
        except Exception as exc:
            st.error(f"Request failed: {exc}")
            st.stop()

    if result["needs_clarification"]:
        st.info(f"**I need a clarification:** {result['clarification_question']}")
    else:
        if result.get("error"):
            st.error(result["error"])
        st.markdown(result["answer"])
        if result["answer"] and not result["grounded"] and not result.get("error"):
            st.warning("⚠️ Some numbers in this answer could not be verified against the query result.")
        elif result["grounded"]:
            st.caption("✅ All numbers verified against the query result.")

        if result["visualization"]:
            st.plotly_chart(go.Figure(result["visualization"]), use_container_width=True)
        if result["data"]:
            st.subheader("Data")
            st.dataframe(pd.DataFrame(result["data"], columns=result["columns"]), use_container_width=True)

    st.caption(f"Latency: {result['latency_ms'] / 1000:.1f}s")
    if result["sql"]:
        with st.expander("SQL"):
            st.code(result["sql"], language="sql")
    if result["analysis"]:
        with st.expander("Deterministic analysis"):
            st.json(result["analysis"])
    with st.expander("Agent trace"):
        st.json(result["trace"])

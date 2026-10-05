"""Streamlit dashboard: ask a business question, see the grounded answer, chart, data and SQL."""

from __future__ import annotations

import json
import os

import httpx
import pandas as pd
import plotly.io as pio
import streamlit as st

API_URL = os.environ.get("API_URL", "http://localhost:8000")
EXAMPLES = [
    "What was total revenue in FY2025-26?",
    "Revenue by region last quarter",
    "Why did South India revenue drop in the latest quarter?",
    "Top 5 product categories by revenue in FY2025-26",
    "What is the return rate by product category in FY2025-26?",
    "Monthly revenue trend for the last 6 months",
]

st.set_page_config(page_title="Enterprise Data Analyst Agent", page_icon="📊", layout="wide")
st.title("📊 Enterprise Data Analyst Agent")
st.caption("Synthetic Indian retail data (INR). Relative dates are anchored to the latest order date in the data, not today.")


@st.cache_data(ttl=30)
def health() -> dict:
    try:
        return httpx.get(f"{API_URL}/health", timeout=5).json()
    except Exception as exc:
        return {"status": "unreachable", "detail": str(exc)}


def ask(question: str) -> dict | str:
    try:
        r = httpx.post(f"{API_URL}/analyze", json={"question": question}, timeout=300)
        if r.status_code != 200:
            return f"API error {r.status_code}: {r.json().get('detail', r.text)}"
        return r.json()
    except httpx.HTTPError as exc:
        return f"Could not reach the API at {API_URL}: {exc}"


with st.sidebar:
    h = health()
    st.subheader("Status")
    st.write(f"API: **{h.get('status')}**")
    if h.get("anchor_date"):
        st.write(f"Anchor date: **{h['anchor_date']}**")
    if h.get("llm_configured") is False:
        st.warning("OPENROUTER_API_KEY is not configured on the API.")
    st.subheader("Examples")
    for ex in EXAMPLES:
        if st.button(ex, use_container_width=True):
            st.session_state["pending"] = ex

question = st.chat_input("Ask a business question…") or st.session_state.pop("pending", None)
if "pending_clarification" in st.session_state and question:
    question = f"{st.session_state.pop('pending_clarification')} — {question}"

if question:
    st.chat_message("user").write(question)
    with st.chat_message("assistant"):
        with st.spinner("Analysing…"):
            res = ask(question)
        if isinstance(res, str):
            st.error(res)
        elif res["needs_clarification"]:
            st.info(res["clarification_question"])
            st.session_state["pending_clarification"] = question
        else:
            st.markdown(res["answer"])
            c1, c2, c3 = st.columns(3)
            c1.metric("Grounded", "✅ yes" if res["grounded"] else "⚠️ unverified")
            c2.metric("Rows", len(res["data"]))
            c3.metric("Latency", f"{res['latency_ms'] / 1000:.1f}s")
            if res["visualization"]:
                st.plotly_chart(pio.from_json(json.dumps(res["visualization"])), use_container_width=True)
            if res["data"]:
                st.dataframe(pd.DataFrame(res["data"], columns=res["columns"]), use_container_width=True)
            with st.expander("SQL"):
                st.code(res["sql"] or "", language="sql")
            with st.expander("Analysis (deterministic Pandas)"):
                st.json(res["analysis"])
            with st.expander("Trace"):
                st.json(res["trace"])

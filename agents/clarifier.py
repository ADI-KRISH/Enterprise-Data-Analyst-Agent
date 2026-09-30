"""Ask a clarifying question instead of guessing (CLAUDE.md rule 4).

Reached when `understand` flags an undefined metric / unknown entity / ambiguous period.
The fast LLM phrases the question; if it fails, a templated question is used.
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from agents.prompting import DATA_NOTICE, data_block
from agents.understand import kpi_catalog
from app import llm

NODE = "clarifier"


class Clarification(BaseModel):
    question: str


SYSTEM_PROMPT = f"""You help a business user rephrase a question for a read-only sales \
analytics assistant. The question could not be answered without guessing. Write ONE short, \
polite clarifying question (max 2 sentences) that names what is missing and offers the \
closest metrics that ARE available from the KPI list. Do not include any numbers or data.

{DATA_NOTICE}"""


def template(state: dict) -> str:
    missing = ", ".join(state.get("undefined_metrics") or []) or "part of your question"
    available = ", ".join(k["title"] for k in kpi_catalog())
    return (f"I couldn't find a definition for {missing} in the available data. "
            f"Could you rephrase using one of these metrics: {available}?")


def run(state: dict, trace: list[dict]) -> dict:
    context = {
        "undefined_metrics": state.get("undefined_metrics") or [],
        "reason": state.get("clarification_reason"),
        "available_kpis": [k["title"] for k in kpi_catalog()],
        "time_range": (state.get("time_range") or {}).get("label"),
    }
    messages = [
        SystemMessage(SYSTEM_PROMPT),
        HumanMessage(data_block("user_question", state["question"]) + "\n" + data_block("context", context)),
    ]
    try:
        out = llm.invoke_structured(llm.get_llm_for_node(NODE, json_mode=True), messages, Clarification,
                                    node=NODE, trace=trace)
        question = out.question.strip() or template(state)
    except Exception:  # noqa: BLE001 - fall back to our own wording
        question = template(state)
    return {"needs_clarification": True, "clarification_question": question, "answer": question}

# CLAUDE.md — Enterprise Data Analyst Agent

This file is read by Claude Code at the start of every session. Keep it accurate.

## What we are building
An AI analytics agent: natural-language business question → intent/plan → schema + KPI retrieval (RAG) → SQL generation → SQL validation → read-only PostgreSQL execution → result verification → Pandas analysis → chart → grounded business answer. Exposed via FastAPI (`POST /analyze`) and a Streamlit dashboard. Target: working end-to-end in one day.

## Non-negotiable rules
1. **No invented numbers.** Every number in a final answer must come from an executed SQL result or a deterministic Pandas computation on that result. The response LLM only receives structured results, never raw freedom to estimate. A post-check (`agents/grounding.py`) extracts numbers from the answer and verifies each against the result/computed values (tolerance for rounding, %, and lakh/crore formatting). Ungrounded answers are regenerated once, then returned with a warning flag.
2. **Read-only database access.** The agent connects as role `analyst_ro` (SELECT only on schema `analytics`). Never use the admin/seed credentials in agent code.
3. **Validate every SQL before execution** (`agents/sql_validator.py`, using `sqlglot`, dialect `postgres`):
   - exactly one statement; top-level must be SELECT (WITH … SELECT allowed)
   - reject INSERT/UPDATE/DELETE/MERGE/DROP/ALTER/CREATE/TRUNCATE/GRANT/COPY/CALL/DO, `pg_*` functions, `;` chaining, comments containing statements
   - every table and column must exist in the allow-listed schema (`database/allowlist.yaml`)
   - enforce `LIMIT` (default 1000) if missing
   - dry-run with `EXPLAIN` before real execution
   - DB session sets `statement_timeout = 15s` and `default_transaction_read_only = on`
4. **Ask for clarification** instead of guessing when a metric/entity/time period is not defined by the data or KPI docs.
5. **Honest reporting.** Never write accuracy, latency, or evaluation figures into README/docs unless produced by `eval/run_eval.py` and saved in `eval/results/`. Do not claim production deployment.
6. Relative dates ("last quarter", "this month") resolve against `ANCHOR_DATE` = max(order_date) in the data, not the system clock. Document this in `retrieval/docs/reporting_policies.md`.

## Stack
Python 3.11 · LangGraph · LangChain (`langchain-openai` `ChatOpenAI` → OpenRouter, see below) · PostgreSQL 16 · SQLAlchemy + psycopg · sqlglot · Pandas · ChromaDB (local persistent) + fastembed (local embeddings, no API key) · Plotly · FastAPI + Uvicorn · Streamlit · pytest · Docker Compose.

### LLM provider (OpenRouter, OpenAI-compatible) — `app/llm.py`
- `ChatOpenAI(base_url="https://openrouter.ai/api/v1", temperature=0, timeout=LLM_TIMEOUT_S=60)`, key `OPENROUTER_API_KEY`.
- `get_llm(kind: "strong" | "fast")`: `LLM_MODEL_STRONG` (default `nvidia/nemotron-3-ultra-550b-a55b:free`) / `LLM_MODEL_FAST` (default `qwen/qwen3.8-27b:free`), each `.with_fallbacks([LLM_MODEL_FALLBACK])` (default `nvidia/nemotron-3-super-120b-a12b:free`).
- Node tiers (`NODE_LLM_KIND`): planner, sql_generator → strong; understand, clarifier, responder → fast.
- Reasoning: `extra_body={"reasoning": {"effort": "low", "exclude": True}}` so thinking never lands in content; `<think>` blocks are also stripped defensively.
- HTTP 429 → exponential backoff with jitter, max 3 retries (then fallback). Client-level retries are disabled to avoid double retrying.
- Structured output: JSON mode (`response_format={"type":"json_object"}`) + Pydantic validation via `invoke_structured`, one repair retry on parse/validation failure, then `StructuredOutputError`.
- Every call appends `{"node", "event": "llm_call", "model", "latency_ms", ...}` to `state.trace`; `model` is the model that actually answered (from the response), so fallbacks are visible.

## Repository layout
```
enterprise-data-analyst-agent/
├── app/            main.py (FastAPI), graph.py (LangGraph), state.py, config.py, llm.py, audit.py
├── agents/         understand.py, planner.py, sql_generator.py, sql_validator.py,
│                   executor.py, verifier.py, responder.py, grounding.py, clarifier.py
├── retrieval/      docs/ (kpi_definitions.md, data_dictionary.md, reporting_policies.md),
│                   build_index.py, retriever.py
├── database/       schema.sql, roles.sql, seed.py, allowlist.yaml
├── analytics/      analysis.py (growth, share, top_n, period_compare, driver_breakdown)
├── visualization/  charts.py (returns Plotly JSON spec; chart choice from result shape + intent)
├── dashboard/      streamlit_app.py
├── eval/           questions.yaml (question + gold SQL), run_eval.py, results/
├── tests/
├── docker-compose.yml, Dockerfile, requirements.txt, .env.example, README.md
```

## Data model (schema `analytics`, star schema, synthetic Indian retail)
- `dim_date`(date_key, date, day, month, month_name, quarter, year, fiscal_quarter, fiscal_year) — Indian FY (Apr–Mar)
- `dim_region`(region_id, region_name: North/South/East/West/Central India, zone)
- `dim_store`(store_id, store_name, city, state, region_id)
- `dim_customer`(customer_id, name, segment: Consumer/Corporate/SMB, city, state, region_id, signup_date)
- `dim_product`(product_id, product_name, category, sub_category, brand, unit_cost, list_price)
- `fact_orders`(order_id, order_date, customer_id, store_id, channel: Online/Store, status: Completed/Returned/Cancelled)
- `fact_order_items`(order_item_id, order_id, product_id, quantity, unit_price, discount_pct, line_revenue, line_cost)

Seed: `database/seed.py`, fixed random seed, ~24 months, ~50k orders, INR amounts, realistic seasonality (Diwali/festive peaks). Inject documented, explainable patterns (e.g. South India revenue dip in the latest quarter driven by one category + a store closure) so "why" questions have real answers. Record every injected pattern in `database/SEED_NOTES.md` — it is synthetic data and README must say so.

## Agent state (app/state.py)
question, intent, entities, metrics, filters, time_range, retrieved_context, plan, sql, validation_errors, sql_attempts (max 3), result_df (records + columns), verification, analysis, chart_spec, answer, grounded (bool), needs_clarification, clarification_question, trace (list of node events), timings.

## Graph
understand → retrieve → (clarify? → END) → plan → generate_sql → validate ⟲ (on error, back to generate_sql with errors, max 3) → execute → verify ⟲ (empty/irrelevant → generate_sql once) → analyze → visualize → respond → grounding_check ⟲ (regenerate once) → END

## API contract
`POST /analyze` {"question": str} → {"answer", "sql", "data", "columns", "visualization" (Plotly JSON or null), "analysis", "grounded", "needs_clarification", "clarification_question", "trace", "latency_ms"}
Also `GET /health`, `GET /schema`. Every request is logged to `logs/audit.jsonl` (question, sql, validation result, row count, latency, grounded).

## Commands
- `docker compose up -d db` → start Postgres (seeds on first run)
- `python -m retrieval.build_index` → build vector index
- `uvicorn app.main:app --reload` → API on :8000
- `streamlit run dashboard/streamlit_app.py` → UI on :8501
- `pytest -q` → tests
- `python -m eval.run_eval` → evaluation, writes `eval/results/<timestamp>.json`

## Working style for Claude Code
- Work phase by phase; after each phase run tests / a smoke check, then `git commit` with a clear message.
- Prefer small, typed, testable functions. Pydantic models for LLM structured outputs (JSON mode + `app.llm.invoke_structured`, not `with_structured_output`).
- Keep secrets in `.env` (never commit). Provide `.env.example`.
- If something in this file becomes untrue, update this file in the same commit.

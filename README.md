# Enterprise Data Analyst Agent

An AI analytics agent that turns a natural-language business question into a grounded answer:

```
question → understand → retrieve (schema + KPI docs, RAG) → clarify? → plan → generate SQL
        → validate (sqlglot) → read-only execute (PostgreSQL) → verify → Pandas analysis
        → chart (Plotly) → answer → grounding check
```

Served by FastAPI (`POST /analyze`) and a Streamlit dashboard.

> **The data is synthetic.** `database/seed.py` generates ~50k orders of fake Indian retail data
> (INR, fixed RNG seed) with documented injected patterns — see [`database/SEED_NOTES.md`](database/SEED_NOTES.md).
> This is a project/demo build; it has not been deployed to production.

## Safety design

| Concern | Mechanism |
|---|---|
| No invented numbers | The responder only receives structured results. `agents/grounding.py` extracts every number from the answer and checks it against the SQL result and Pandas computations (rounding, `%`, lakh/crore tolerant). Ungrounded answers are regenerated once, then returned with `grounded=false` and a visible warning. |
| Read-only DB | Agent connects as `analyst_ro` (SELECT on schema `analytics` only); session sets `statement_timeout=15s`, `default_transaction_read_only=on`. Admin credentials are used only by `database/seed.py`. |
| SQL validation | `agents/sql_validator.py` (sqlglot, postgres): one statement, SELECT/WITH only, no DML/DDL/`pg_*`/statement-bearing comments, tables+columns allow-listed (`database/allowlist.yaml`), `LIMIT` enforced, then `EXPLAIN` dry-run before execution. The *normalised* SQL is what runs. |
| Clarify, don't guess | Undefined metrics/entities/periods (churn, CLV, forecasts, marketing ROI…) trigger a clarifying question instead of SQL. |
| Relative dates | "last quarter", "this month" resolve against `ANCHOR_DATE = MAX(order_date)`, computed in code (`agents/time_ranges.py`), never the system clock. Indian fiscal year (Apr–Mar). |

## Quick start

```bash
cp .env.example .env            # set passwords and OPENROUTER_API_KEY
pip install -r requirements.txt

docker compose up -d db         # Postgres 16; creates schema + analyst_ro role on first run
python -m database.seed         # loads synthetic data (uses the admin URL)
python -m retrieval.build_index # builds the Chroma index (also built lazily on first query)

uvicorn app.main:app --reload   # API    → http://localhost:8000  (docs at /docs)
streamlit run dashboard/streamlit_app.py   # UI → http://localhost:8501
```

Or run everything in containers: `docker compose --profile app up --build` (then seed once with
`python -m database.seed` from the host).

### LLM provider
OpenRouter via `langchain-openai` (`app/llm.py`). Models are configurable (`LLM_MODEL_STRONG`,
`LLM_MODEL_FAST`, `LLM_MODEL_FALLBACK`); defaults are free-tier models. Planner and SQL generator use the
strong tier; understand, clarifier and responder use the fast tier. 429s are retried with backoff, then the
fallback model is used; the model that actually answered is recorded in each trace event.

### Embeddings
`EMBEDDER=auto` uses `fastembed` (local ONNX, no API key; downloads `BAAI/bge-small-en-v1.5` from
Hugging Face on first use). If that is unavailable it falls back to an offline hashed bag-of-words embedder
(lexical, lower quality) so retrieval still works; the active embedder is part of the Chroma collection name.

## API

`POST /analyze` `{"question": "..."}` →
`{answer, sql, data, columns, visualization, analysis, grounded, needs_clarification, clarification_question, trace, latency_ms}`.
`GET /health`, `GET /schema`. Every request is appended to `logs/audit.jsonl`
(question, sql, validation result, row count, latency, grounded).

## Tests and evaluation

```bash
pytest -q                 # offline unit tests + DB integration tests (skipped if Postgres is down)
python -m eval.run_eval   # runs eval/questions.yaml through the real agent; needs OPENROUTER_API_KEY
```

The test suite uses a scripted fake LLM for the graph tests, so it exercises routing, validation, retries,
execution, analysis, charting and grounding — but **not live model quality**.
Accuracy / latency figures are deliberately not reported here: they are only valid when produced by
`eval/run_eval.py` against a real model, which writes `eval/results/<timestamp>.json`.

## Layout

```
app/        FastAPI (main.py), LangGraph (graph.py), state, config, llm, audit
agents/     understand, planner, sql_generator, sql_validator, executor, verifier, responder,
            grounding, clarifier, time_ranges
retrieval/  docs/ (KPI definitions, data dictionary, reporting policies), build_index, retriever, embedding
database/   schema.sql, roles.sql(+roles.sh), seed.py, allowlist.yaml, SEED_NOTES.md
analytics/  deterministic Pandas analysis      visualization/  Plotly chart selection
dashboard/  Streamlit UI                       eval/           questions + gold SQL + runner
```

## Known limitations
- Live LLM behaviour with the default free models is unmeasured until you run the eval with your key.
- Sales "region" is the customer's region by default; online orders carry a fulfilling store.
- The grounding check is numeric: it cannot verify qualitative claims, and a coincidental match of a wrong
  number to some value in the data would pass.

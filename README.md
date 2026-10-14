# Multi-Agent Workflow Automation

A coordinated multi-agent system that triages incoming requests, routes work
across specialized agents, and drafts actions — with a human-in-the-loop
approval gate and a full audit trail persisted to PostgreSQL (SQLite fallback
for local dev).

## Architecture

```
                ┌─────────────────────┐
                │   triage_agent      │  classify request + extract entities
                │ (Triage Specialist) │  (ticket IDs, emails)
                └─────────┬───────────┘
                          ▼
                ┌─────────────────────┐
                │  research_agent     │  gather context via tools:
                │ (Context Research)  │  lookup_ticket, search_knowledge_base
                └─────────┬───────────┘  (retry once, then fall back)
                          ▼
                ┌─────────────────────┐
                │  reviewer_agent     │  draft action; flag steps with
                │ (Action Reviewer)   │  confidence < 0.75 for human review
                └─────────┬───────────┘
                          ▼
                ┌─────────────────────┐
                │   human_approver    │  HUMAN-IN-THE-LOOP gate: prompt or
                │  (approval gate)    │  auto-approve (always logged)
                └─────────┬───────────┘
                          ▼
              audit trail → PostgreSQL / SQLite
```

Every agent step (agent name, action, output) is recorded on the run and
persisted to the `agent_steps` table, ordered and linked to its
`workflow_runs` row — so any run can be audited or replayed after the fact.

## Two backends, one workflow

| Backend | When | What it is |
|---|---|---|
| `StubBackend` (default) | `DRY_RUN=true` or no `OPENAI_API_KEY` | Deterministic rule-based agents. Zero network, zero keys. Powers tests and offline demos. |
| `CrewAIBackend` | `OPENAI_API_KEY` set | Real CrewAI agents (`Agent`/`Task`/`Crew`, sequential) driven by `gpt-4o-mini`, with tool-calling agents. |

Both backends log identical audit-trail shapes, so switching backends never
changes the persistence or approval code paths.

## Setup

```bash
git clone https://github.com/andra-jasper/multi-agent-workflow-.git
cd multi-agent-workflow-
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then edit .env
```

### Environment variables

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | Only for real mode | LLM key for the CrewAI backend. |
| `DATABASE_URL` | No | Audit-trail DB. `postgresql://user:pw@host:5432/workflows` for Postgres; defaults to local SQLite file `./workflow_runs.db`. |
| `DRY_RUN` | No | `true` → deterministic offline stub agents. Auto-selected when no API key is set. |
| `HUMAN_APPROVAL` | No | `prompt` asks for approval on stdin before execution; default `auto` (decision still logged). Dry runs never block. |

## Run it

```bash
# Offline demo (no key, no network needed)
DRY_RUN=true python src/main.py run "Ticket #1234: login page broken, draft a customer response"

# Real CrewAI agents (needs OPENAI_API_KEY)
python src/main.py run "Summarize ticket #1234 and draft a response"

# Interactive human approval gate
HUMAN_APPROVAL=prompt python src/main.py run "Ticket #1234: refund this order"
```

## Tests

```bash
DRY_RUN=true python -m pytest tests/ -q
```

16 tests cover: stub triage classification + entity extraction, tool use and
step logging, low-confidence reviewer flags, full end-to-end workflow runs
(including determinism), approval-step logging, and SQLite persistence
round-trips with audit-trail ordering.

## Project structure

```
multi-agent-workflow-/
├── .github/workflows/ci.yml   # lint + pytest on every push
├── src/
│   ├── __init__.py
│   ├── main.py                # CLI, orchestration, human-in-the-loop gate
│   ├── agents.py              # role definitions, StubBackend, CrewAIBackend
│   ├── config.py              # env-based settings
│   └── persistence.py         # SQLAlchemy audit-trail models
├── tests/
│   └── test_workflow.py       # 16 tests, all offline
├── requirements.txt
├── .env.example
└── README.md
```

## Notes

- The stub ticket/knowledge-base tools return deterministic demo data (see
  `lookup_ticket` / `search_knowledge_base` in `src/agents.py`); point them at
  real systems for production use.
- PostgreSQL support is implemented via SQLAlchemy + `psycopg` (v3); the
  test suite exercises the same code path against SQLite.

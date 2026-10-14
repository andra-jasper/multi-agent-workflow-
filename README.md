# Multi-Agent Workflow Automation

A coordinated multi-agent system built with CrewAI that triages incoming requests, routes work across specialized agents, and drafts actions — with human-in-the-loop checks and a full audit history.

> Portfolio project demonstrating multi-agent orchestration, context engineering, and reliable automation design.

## Features

- **Specialized agents** — triage, research, and action-drafting agents with clear roles
- **Tool routing** — context-aware routing with retry/fallback logic to reduce hallucinations
- **Human-in-the-loop** — approval checkpoints before consequential actions
- **Audit trail** — every run persisted to PostgreSQL for review and replay
- **CI/CD** — GitHub Actions for lint, test, and repeatable runs

## Tech Stack

`Python` `CrewAI` `PostgreSQL` `GitHub Actions`

## Quickstart

```bash
# 1. Clone and enter the project
git clone https://github.com/andra-jasper/multi-agent-workflow-.git
cd multi-agent-workflow-

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure
cp .env.example .env   # add DATABASE_URL and your LLM API key

# 4. Run the workflow on a sample request
python src/main.py run "Summarize ticket #1234 and draft a response"
```

## Project Structure

```
multi-agent-workflow-/
├── .github/
│   └── workflows/
│       └── ci.yml       # lint + smoke test on every push
├── src/
│   ├── __init__.py
│   └── main.py          # Agent definitions + workflow entrypoint (skeleton)
├── requirements.txt
├── .env.example
└── README.md
```

## How It Works

1. **Triage agent** classifies the request and extracts entities.
2. **Specialist agents** gather context via tools.
3. **Reviewer agent** drafts the action and flags low-confidence steps.
4. A **human approver** confirms before anything consequential is executed.
5. Run history lands in PostgreSQL for auditing and replay.

## Note

This is a portfolio skeleton showing agent architecture and engineering practices. Wire up your own tools, prompts, and database to run it end to end.

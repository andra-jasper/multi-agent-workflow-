"""Multi-Agent Workflow Automation.

Triage -> gather context -> draft action, with a human-in-the-loop
approval gate and a full audit trail persisted to PostgreSQL/SQLite.

Usage:
    python src/main.py run "Summarize ticket #1234 and draft a response"

Environment (see .env.example):
    OPENAI_API_KEY   LLM key for the real CrewAI backend.
    DATABASE_URL     e.g. postgresql://user:pw@localhost:5432/workflows
                     (SQLite file fallback when unset).
    DRY_RUN=true     Deterministic offline stub agents; no network, no key.
    HUMAN_APPROVAL   "prompt" asks for approval on stdin; default "auto"
                     (always logged). Dry runs never block.
"""

import json
import os
import sys

# Make `from src import ...` work whether this file is run as
# `python src/main.py`, `python -m src.main`, or imported by tests.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src import agents, config, persistence  # noqa: E402


class WorkflowRun:
    """A single run of the multi-agent workflow."""

    def __init__(self, request_text: str):
        self.request_text = request_text
        self.steps: list[dict] = []
        self.run_id: str | None = None

    def log(self, agent: str, action: str, output: str) -> None:
        """Record one agent step for the audit trail."""
        self.steps.append({"agent": agent, "action": action, "output": output})


def _backend():
    return agents.get_backend()


def triage(run: WorkflowRun) -> dict:
    """Classify the request and extract entities.

    Returns a dict like {"category": ..., "entities": {...}}.
    """
    return _backend().triage(run)


def gather_context(run: WorkflowRun, triage_result: dict) -> dict:
    """Have specialist agents gather context via tools (with retry/fallback)."""
    return _backend().gather_context(run, triage_result)


def draft_action(run: WorkflowRun, context: dict) -> str:
    """Draft the action; the reviewer flags low-confidence steps."""
    return _backend().draft_action(run, context)


def request_approval(run: WorkflowRun, action: str) -> bool:
    """Human-in-the-loop gate before anything consequential is executed.

    - ``HUMAN_APPROVAL=prompt``: asks for approval on stdin.
    - otherwise (default): auto-approves. Dry runs always auto-approve
      and never block on input.

    The decision is always recorded as an audit-trail step, so approvals
    are reviewable after the fact.
    """
    mode = config.get_approval_mode()
    if config.is_dry_run():
        mode = "auto"  # automated runs must never block on stdin

    if mode == "prompt":
        print("\n--- Drafted action awaiting human approval ---")
        print(action)
        print("----------------------------------------------")
        try:
            answer = input("Approve execution? [y/N]: ").strip().lower()
        except EOFError:
            answer = ""
        approved = answer in {"y", "yes"}
    else:
        approved = True

    run.log(
        "human_approver",
        "approval_decision",
        json.dumps({"mode": mode, "approved": approved}),
    )
    return approved


def persist_run(run: WorkflowRun) -> str:
    """Persist the run's audit trail. Returns the database run id."""
    run_id = persistence.persist_run(run)
    run.run_id = run_id
    return run_id


def run_workflow(request_text: str) -> str:
    """Run the full workflow and return the drafted action."""
    run = WorkflowRun(request_text)
    triage_result = triage(run)
    context = gather_context(run, triage_result)
    action = draft_action(run, context)

    # Human-in-the-loop: approval happens here before execution.
    if not request_approval(run, action):
        print("Action REJECTED by human approver; nothing was executed.")
        persist_run(run)
        return action

    persist_run(run)
    return action


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "run":
        print(run_workflow(" ".join(sys.argv[2:])))
    else:
        print('Usage: python src/main.py run "<request text>"')

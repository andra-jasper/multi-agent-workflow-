"""Multi-Agent Workflow Automation.

Portfolio skeleton: a CrewAI-based workflow where specialized agents
triage a request, gather context, and draft an action — with
human-in-the-loop approval and a PostgreSQL audit trail.

Extend the marked TODO sections with your own tools, prompts, and
database connection.
"""

import sys

# NOTE: crewai is an optional dependency for the skeleton; the agent
# definitions below degrade gracefully if it is not installed.


class WorkflowRun:
    """A single run of the multi-agent workflow."""

    def __init__(self, request_text: str):
        self.request_text = request_text
        self.steps: list[dict] = []

    def log(self, agent: str, action: str, output: str) -> None:
        """Record one agent step for the audit trail."""
        self.steps.append({"agent": agent, "action": action, "output": output})


def triage(run: WorkflowRun) -> dict:
    """Classify the request and extract entities.

    Returns a dict like {"category": ..., "entities": [...]}.
    """
    # TODO: implement with a CrewAI triage agent.
    raise NotImplementedError("Triage agent not yet implemented")


def gather_context(run: WorkflowRun, triage_result: dict) -> dict:
    """Have specialist agents gather context via tools."""
    # TODO: implement specialist agents with tool routing + retries.
    raise NotImplementedError("Specialist agents not yet implemented")


def draft_action(run: WorkflowRun, context: dict) -> str:
    """Draft the action and flag low-confidence steps for review."""
    # TODO: implement reviewer agent.
    raise NotImplementedError("Reviewer agent not yet implemented")


def persist_run(run: WorkflowRun) -> None:
    """Persist the run's audit trail to PostgreSQL."""
    # TODO: connect via DATABASE_URL and insert run.steps.
    raise NotImplementedError("Persistence not yet implemented")


def run_workflow(request_text: str) -> str:
    """Run the full workflow and return the drafted action."""
    run = WorkflowRun(request_text)
    triage_result = triage(run)
    context = gather_context(run, triage_result)
    action = draft_action(run, context)
    # Human-in-the-loop: approval happens here before execution.
    persist_run(run)
    return action


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "run":
        print(run_workflow(" ".join(sys.argv[2:])))
    else:
        print('Usage: python src/main.py run "<request text>"')

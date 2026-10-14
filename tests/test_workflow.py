"""End-to-end tests. Everything runs against the deterministic stub backend
(DRY_RUN=true, in-memory SQLite): zero network, zero API keys."""

import json

import pytest
from sqlalchemy import create_engine

from src import agents, config, persistence
from src.main import WorkflowRun, run_workflow


@pytest.fixture(autouse=True)
def stub_env(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("HUMAN_APPROVAL", "auto")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")


@pytest.fixture()
def memory_engine():
    return create_engine("sqlite:///:memory:")


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------

def test_stub_backend_selected_without_api_key():
    assert config.is_dry_run()
    assert isinstance(agents.get_backend(), agents.StubBackend)


def test_explicit_dry_run_wins_even_with_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("DRY_RUN", "true")
    assert isinstance(agents.get_backend(), agents.StubBackend)


# ---------------------------------------------------------------------------
# Triage
# ---------------------------------------------------------------------------

def test_triage_classifies_support_ticket():
    run = WorkflowRun("Ticket #1234: login page is broken, please help")
    result = agents.StubBackend().triage(run)
    assert result["category"] == "support_ticket"
    assert result["entities"]["ticket_ids"] == ["1234"]
    assert run.steps and run.steps[0]["agent"] == "triage_agent"


def test_triage_classifies_research_request():
    run = WorkflowRun("Please summarize the Q3 performance report")
    result = agents.StubBackend().triage(run)
    assert result["category"] == "research_request"


def test_triage_extracts_emails():
    run = WorkflowRun("Send an update to jane@example.com when done")
    result = agents.StubBackend().triage(run)
    assert result["category"] == "action_request"
    assert result["entities"]["emails"] == ["jane@example.com"]


def test_triage_defaults_to_general():
    run = WorkflowRun("hello there")
    result = agents.StubBackend().triage(run)
    assert result["category"] == "general"
    assert result["entities"] == {"ticket_ids": [], "emails": []}


# ---------------------------------------------------------------------------
# Context gathering + drafting
# ---------------------------------------------------------------------------

def test_gather_context_uses_tools_and_logs_steps():
    run = WorkflowRun("Ticket #42 is down, investigate")
    backend = agents.StubBackend()
    triage_result = backend.triage(run)
    context = backend.gather_context(run, triage_result)
    assert context["category"] == "support_ticket"
    assert context["confidence"] == 0.90  # entities present -> high confidence
    tool_steps = [s for s in run.steps if s["action"].startswith("tool:")]
    assert any(s["action"] == "tool:lookup_ticket" for s in tool_steps)
    assert any(s["action"] == "tool:search_knowledge_base" for s in tool_steps)
    ticket_call = next(s for s in tool_steps if s["action"] == "tool:lookup_ticket")
    assert json.loads(ticket_call["output"])["record"]["id"] == "42"


def test_draft_action_flags_low_confidence():
    run = WorkflowRun("hello there")  # no entities -> lower confidence
    backend = agents.StubBackend()
    triage_result = backend.triage(run)
    context = backend.gather_context(run, triage_result)
    assert context["confidence"] < 0.75
    action = backend.draft_action(run, context)
    assert "FLAGGED FOR HUMAN REVIEW" in action
    reviewer_steps = [s for s in run.steps if s["agent"] == "reviewer_agent"]
    assert reviewer_steps and json.loads(reviewer_steps[0]["output"])["flagged"] is True


def test_draft_action_ready_when_confident():
    run = WorkflowRun("Ticket #7 keeps crashing on submit, draft a response")
    backend = agents.StubBackend()
    context = backend.gather_context(run, backend.triage(run))
    action = backend.draft_action(run, context)
    assert "ready for human approval" in action
    assert "FLAGGED FOR HUMAN REVIEW" not in action


# ---------------------------------------------------------------------------
# Full workflow
# ---------------------------------------------------------------------------

def test_full_workflow_end_to_end():
    action = run_workflow("Ticket #1234: login page broken, draft a customer response")
    assert isinstance(action, str) and len(action) > 50
    assert "support_ticket" in action


def test_full_workflow_is_deterministic():
    a1 = run_workflow("Summarize ticket #99 and draft a response")
    a2 = run_workflow("Summarize ticket #99 and draft a response")
    assert a1 == a2


def test_full_workflow_logs_approval_step(monkeypatch):
    # Capture the run object by wrapping persist_run
    import src.main as main_mod

    captured = {}

    def spy(run):
        captured["run"] = run
        return "test-id"

    monkeypatch.setattr(main_mod, "persist_run", spy)
    main_mod.run_workflow("Ticket #5 is slow")
    agents_seen = [s["agent"] for s in captured["run"].steps]
    assert agents_seen[0] == "triage_agent"
    assert "research_agent" in agents_seen
    assert "reviewer_agent" in agents_seen
    assert agents_seen[-1] == "human_approver"
    approval = json.loads(captured["run"].steps[-1]["output"])
    assert approval == {"mode": "auto", "approved": True}


# ---------------------------------------------------------------------------
# Persistence (SQLite round-trip)
# ---------------------------------------------------------------------------

def test_persistence_round_trip(memory_engine):
    run = WorkflowRun("Ticket #1234: login broken")
    backend = agents.StubBackend()
    triage_result = backend.triage(run)
    context = backend.gather_context(run, triage_result)
    backend.draft_action(run, context)
    run.log("human_approver", "approval_decision",
            json.dumps({"mode": "auto", "approved": True}))

    run_id = persistence.persist_run(run, engine=memory_engine)
    assert run_id

    loaded = persistence.fetch_run(memory_engine, run_id)
    assert loaded is not None
    assert loaded["request_text"] == "Ticket #1234: login broken"
    assert loaded["status"] == "completed"
    assert len(loaded["steps"]) == len(run.steps)
    # Steps come back in order with identical contents
    for original, stored in zip(run.steps, loaded["steps"]):
        assert stored["agent"] == original["agent"]
        assert stored["action"] == original["action"]
        assert stored["output"] == original["output"]
    seqs = [s["seq"] for s in loaded["steps"]]
    assert seqs == sorted(seqs) == list(range(len(seqs)))


def test_audit_trail_agent_sequence(memory_engine):
    run = WorkflowRun("Research the outage and draft a summary")
    backend = agents.StubBackend()
    backend.draft_action(run, backend.gather_context(run, backend.triage(run)))
    run_id = persistence.persist_run(run, engine=memory_engine)
    loaded = persistence.fetch_run(memory_engine, run_id)
    agents_seen = [s["agent"] for s in loaded["steps"]]
    assert agents_seen[0] == "triage_agent"
    assert "research_agent" in agents_seen
    assert "reviewer_agent" in agents_seen


def test_fetch_missing_run_returns_none(memory_engine):
    persistence.init_db(memory_engine)
    assert persistence.fetch_run(memory_engine, "nope") is None


def test_lookup_ticket_is_deterministic():
    assert agents.lookup_ticket("1234") == agents.lookup_ticket("1234")
    assert agents.lookup_ticket("1234")["id"] == "1234"

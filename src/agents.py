"""Agent definitions and workflow backends.

Two backends implement the same three-stage workflow (triage ->
gather context -> draft action) behind one interface:

- :class:`StubBackend` -- deterministic, rule-based agents. Zero network,
  zero API keys, zero CrewAI dependency. Used when ``DRY_RUN=true`` or no
  ``OPENAI_API_KEY`` is set. Powers the test suite and offline demos.
- :class:`CrewAIBackend` -- real CrewAI agents driven by an OpenAI LLM.
  Requires ``crewai`` installed and ``OPENAI_API_KEY`` set.

Both backends record every agent step on the :class:`WorkflowRun`
so the audit trail is identical in shape either way.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from src import config

# ---------------------------------------------------------------------------
# Shared agent role definitions (used by both backends)
# ---------------------------------------------------------------------------

TRIAGE_AGENT = {
    "role": "Request Triage Specialist",
    "goal": "Classify each incoming request into exactly one category and "
    "extract the key entities (ticket IDs, emails, dates) with high precision.",
    "backstory": "You are the front door of an operations team. You have "
    "triaged thousands of support tickets, research asks, and action "
    "requests. You are terse, you never guess entities that are not in the "
    "text, and you always return a single category.",
}

RESEARCH_AGENT = {
    "role": "Context Research Specialist",
    "goal": "Gather the concrete facts needed to act on a triaged request, "
    "using the available tools. Prefer tool output over assumptions and "
    "retry a failing tool once before falling back.",
    "backstory": "You are a meticulous investigator. You query the ticket "
    "system and the knowledge base, cite what you found, and clearly mark "
    "anything you could not verify.",
}

REVIEWER_AGENT = {
    "role": "Action Reviewer",
    "goal": "Draft a clear, actionable response from the gathered context "
    "and explicitly flag any low-confidence step for human review.",
    "backstory": "You are a senior operator signing off on work. You would "
    "rather flag uncertainty than ship a confident-sounding mistake.",
}

CATEGORIES = ("support_ticket", "research_request", "action_request", "general")

_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    # Order matters: first match wins, so put the most specific first.
    "support_ticket": (
        "ticket", "issue", "bug", "error", "broken", "outage", "incident",
        "support", "help", "fix", "crash", "down",
    ),
    "research_request": (
        "research", "summarize", "summary", "analyse", "analyze", "analysis",
        "report", "investigate", "investigation", "compare", "overview",
    ),
    "action_request": (
        "draft", "write", "create", "schedule", "send", "prepare",
        "generate", "compose",
    ),
}

_TICKET_RE = re.compile(r"#(\d{2,})")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


# ---------------------------------------------------------------------------
# Deterministic stub tools (no network; safe to reuse in both backends)
# ---------------------------------------------------------------------------

def lookup_ticket(ticket_id: str) -> dict[str, Any]:
    """Return a deterministic ticket record for *ticket_id*.

    In a production deployment this would query the real ticketing system;
    the stub derives stable demo values from a hash so runs are repeatable.
    """
    digest = hashlib.md5(ticket_id.encode("utf-8"), usedforsecurity=False).hexdigest()
    priorities = ["P1", "P2", "P3", "P4"]
    statuses = ["open", "open", "in_progress", "open"]
    return {
        "id": ticket_id,
        "status": statuses[int(digest[:2], 16) % len(statuses)],
        "priority": priorities[int(digest[2:4], 16) % len(priorities)],
        "summary": f"Ticket #{ticket_id} pulled from the ticketing system.",
    }


def search_knowledge_base(query: str) -> list[str]:
    """Return deterministic knowledge-base snippets for *query*."""
    words = [w for w in re.findall(r"[a-zA-Z]{4,}", query.lower())][:3] or ["general"]
    topic = " ".join(words)
    return [
        f"KB article: '{topic}' handling procedure (rev 3).",
        f"KB article: '{topic}' troubleshooting checklist.",
    ]


def _stable_confidence(*parts: str) -> float:
    """Deterministic pseudo-confidence in [0.5, 0.95] from input text."""
    digest = hashlib.md5("|".join(parts).encode("utf-8"), usedforsecurity=False).hexdigest()
    return 0.50 + (int(digest[:4], 16) % 46) / 100.0


# ---------------------------------------------------------------------------
# Stub backend: deterministic, offline
# ---------------------------------------------------------------------------

class StubBackend:
    """Rule-based agents. No network, no keys, fully deterministic."""

    name = "stub"

    # -- stage 1: triage ----------------------------------------------------
    def triage(self, run) -> dict[str, Any]:
        text = run.request_text
        lowered = text.lower()
        category = "general"
        for candidate, keywords in _CATEGORY_KEYWORDS.items():
            if any(kw in lowered for kw in keywords):
                category = candidate
                break
        entities = {
            "ticket_ids": _TICKET_RE.findall(text),
            "emails": _EMAIL_RE.findall(text),
        }
        result = {"category": category, "entities": entities}
        run.log("triage_agent", "classify_request", json.dumps(result))
        return result

    # -- stage 2: gather context --------------------------------------------
    def gather_context(self, run, triage_result: dict[str, Any]) -> dict[str, Any]:
        findings: list[dict[str, Any]] = []

        for ticket_id in triage_result["entities"]["ticket_ids"]:
            record = lookup_ticket(ticket_id)
            findings.append({"tool": "lookup_ticket", "input": ticket_id, "result": record})
            run.log("research_agent", "tool:lookup_ticket",
                    json.dumps({"ticket_id": ticket_id, "record": record}))

        snippets = search_knowledge_base(run.request_text)
        findings.append({"tool": "search_knowledge_base",
                         "input": run.request_text[:80],
                         "result": snippets})
        run.log("research_agent", "tool:search_knowledge_base", json.dumps(snippets))

        has_entities = bool(triage_result["entities"]["ticket_ids"]
                            or triage_result["entities"]["emails"])
        confidence = 0.90 if has_entities else _stable_confidence(run.request_text)
        context = {
            "category": triage_result["category"],
            "entities": triage_result["entities"],
            "findings": findings,
            "confidence": round(confidence, 2),
        }
        run.log("research_agent", "summarize_context",
                json.dumps({"findings_count": len(findings), "confidence": context["confidence"]}))
        return context

    # -- stage 3: draft action ----------------------------------------------
    def draft_action(self, run, context: dict[str, Any]) -> str:
        category = context["category"]
        confidence = context["confidence"]
        finding_lines = []
        for f in context["findings"]:
            if f["tool"] == "lookup_ticket":
                r = f["result"]
                finding_lines.append(
                    f"- Ticket #{r['id']}: status={r['status']}, priority={r['priority']}.")
            else:
                finding_lines.append(f"- Knowledge base: {f['result'][0]}")
        findings_text = "\n".join(finding_lines) if finding_lines else "- No tool findings."

        low_confidence = confidence < 0.75
        action = (
            f"Drafted action for {category} request: \"{run.request_text[:100]}\"\n"
            f"Context gathered:\n{findings_text}\n"
            f"Reviewer confidence: {confidence:.2f}\n"
        )
        if low_confidence:
            action += ("FLAGGED FOR HUMAN REVIEW: low confidence "
                       f"({confidence:.2f} < 0.75). Verify entities before acting.\n")
        else:
            action += "Reviewer assessment: ready for human approval.\n"

        run.log("reviewer_agent", "draft_action",
                json.dumps({"confidence": confidence,
                            "flagged": low_confidence,
                            "action_preview": action[:200]}))
        return action


# ---------------------------------------------------------------------------
# Real backend: CrewAI agents with an OpenAI LLM
# ---------------------------------------------------------------------------

class CrewAIBackend:
    """CrewAI agents driven by an OpenAI LLM (requires OPENAI_API_KEY)."""

    name = "crewai"

    def __init__(self, api_key: str) -> None:
        try:
            import crewai  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "CrewAI backend selected but the 'crewai' package is not "
                "installed. Run: pip install -r requirements.txt"
            ) from exc
        if not api_key:
            raise RuntimeError("CrewAI backend requires OPENAI_API_KEY to be set.")
        self._crewai = crewai
        self._api_key = api_key

    # -- helpers ------------------------------------------------------------
    def _llm(self):
        return self._crewai.LLM(model="gpt-4o-mini", api_key=self._api_key)

    def _make_agent(self, role_def: dict[str, str], tools: list | None = None):
        return self._crewai.Agent(
            role=role_def["role"],
            goal=role_def["goal"],
            backstory=role_def["backstory"],
            llm=self._llm(),
            tools=tools or [],
            verbose=False,
        )

    def _ticket_tool(self):
        crewai = self._crewai

        class TicketLookupTool(crewai.tools.BaseTool):
            name: str = "lookup_ticket"
            description: str = ("Look up a support ticket by its numeric ID "
                                "(without the '#'). Returns status/priority/summary.")

            def _run(self, ticket_id: str) -> str:
                return json.dumps(lookup_ticket(ticket_id.strip().lstrip("#")))

        return TicketLookupTool()

    def _kb_tool(self):
        crewai = self._crewai

        class KnowledgeBaseTool(crewai.tools.BaseTool):
            name: str = "search_knowledge_base"
            description: str = ("Search the internal knowledge base for "
                                "procedures and troubleshooting checklists.")

            def _run(self, query: str) -> str:
                return json.dumps(search_knowledge_base(query))

        return KnowledgeBaseTool()

    def _run_crew(self, agent, task_description: str, expected_output: str) -> str:
        task = self._crewai.Task(
            description=task_description,
            expected_output=expected_output,
            agent=agent,
        )
        crew = self._crewai.Crew(
            agents=[agent],
            tasks=[task],
            process=self._crewai.Process.sequential,
            verbose=False,
        )
        return str(crew.kickoff())

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        """Best-effort parse of a JSON object embedded in LLM output."""
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
        return {"raw": text}

    # -- stage 1: triage ----------------------------------------------------
    def triage(self, run) -> dict[str, Any]:
        agent = self._make_agent(TRIAGE_AGENT)
        output = self._run_crew(
            agent,
            task_description=(
                "Classify this request into exactly one category: "
                f"{', '.join(CATEGORIES)}. Extract ticket_ids (numbers after "
                f"'#'), emails, and dates. Request: \"{run.request_text}\""
            ),
            expected_output=(
                'A JSON object like {"category": "<one of: '
                + ", ".join(CATEGORIES)
                + '>", "entities": {"ticket_ids": [...], "emails": [...]}}. '
                "JSON only."
            ),
        )
        result = self._extract_json(output)
        if "category" not in result or result["category"] not in CATEGORIES:
            result = {"category": "general",
                      "entities": {"ticket_ids": [], "emails": []},
                      "note": "LLM output unparseable; defaulted."}
        run.log("triage_agent", "classify_request", json.dumps(result))
        return result

    # -- stage 2: gather context --------------------------------------------
    def gather_context(self, run, triage_result: dict[str, Any]) -> dict[str, Any]:
        tools = [self._ticket_tool(), self._kb_tool()]
        agent = self._make_agent(RESEARCH_AGENT, tools=tools)
        entities = triage_result.get("entities", {})
        output = self._run_crew(
            agent,
            task_description=(
                f"Gather context for this {triage_result.get('category', 'general')} "
                f"request: \"{run.request_text}\". Known entities: "
                f"{json.dumps(entities)}. Use lookup_ticket for any ticket IDs "
                "and search_knowledge_base for procedures. If a tool fails, "
                "retry it once, then continue without it and say what is missing."
            ),
            expected_output=(
                "A JSON object like {\"findings\": [{\"tool\": ..., \"result\": ...}], "
                "\"confidence\": <0.0-1.0>}. JSON only."
            ),
        )
        parsed = self._extract_json(output)
        findings = parsed.get("findings", [])
        for f in findings:
            run.log("research_agent",
                    f"tool:{f.get('tool', 'unknown')}",
                    json.dumps(f.get("result", ""))[:2000])
        try:
            confidence = float(parsed.get("confidence", 0.6))
        except (TypeError, ValueError):
            confidence = 0.6
        context = {
            "category": triage_result.get("category", "general"),
            "entities": entities,
            "findings": findings,
            "confidence": round(max(0.0, min(1.0, confidence)), 2),
        }
        run.log("research_agent", "summarize_context",
                json.dumps({"findings_count": len(findings),
                            "confidence": context["confidence"]}))
        return context

    # -- stage 3: draft action ----------------------------------------------
    def draft_action(self, run, context: dict[str, Any]) -> str:
        agent = self._make_agent(REVIEWER_AGENT)
        action = self._run_crew(
            agent,
            task_description=(
                "Draft a clear, actionable response for this request: "
                f"\"{run.request_text}\". Context: {json.dumps(context)[:3000]} "
                "If reviewer confidence is below 0.75, start the draft with "
                "'FLAGGED FOR HUMAN REVIEW: low confidence'."
            ),
            expected_output=("The drafted action as plain text, with any "
                             "low-confidence flag on the first line."),
        )
        flagged = "FLAGGED FOR HUMAN REVIEW" in action
        run.log("reviewer_agent", "draft_action",
                json.dumps({"flagged": flagged, "action_preview": action[:200]}))
        return action


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------

def get_backend() -> StubBackend | CrewAIBackend:
    """Return the stub backend in dry-run / no-key mode, else CrewAI."""
    if config.is_dry_run():
        return StubBackend()
    return CrewAIBackend(config.get_openai_api_key())

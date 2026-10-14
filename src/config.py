"""Runtime configuration.

Every setting comes from the environment (see .env.example). Values are
read fresh on each call so tests can override them with monkeypatched
env vars without import-order tricks.
"""

import os


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        # python-dotenv is optional; env vars still work without it.
        pass


_load_dotenv_if_available()


def get_openai_api_key() -> str:
    """API key for the real CrewAI backend. Empty means 'not configured'."""
    return os.environ.get("OPENAI_API_KEY", "").strip()


def get_database_url() -> str:
    """PostgreSQL via DATABASE_URL; SQLite file fallback for local dev."""
    return os.environ.get("DATABASE_URL", "").strip() or "sqlite:///./workflow_runs.db"


def is_dry_run() -> bool:
    """True -> deterministic offline stub agents, zero network.

    Explicit DRY_RUN=true forces stub mode. With no OPENAI_API_KEY set,
    stub mode is also selected automatically so the workflow always runs.
    """
    explicit = os.environ.get("DRY_RUN", "").strip().lower()
    if explicit in {"1", "true", "yes", "on"}:
        return True
    return not get_openai_api_key()


def get_approval_mode() -> str:
    """'prompt' asks a human on stdin; anything else auto-approves (logged)."""
    mode = os.environ.get("HUMAN_APPROVAL", "auto").strip().lower()
    return "prompt" if mode == "prompt" else "auto"

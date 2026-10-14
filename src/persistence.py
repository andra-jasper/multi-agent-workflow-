"""Audit-trail persistence.

Every agent step of every workflow run is stored relationally:

- ``workflow_runs`` -- one row per run (request text, status, timestamp)
- ``agent_steps``   -- one row per agent step, ordered (agent, action, output)

PostgreSQL is used when ``DATABASE_URL`` is set (``psycopg2-binary`` driver);
otherwise a local SQLite file is used so the project runs with zero setup.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, create_engine, func
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

from src import config


class Base(DeclarativeBase):
    pass


class WorkflowRunRecord(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="completed")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    steps: Mapped[list["AgentStepRecord"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="AgentStepRecord.seq",
    )


class AgentStepRecord(Base):
    __tablename__ = "agent_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    agent: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    output: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    run: Mapped["WorkflowRunRecord"] = relationship(back_populates="steps")


def get_engine(database_url: str | None = None):
    """Create a SQLAlchemy engine for PostgreSQL or SQLite.

    ``postgresql://`` URLs use the psycopg (v3) driver; ``sqlite://`` works
    out of the box and is the default for local development and tests.
    """
    url = database_url or config.get_database_url()
    try:
        return create_engine(url)
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            f"Database driver missing for URL {url!r}: {exc}. "
            "Install it with: pip install -r requirements.txt"
        ) from exc


def init_db(engine) -> None:
    """Create tables if they do not exist (safe to call repeatedly)."""
    Base.metadata.create_all(engine)


def persist_run(run, engine=None, status: str = "completed") -> str:
    """Persist *run* (a WorkflowRun) and its steps. Returns the run id."""
    engine = engine or get_engine()
    init_db(engine)
    run_id = uuid.uuid4().hex
    with Session(engine) as session:
        record = WorkflowRunRecord(
            id=run_id,
            request_text=run.request_text,
            status=status,
        )
        for seq, step in enumerate(run.steps):
            record.steps.append(
                AgentStepRecord(
                    seq=seq,
                    agent=step["agent"],
                    action=step["action"],
                    output=step["output"],
                )
            )
        session.add(record)
        session.commit()
    return run_id


def fetch_run(engine, run_id: str) -> dict | None:
    """Load a run and its ordered steps back as plain dicts (used by tests)."""
    with Session(engine) as session:
        record = session.get(WorkflowRunRecord, run_id)
        if record is None:
            return None
        return {
            "id": record.id,
            "request_text": record.request_text,
            "status": record.status,
            "steps": [
                {"seq": s.seq, "agent": s.agent, "action": s.action, "output": s.output}
                for s in record.steps
            ],
        }

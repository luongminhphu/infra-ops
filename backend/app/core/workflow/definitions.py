"""Contract for workflows declared by modules (generic Job / Step engine in core).

A workflow is an ordered list of steps. Automatic steps call external systems from the worker;
manual steps wait for a person to confirm (optionally with input). Any step may ask for a typed
confirmation of the subject key (e.g. employee ID) before it runs.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.audit.service import Actor
    from app.core.workflow.models import Job, JobStep

OutcomeStatus = Literal["done", "waiting", "failed", "skipped"]


@dataclass
class StepOutcome:
    status: OutcomeStatus
    message: str = ""
    output: dict[str, Any] = field(default_factory=dict)
    # State before a change (kept for manual recovery – there is no automatic rollback).
    before_state: dict[str, Any] | None = None
    # For "waiting": when to check again.
    retry_in_seconds: int = 60


@dataclass
class StepContext:
    session: AsyncSession
    job: Job
    step: JobStep
    actor: Actor
    # Outputs of earlier steps, by step key.
    outputs: dict[str, dict[str, Any]]
    # Data submitted by the user when completing a manual step.
    input: dict[str, Any] = field(default_factory=dict)

    @property
    def params(self) -> dict[str, Any]:
        return self.job.params


StepFn = Callable[[StepContext], Awaitable[StepOutcome]]
PreviewFn = Callable[[StepContext], Awaitable[list[str]]]


@dataclass(frozen=True)
class StepDefinition:
    key: str
    title: str
    kind: Literal["auto", "manual"]
    description: str = ""
    # auto: performs the work; manual: validates the user's input when they mark it done.
    run: StepFn | None = None
    # Optional: prepare data when a manual step becomes active (e.g. read current assets).
    prepare: StepFn | None = None
    # Dry-run description of what the step will do (read-only).
    preview: PreviewFn | None = None
    # Require retyping the job's subject key (employee ID) before running.
    confirm: bool = False
    confirm_text: str = ""
    skippable: bool = True
    # Waiting longer than this fails the step (None = wait indefinitely).
    wait_timeout_seconds: int | None = None
    # Which external systems the step writes to (shown in the UI).
    writes: tuple[str, ...] = ()
    when: Callable[[dict[str, Any]], bool] = lambda _params: True
    # Default verification checklist (Managers edit it in the UI; see core.workflow.checklist).
    checklist: tuple[str, ...] = ()
    # Operators must attach evidence (screenshot, file, link or note) before confirming this step.
    evidence_required: bool = False


@dataclass(frozen=True)
class WorkflowDefinition:
    key: str
    title: str
    steps: Sequence[StepDefinition]
    view_permission: str
    execute_permission: str
    override_permission: str
    # Steps whose completion is required before the job may close (unless overridden).
    required_for_close: Sequence[str] = ()
    # Every step ends in "verify": an operator ticks the checklist (and attaches evidence where
    # required) before the next step runs.
    verify_steps: bool = False
    # Least privilege: when set, people without it see (and open) only the jobs assigned to them and the
    # unassigned ones they may take; the rest of the team's jobs stay out of their view.
    view_all_permission: str | None = None

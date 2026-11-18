"""Storage-neutral records for Nora. Business evidence remains owned by the brain."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Callable, Protocol, TypeVar


SCHEMA_VERSION = 1
MISSING_FIELDS = frozenset({"trigger", "performer", "input", "action", "result",
                            "purpose", "placement", "handoff", "tracking_record"})
T = TypeVar("T")


def aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("A timezone-aware clock is required")
    return value


class QuestionStatus(StrEnum):
    PENDING = "pending"
    PARTIAL = "partially_answered"
    AWAITING_PERSON = "awaiting_person"
    RESOLVED = "resolved"
    SUPERSEDED = "superseded"


class RoundStatus(StrEnum):
    PREPARED = "prepared"
    SENDING = "sending"
    ACCEPTED = "accepted"
    KNOWN_UNSENT = "known_unsent"
    UNKNOWN = "unknown"
    EXPIRED = "expired"
    EMPTY = "empty"


@dataclass(frozen=True)
class Route:
    company_id: str
    id: str
    revision: int
    account: str
    conversation: str
    founder_sender: str
    founder_actor_id: str
    enabled: bool = False
    group_name: str = ""
    responders: tuple = ()

    def __post_init__(self) -> None:
        if self.revision < 1 or not all((self.company_id, self.id, self.account,
                                       self.conversation, self.founder_sender,
                                       self.founder_actor_id)):
            raise ValueError("A complete verified route binding is required")
        people = []
        for item in self.responders:
            if not isinstance(item, (tuple, list)) or len(item) != 3 or not all(isinstance(part, str) and part.strip() for part in item):
                raise ValueError("Each responder needs a sender, actor and name")
            people.append((item[0].strip(), item[1].strip(), item[2].strip()))
        object.__setattr__(self, "responders", tuple(people))
        if self.group_name and (len(self.group_name) > 120 or not self.group_name.isprintable()):
            raise ValueError("configured_group_name_required")


@dataclass(frozen=True)
class Question:
    id: str
    company_id: str
    task_id: str
    task_version: int
    revision: int
    created_at: datetime
    context_key: str
    context_name: str
    activity_summary: str
    prompt: str
    source_url: str
    observation_ids: tuple[str, ...]
    missing_links: tuple[str, ...]
    answer_ids: tuple[str, ...] = ()
    conflict_ids: tuple[str, ...] = ()
    # Only evidence that changes THIS uncertainty's context, not every recurrence.
    # The brain must select these explicitly; observation_ids retains all support.
    revisit_evidence_ids: tuple[str, ...] = ()
    resend_request_id: str | None = None
    status: QuestionStatus = QuestionStatus.PENDING
    target_kind: str = "task"
    activity_url: str | None = None
    response_disposition: dict | None = None
    disposition_digest: str | None = None
    coverage: dict | None = None
    gap_kind: str | None = None
    evidence: tuple[dict, ...] = ()
    observed_context: dict | None = None
    known_facts: tuple[dict, ...] = ()
    issue_ids: tuple[str, ...] = ()
    issues: tuple[dict, ...] = ()
    application: dict | None = None
    privacy_disposition: str | None = None
    conflict: dict | None = None
    priority_hint: dict | None = None

    def __post_init__(self) -> None:
        aware(self.created_at)
        if not all((self.id, self.company_id, self.task_id, self.context_key,
                    self.context_name, self.activity_summary, self.prompt,
                    self.source_url, self.observation_ids, self.missing_links)):
            raise ValueError("Question needs an observed activity, source and missing link")
        if self.task_version < 1 or self.revision < 1:
            raise ValueError("Versions must be positive")
        if not set(self.missing_links) <= MISSING_FIELDS:
            raise ValueError("Missing links must use the brain's canonical field enum")
        if not set(self.revisit_evidence_ids) <= set(self.observation_ids):
            raise ValueError("Revisit evidence must be retained among supporting observations")
        if not isinstance(self.status, QuestionStatus):
            raise ValueError("Invalid question status")


@dataclass(frozen=True)
class RoundItem:
    label: str
    question: Question
    digest: str
    reminder: bool = False
    prior_ask_at: datetime | None = None
    why_chosen: dict | None = None
    evidence: tuple[dict, ...] = ()


@dataclass(frozen=True)
class Attempt:
    id: str
    started_at: datetime
    deadline: datetime
    outcome: RoundStatus = RoundStatus.SENDING
    finished_at: datetime | None = None
    message_id: str | None = None
    # A closed diagnostic code, never a subprocess argument/body/secret.
    diagnostic: str | None = None


@dataclass(frozen=True)
class Round:
    id: str
    slot: datetime
    route: Route
    items: tuple[RoundItem, ...]
    text: str
    content_hash: str
    status: RoundStatus
    attempts: tuple[Attempt, ...] = ()
    retry_after: datetime | None = None
    recovery_evidence_ids: tuple[str, ...] = ()
    decision_reason: str | None = None


@dataclass(frozen=True)
class InboundEvent:
    account: str
    conversation: str
    sender: str
    message_id: str
    received_at: datetime
    text: str
    reply_to: str | None = None
    media_type: str | None = None


@dataclass(frozen=True)
class ReceivedAnswer:
    id: str
    source_key: str
    source_hash: str
    company_id: str
    actor_id: str
    event: InboundEvent
    round_id: str | None
    candidate_question_ids: tuple[str, ...]
    question_ids: tuple[str, ...]
    question_versions: tuple[tuple[str, int, int], ...]
    correlation: str
    # Brain applies/re-evaluates this immutable record transactionally with its map.
    status: str = "awaiting_interpretation"
    handoff_scope: dict | None = None
    conversation_contexts: tuple[dict, ...] = ()
    responder_name: str = ""


@dataclass
class NoraState:
    """Company-scoped transaction view. Repository must persist it atomically.

    A backend may partition records internally. The view contains no observations,
    company secrets or work-map contents beyond the founder-visible question projection.
    """
    route: Route | None = None
    questions: dict[str, Question] = field(default_factory=dict)
    rounds: dict[str, Round] = field(default_factory=dict)
    # (question ID, semantic digest) -> reserving/accepted round ID.
    suppressions: dict[tuple[str, str], str] = field(default_factory=dict)
    inbox: dict[str, ReceivedAnswer] = field(default_factory=dict)
    # One bounded scheduled reminder for each unchanged pending gap/context.
    reminders: dict[tuple[str, str], str] = field(default_factory=dict)
    waiting_question_ids: tuple[str, ...] = ()
    freshness: dict = field(default_factory=dict)
    binding_request: dict | None = None
    pending_followup: dict | None = None


class Repository(Protocol):
    def transact(self, company_id: str, operation: Callable[[NoraState], T]) -> T:
        """Serialize or CAS concurrent writers; commit before returning the result.

        Operation changes the state and returns a result. It may be re-evaluated on CAS
        conflict, so it MUST be free of external effects. Exceptions roll back changes.
        A sending state must be durable before the sender starts. Engine deliberately
        invokes transport only after this method returns successfully.
        """
        ...


@dataclass(frozen=True)
class SendReceipt:
    account: str
    conversation: str
    message_id: str


class KnownUnsent(Exception):
    """Transport positively knows no external send started; a bounded retry is safe."""


class Sender(Protocol):
    def send(self, route: Route, text: str) -> SendReceipt: ...


Clock = Callable[[], datetime]


class ReplayConflict(ValueError):
    pass


class UnauthorizedInbound(ValueError):
    pass


@dataclass(frozen=True)
class TickResult:
    outcome: str
    next_scheduled_at: datetime
    round_id: str | None = None
    pending_count: int = 0
    overflow_count: int = 0

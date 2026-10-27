from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import threading
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, event, inspect, select, text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.types import String, Text, Integer


def uid():
    return uuid4().hex


def teaching_schema_id(tenant_id):
    return "teaching-schema:" + hashlib.sha256(tenant_id.encode()).hexdigest()[:40]


def external_review_schema_id(tenant_id):
    return "external-schema:" + hashlib.sha256(tenant_id.encode()).hexdigest()[:40]


def learning_preferences_schema_id(tenant_id):
    return "learning-schema:" + hashlib.sha256(tenant_id.encode()).hexdigest()[:40]


def homework_voice_schema_id(tenant_id):
    return "homework-voice-schema:" + hashlib.sha256(tenant_id.encode()).hexdigest()[:40]


def school_curriculum_schema_id(tenant_id):
    return "curriculum-schema:" + hashlib.sha256(tenant_id.encode()).hexdigest()[:40]


def chat_schema_id(tenant_id):
    return "chat-schema:" + hashlib.sha256(tenant_id.encode()).hexdigest()[:40]


def classroom_schema_id(tenant_id):
    return "classroom-schema:" + hashlib.sha256(tenant_id.encode()).hexdigest()[:40]


def family_schema_id(tenant_id):
    return "family-schema:" + hashlib.sha256(tenant_id.encode()).hexdigest()[:40]


def homework_ai_schema_id(tenant_id):
    return "homework-ai-schema:" + hashlib.sha256(tenant_id.encode()).hexdigest()[:40]


def teacher_models_schema_id(tenant_id):
    return "teacher-models-schema:" + hashlib.sha256(tenant_id.encode()).hexdigest()[:40]


def administration_schema_id(tenant_id):
    return "administration-schema:" + hashlib.sha256(tenant_id.encode()).hexdigest()[:40]


IMMUTABLE_RECORD_KINDS = {"teacher_model_revision", "teacher_model_approval", "homework_ai_config_revision", "homework_ai_approval", "homework_ai_session", "homework_ai_send", "family_event", "classroom_link_event", "academy_chat_command", "academy_chat_receipt", "academy_chat_proposal", "academy_chat_membership_event", "school_curriculum_run", "school_curriculum_publication", "knowledge_workflow_run", "homework_session", "homework_message", "homework_outcome", "student_voice_preference", "student_preference_revision", "student_agent_feedback", "submission", "teacher_profile_revision", "course_corpus", "corpus_approval", "external_resource_snapshot", "external_audit_agent_revision", "external_review_result", "external_publication"}
SEALED_JOB_KINDS = {"homework_ai_message", "classroom_operation", "academy_chat_attempt", "turn", "lesson_turn", "external_review_job", "voice_operation"}
IMMUTABLE_RECORD_KINDS.update({"admin_agent_revision", "admin_skill_revision", "admin_run_send", "admin_run_result"})
SEALED_JOB_KINDS.add("admin_run")


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class Base(DeclarativeBase):
    pass


class Record(Base):
    __tablename__ = "records"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    parent_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    owner_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    data: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String(40), default=now)

    @property
    def value(self):
        from .pii_storage import record_value
        return record_value(self)

    def set(self, value):
        self.data = canonical(value)


class Login(Base):
    __tablename__ = "logins"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64))
    tenant_id: Mapped[str] = mapped_column(String(64))
    csrf: Mapped[str] = mapped_column(String(100))
    token_cipher: Mapped[str] = mapped_column(Text, default="")
    expires: Mapped[int] = mapped_column(Integer)


class CoreIdentityBinding(Base):
    __tablename__ = "core_identity_bindings"
    __table_args__ = (UniqueConstraint("tenant_id", "issuer", "user_id"),)
    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    issuer: Mapped[str] = mapped_column(String(500), primary_key=True)
    subject: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)


class RecordScope(Base):
    """Additive school ownership, without rewriting historical record payloads."""
    __tablename__ = "record_scopes"
    record_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    school_id: Mapped[str] = mapped_column(String(64), index=True)


class Flow(Base):
    __tablename__ = "oauth_flows"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    cipher: Mapped[str] = mapped_column(Text)
    expires: Mapped[int] = mapped_column(Integer)


class AuditHead(Base):
    __tablename__ = "audit_heads"
    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    digest: Mapped[str] = mapped_column(String(64), default="0" * 64)


class Audit(Base):
    __tablename__ = "audit_events"
    __table_args__ = (UniqueConstraint("tenant_id", "sequence"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    session_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    event_type: Mapped[str] = mapped_column(String(60))
    actor_id: Mapped[str] = mapped_column(String(64))
    payload: Mapped[str] = mapped_column(Text)
    previous_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(40), default=now)


@event.listens_for(Record, "before_update")
@event.listens_for(Record, "before_delete")
def immutable_record(mapper, connection, target):
    old_kinds = inspect(target).attrs.kind.history.deleted
    if target.kind in IMMUTABLE_RECORD_KINDS or any(kind in IMMUTABLE_RECORD_KINDS for kind in old_kinds):
        raise ValueError("Submitted work and approved teaching snapshots are immutable")
    if target.kind in SEALED_JOB_KINDS or any(kind in SEALED_JOB_KINDS for kind in old_kinds):
        history = inspect(target).attrs.data.history.deleted
        previous = json.loads(history[0]) if history else target.value
        if old_kinds or previous.get("status") != "pending" or target in inspect(target).session.deleted:
            raise ValueError("Completed tutoring turns are immutable")


@event.listens_for(Audit, "before_update")
@event.listens_for(Audit, "before_delete")
def immutable(*_):
    raise ValueError("Audit events are append-only")


class Database:
    def __init__(self, settings):
        if settings.database_url.startswith("sqlite:///."):
            Path("var").mkdir(exist_ok=True)
        self.engine = create_engine(settings.database_url, connect_args={"check_same_thread": False, "timeout": 30} if settings.database_url.startswith("sqlite") else {}, pool_pre_ping=True)
        self.factory = sessionmaker(self.engine, expire_on_commit=False)
        self.lock = threading.RLock()

    @contextmanager
    def write(self, *, maintenance=False):
        # SQLite's write admission is cross-process; PostgreSQL additionally uses
        # transaction advisory locks so every event and related domain write is atomic.
        with self.lock, self.factory() as db:
            try:
                if self.engine.dialect.name == "sqlite":
                    db.execute(text("BEGIN IMMEDIATE"))
                else:
                    if maintenance:
                        db.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
                        db.execute(text("SET LOCAL search_path = public"))
                        db.execute(text("SET LOCAL lock_timeout = '5s'"))
                        db.execute(text("SET LOCAL statement_timeout = '30s'"))
                    db.execute(text("SELECT pg_advisory_xact_lock(514672980124)"))
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise


def audit(db, tenant, actor, event_type, payload, session_id=""):
    head = db.get(AuditHead, tenant)
    if head is None:
        head = AuditHead(tenant_id=tenant, sequence=0, digest="0" * 64)
        db.add(head)
        db.flush()
    row = Audit(id=uid(), tenant_id=tenant, sequence=head.sequence + 1, session_id=session_id, actor_id=actor, event_type=event_type, payload=canonical(payload), previous_hash=head.digest, created_at=now())
    from .pii_storage import protect_audit
    row.payload = canonical(protect_audit(db, row, payload))
    row._family_crypto = db.info.get("family_crypto")
    row.hash = hashlib.sha256(canonical(audit_material(row)).encode()).hexdigest()
    db.add(row)
    head.sequence, head.digest = row.sequence, row.hash
    from .audit_delivery import enqueue_audit_metadata
    enqueue_audit_metadata(db, row, payload)
    return row


def audit_material(row):
    return {"id": row.id, "tenant_id": row.tenant_id, "sequence": row.sequence, "session_id": row.session_id, "actor_id": row.actor_id, "event_type": row.event_type, "payload": json.loads(row.payload), "previous_hash": row.previous_hash, "created_at": row.created_at}


def audit_dict(row, *, reveal=False):
    result = {**audit_material(row), "hash": row.hash}
    if reveal and "__academy_pii__" in result["payload"]:
        from .pii_storage import audit_payload
        result["private_payload"] = audit_payload(row)
    return result


# Register outbox tables before any caller creates metadata, including isolated tests.
from . import audit_delivery as _audit_delivery_models  # noqa: E402,F401

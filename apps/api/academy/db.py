from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import threading
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, event, select, text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.types import String, Text, Integer


def uid():
    return uuid4().hex


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
        return json.loads(self.data)

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
    row.hash = hashlib.sha256(canonical(audit_material(row)).encode()).hexdigest()
    db.add(row)
    head.sequence, head.digest = row.sequence, row.hash
    return row


def audit_material(row):
    return {"id": row.id, "tenant_id": row.tenant_id, "sequence": row.sequence, "session_id": row.session_id, "actor_id": row.actor_id, "event_type": row.event_type, "payload": json.loads(row.payload), "previous_hash": row.previous_hash, "created_at": row.created_at}


def audit_dict(row):
    return {**audit_material(row), "hash": row.hash}

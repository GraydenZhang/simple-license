from datetime import datetime, timezone
from sqlalchemy import create_engine, event, String, Text, Integer, ForeignKey, Boolean, UniqueConstraint, CheckConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from .config import DB_URL

def now():
    return datetime.now(timezone.utc).isoformat()

class Base(DeclarativeBase): pass

engine = create_engine(DB_URL, connect_args={'check_same_thread': False, 'timeout': 30} if DB_URL.startswith('sqlite') else {}, pool_pre_ping=True)
if DB_URL.startswith('sqlite'):
    @event.listens_for(engine, 'connect')
    def sqlite_setup(conn, _):
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('PRAGMA journal_mode=WAL')
Session = sessionmaker(engine, expire_on_commit=False)

class User(Base):
    __tablename__ = 'users'
    __table_args__ = (CheckConstraint("role IN ('ADMIN', 'APPROVER')", name='staff_roles_only'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Legacy column name retained for DB compatibility; now means a one-time suggestion only.
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    deleted_at: Mapped[str | None] = mapped_column(String(40), nullable=True)

class StaffSession(Base):
    __tablename__ = 'staff_sessions'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    csrf: Mapped[str] = mapped_column(String(100))
    expires_at: Mapped[str] = mapped_column(String(40))

class Setting(Base):
    __tablename__ = 'settings'
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text)

class Invitation(Base):
    __tablename__ = 'invitations'
    id: Mapped[int] = mapped_column(primary_key=True)
    invitation_no: Mapped[str | None] = mapped_column(String(40), unique=True, nullable=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    label: Mapped[str] = mapped_column(String(100), default='')
    created_at: Mapped[str] = mapped_column(String(40), default=now)
    expires_at: Mapped[str] = mapped_column(String(40))
    disabled_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    first_submitted_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    access_ends_at: Mapped[str | None] = mapped_column(String(40), nullable=True)

class InvitationSequence(Base):
    __tablename__ = 'invitation_sequences'
    day: Mapped[str] = mapped_column(String(8), primary_key=True)
    last_number: Mapped[int] = mapped_column(Integer)

class Application(Base):
    __tablename__ = 'applications'
    id: Mapped[int] = mapped_column(primary_key=True)
    invitation_id: Mapped[int] = mapped_column(ForeignKey('invitations.id'), unique=True)
    status: Mapped[str] = mapped_column(String(20), default='PENDING')
    data_json: Mapped[str] = mapped_column(Text)
    submitted_at: Mapped[str] = mapped_column(String(40), default=now)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    reviewed_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

class Revision(Base):
    __tablename__ = 'application_revisions'
    __table_args__ = (UniqueConstraint('application_id', 'version_no'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey('applications.id'))
    version_no: Mapped[int] = mapped_column(Integer)
    snapshot_json: Mapped[str] = mapped_column(Text)
    submitted_at: Mapped[str] = mapped_column(String(40), default=now)

class Attachment(Base):
    __tablename__ = 'attachments'
    id: Mapped[int] = mapped_column(primary_key=True)
    invitation_id: Mapped[int] = mapped_column(ForeignKey('invitations.id'))
    kind: Mapped[str] = mapped_column(String(30))
    mime: Mapped[str] = mapped_column(String(60))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(100))
    original_name: Mapped[str] = mapped_column(String(255))
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    uploaded_at: Mapped[str] = mapped_column(String(40), default=now)

class Permit(Base):
    __tablename__ = 'permits'
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey('applications.id'), index=True)
    active_application_id: Mapped[int | None] = mapped_column(Integer, unique=True, nullable=True)
    permit_no: Mapped[str] = mapped_column(String(60), unique=True)
    verify_token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    snapshot_json: Mapped[str] = mapped_column(Text)
    start_date: Mapped[str] = mapped_column(String(10))
    end_date: Mapped[str] = mapped_column(String(10))
    issued_at: Mapped[str] = mapped_column(String(40), default=now)
    issued_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    revoked_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reissued_from_id: Mapped[int | None] = mapped_column(ForeignKey('permits.id'), nullable=True)
    image_storage_key: Mapped[str] = mapped_column(String(100))

class VerificationLog(Base):
    __tablename__ = 'verification_access_logs'
    id: Mapped[int] = mapped_column(primary_key=True)
    permit_id: Mapped[int | None] = mapped_column(ForeignKey('permits.id'), nullable=True, index=True)
    anon_device_id: Mapped[str] = mapped_column(String(36), index=True)
    source_ip: Mapped[str] = mapped_column(String(100), index=True)
    user_agent: Mapped[str] = mapped_column(Text)
    device_type: Mapped[str] = mapped_column(String(30))
    browser_name: Mapped[str] = mapped_column(String(100))
    browser_version: Mapped[str] = mapped_column(String(100))
    os_name: Mapped[str] = mapped_column(String(100))
    os_version: Mapped[str] = mapped_column(String(100))
    status_seen: Mapped[str] = mapped_column(String(30))
    http_status: Mapped[int] = mapped_column(Integer)
    visited_at: Mapped[str] = mapped_column(String(40), default=now, index=True)

class AuditLog(Base):
    __tablename__ = 'audit_logs'
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    action: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    details_json: Mapped[str] = mapped_column(Text, default='{}')
    source_ip: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[str] = mapped_column(String(40), default=now)

class LoginAttempt(Base):
    __tablename__ = 'login_attempts'
    id: Mapped[int] = mapped_column(primary_key=True)
    source_ip: Mapped[str] = mapped_column(String(100), index=True)
    created_at: Mapped[str] = mapped_column(String(40), default=now)

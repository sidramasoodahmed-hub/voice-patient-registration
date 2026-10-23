import os
import uuid
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./patients.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
if DATABASE_URL.startswith("postgresql://"):
    # force the psycopg v3 driver we install in requirements.txt
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def _now():
    return datetime.now(timezone.utc)


def _uuid():
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Patient(Base):
    __tablename__ = "patients"
    __table_args__ = (
        CheckConstraint("sex IN ('Male','Female','Other','Decline to Answer')", name="ck_sex"),
        CheckConstraint("length(state) = 2", name="ck_state"),
        CheckConstraint("length(phone_number) = 10", name="ck_phone"),
    )
    patient_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    first_name: Mapped[str] = mapped_column(String(50))
    last_name: Mapped[str] = mapped_column(String(50), index=True)
    date_of_birth: Mapped[date] = mapped_column(Date)
    sex: Mapped[str] = mapped_column(String(20))
    phone_number: Mapped[str] = mapped_column(String(10), index=True)
    email: Mapped[Optional[str]] = mapped_column(String(254), nullable=True)
    address_line_1: Mapped[str] = mapped_column(String(200))
    address_line_2: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    city: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(2))
    zip_code: Mapped[str] = mapped_column(String(10))
    insurance_provider: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    insurance_member_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    preferred_language: Mapped[str] = mapped_column(String(50), default="English")
    emergency_contact_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    emergency_contact_phone: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class CallLog(Base):
    """Transcript + summary of each call, linked to the patient it created/updated."""
    __tablename__ = "call_logs"
    call_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    patient_id: Mapped[Optional[str]] = mapped_column(ForeignKey("patients.patient_id"), nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    transcript: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ended_reason: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        if db.scalar(select(Patient).limit(1)) is None:  # seed one demo record
            db.add(Patient(
                first_name="Jane", last_name="Doe", date_of_birth=date(1990, 4, 12), sex="Female",
                phone_number="5555550100", address_line_1="123 Main St", city="Austin",
                state="TX", zip_code="78701", preferred_language="English",
            ))
            db.commit()
            

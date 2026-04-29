from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import declarative_base, sessionmaker


HISTORY_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
HISTORY_DATA_DIR.mkdir(exist_ok=True)
HISTORY_DATABASE_URL = f"sqlite:///{HISTORY_DATA_DIR / 'history.db'}"

engine = create_engine(HISTORY_DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class TripImportJob(Base):
    __tablename__ = "trip_import_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    root_path = Column(String(1024), nullable=False)
    status = Column(String(30), default="pending")
    total_files = Column(Integer, default=0)
    processed_count = Column(Integer, default=0)
    success_count = Column(Integer, default=0)
    failed_count = Column(Integer, default=0)
    review_count = Column(Integer, default=0)
    message = Column(Text)
    started_at = Column(DateTime, default=datetime.now)
    completed_at = Column(DateTime)


class TripSourceFile(Base):
    __tablename__ = "trip_source_files"

    id = Column(Integer, primary_key=True, autoincrement=True)
    import_job_id = Column(Integer, nullable=True)
    trip_record_id = Column(Integer, nullable=True)
    filename = Column(String(255), nullable=False)
    file_path = Column(String(1024), nullable=False)
    file_hash = Column(String(64), nullable=False)
    file_size_kb = Column(Float)
    status = Column(String(30), default="pending")
    error_message = Column(Text)
    created_at = Column(DateTime, default=datetime.now)
    processed_at = Column(DateTime)


class Organization(Base):
    __tablename__ = "organizations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name_cn = Column(String(255), nullable=False)
    normalized_name = Column(String(255), nullable=False)
    name_en = Column(String(255))
    country = Column(String(100))
    region = Column(String(50))
    industry = Column(String(100))
    org_type = Column(String(50))
    notes = Column(Text)
    visit_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class TripRecord(Base):
    __tablename__ = "trip_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    import_job_id = Column(Integer, nullable=True)
    source_file_id = Column(Integer, nullable=True)
    organization_id = Column(Integer, nullable=True)
    country = Column(String(100))
    region = Column(String(50))
    city = Column(String(100))
    organization_name_cn = Column(String(255))
    organization_name_en = Column(String(255))
    org_type = Column(String(50))
    industry = Column(String(100))
    visit_purpose = Column(Text)
    visit_summary = Column(Text)
    duration_days = Column(Integer)
    group_unit = Column(String(255))
    group_type = Column(String(50))
    group_size = Column(String(20))
    expense_source = Column(String(255))
    visit_date = Column(String(50))
    source_filename = Column(String(255))
    source_path = Column(String(1024))
    source_hash = Column(String(64))
    status = Column(String(30), default="completed")
    needs_review = Column(Boolean, default=False)
    duplicate_status = Column(String(30), default="unique")
    duplicate_reason = Column(Text)
    extraction_json = Column(Text)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class TripContact(Base):
    __tablename__ = "trip_contacts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trip_record_id = Column(Integer, nullable=False)
    organization_id = Column(Integer, nullable=True)
    name = Column(String(100), nullable=False)
    title = Column(String(100))
    email = Column(String(255))
    phone = Column(String(50))
    created_at = Column(DateTime, default=datetime.now)


Base.metadata.create_all(bind=engine)


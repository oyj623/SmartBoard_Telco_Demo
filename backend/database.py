"""
The auth-side ORM.

Deliberately small. SQLAlchemy is here only for the two tables the application
writes to and reads through Python — users, and which states each one may see.
Everything the board queries goes through SmartBoard's own read-only adapter
against the same file, and none of those tables need a model here.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import Column, ForeignKey, Integer, String, UniqueConstraint, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{ROOT / 'nusatel.db'}")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    role = Column(String, nullable=False)   # exec | regional
    name = Column(String)


class RegionAssignment(Base):
    """
    Which states a regional manager may see.

    This table is the source of the tenancy predicate. It is read server-side in
    `board_context()` and never influenced by the request body — see
    backend/board_security.py.
    """

    __tablename__ = "region_assignments"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    state_code = Column(String, nullable=False, index=True)

    __table_args__ = (UniqueConstraint("user_id", "state_code", name="uq_region_assignment"),)

import sys
import os
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.engine import Engine
from fastapi.testclient import TestClient
from unittest.mock import patch

from app.models import Base
from app.db import get_db, SessionLocal
from app.main import app, initialize_database

# Set TESTING environment variable
os.environ["TESTING"] = "true"

# Create SQLite in-memory engine for testing
engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

@pytest.fixture(scope="session", autouse=True)
def create_test_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)

@pytest.fixture()
def session():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

@pytest.fixture()
def client(session):
    def override_get_db():
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()

@pytest.fixture(autouse=True)
def mock_db_session(monkeypatch):
    """Globally mock the database session for all tests"""
    def mock_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    monkeypatch.setattr("app.db.get_db", mock_get_db)
    monkeypatch.setattr("app.main.get_db", mock_get_db)
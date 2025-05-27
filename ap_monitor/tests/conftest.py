import sys
import os
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.engine import Engine
from fastapi.testclient import TestClient
from unittest.mock import patch

from ap_monitor.app.models import (
    AccessPoint, ClientCount, Building, Floor, Campus,
    ApBuilding, Room, RadioType, ClientCountAP
)
from ap_monitor.app.db import (
    wireless_engine, apclient_engine,
    WirelessBase, APClientBase,
    WirelessSessionLocal, APClientSessionLocal,
    get_wireless_db, get_apclient_db
)
from ap_monitor.app.main import app, initialize_database

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
    """Create test database tables."""
    # Create tables for both databases
    WirelessBase.metadata.create_all(bind=wireless_engine)
    APClientBase.metadata.create_all(bind=apclient_engine)
    yield
    # Clean up
    WirelessBase.metadata.drop_all(bind=wireless_engine)
    APClientBase.metadata.drop_all(bind=apclient_engine)

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

    app.dependency_overrides[get_wireless_db] = override_get_db
    app.dependency_overrides[get_apclient_db] = override_get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()

@pytest.fixture(autouse=True)
def mock_db_session(monkeypatch):
    """Globally mock the database session for all tests"""
    def mock_get_wireless_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    def mock_get_apclient_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    monkeypatch.setattr("ap_monitor.app.db.get_wireless_db", mock_get_wireless_db)
    monkeypatch.setattr("ap_monitor.app.db.get_apclient_db", mock_get_apclient_db)
    monkeypatch.setattr("ap_monitor.app.main.get_wireless_db", mock_get_wireless_db)
    monkeypatch.setattr("ap_monitor.app.main.get_apclient_db", mock_get_apclient_db)

@pytest.fixture
def wireless_db():
    """Get wireless_count database session."""
    db = WirelessSessionLocal()
    try:
        yield db
    finally:
        db.close()

@pytest.fixture
def apclient_db():
    """Get apclientcount database session."""
    db = APClientSessionLocal()
    try:
        yield db
    finally:
        db.close()
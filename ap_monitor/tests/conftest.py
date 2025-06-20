import sys
import os
import pytest
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.pool import StaticPool
from sqlalchemy.engine import Engine
from fastapi.testclient import TestClient
from unittest.mock import patch

import ap_monitor.app.db  # Ensure db module is loaded so attributes exist for monkeypatching
print('DEBUG ap_monitor.app.db attributes:', dir(ap_monitor.app.db))

from ap_monitor.app.models import (
    AccessPoint, ClientCount, Building, Floor, Campus,
    ApBuilding, Room, RadioType, ClientCountAP,
    WirelessBase, APClientBase
)
from ap_monitor.app.db import (
    get_wireless_db,
    get_apclient_db,
    get_wireless_db_dep,
    get_apclient_db_dep
)
from ap_monitor.app.main import app

# Set TESTING environment variable
os.environ["TESTING"] = "true"

# Use two separate in-memory SQLite databases for wireless and apclient schemas
WIRELESS_TEST_DB_URL = "sqlite:///:memory:"
APCLIENT_TEST_DB_URL = "sqlite:///:memory:"

# Create engines with StaticPool to ensure same connection across threads
wireless_engine = create_engine(
    WIRELESS_TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
apclient_engine = create_engine(
    APCLIENT_TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)

# Create session factories
WirelessSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=wireless_engine)
APClientSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=apclient_engine)

# Monkeypatch the app's db.py to use the test engines and session factories
ap_monitor.app.db.wireless_engine = wireless_engine
ap_monitor.app.db.apclient_engine = apclient_engine
ap_monitor.app.db.WirelessSessionLocal = WirelessSessionLocal
ap_monitor.app.db.APClientSessionLocal = APClientSessionLocal

# Enable foreign key support for SQLite
@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

# --- Create tables for both databases ---
@pytest.fixture(autouse=True)
def create_test_db():
    # Import wireless models before creating wireless tables
    from ap_monitor.app.models import Building, Campus, ClientCount, WirelessBase, ApBuilding, Floor, Room, AccessPoint, ClientCountAP, RadioType, APClientBase
    WirelessBase.metadata.drop_all(bind=wireless_engine)
    WirelessBase.metadata.create_all(bind=wireless_engine)
    APClientBase.metadata.drop_all(bind=apclient_engine)
    APClientBase.metadata.create_all(bind=apclient_engine)
    # Verify tables are created correctly
    inspector = inspect(apclient_engine)
    tables = inspector.get_table_names()
    print(f"Tables in apclient_engine: {tables}")
    for table_name in ['buildings', 'floors', 'rooms', 'accesspoints', 'clientcount', 'radiotypes']:
        assert table_name in tables, f"{table_name} table not created"
        columns = [col['name'] for col in inspector.get_columns(table_name)]
        print(f"Columns in {table_name}: {columns}")
    # Add default radio types
    with APClientSessionLocal() as session:
        if not session.query(RadioType).first():
            session.add_all([
                RadioType(radioname="radio0", radioid=1),
                RadioType(radioname="radio1", radioid=2),
                RadioType(radioname="radio2", radioid=3)
            ])
            session.commit()
    yield
    WirelessBase.metadata.drop_all(bind=wireless_engine)
    APClientBase.metadata.drop_all(bind=apclient_engine)

# --- Database session fixtures ---
@pytest.fixture
def wireless_db():
    """Provide a session for the wireless database."""
    db = WirelessSessionLocal()
    try:
        yield db
    finally:
        db.close()

@pytest.fixture
def apclient_db():
    """Provide a session for the apclient database."""
    db = APClientSessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- TestClient with dependency overrides for both DBs ---
@pytest.fixture
def client(wireless_db, apclient_db, scheduler):
    def override_get_wireless_db():
        try:
            yield wireless_db
        finally:
            pass
    
    def override_get_apclient_db():
        try:
            yield apclient_db
        finally:
            pass
    
    def override_get_wireless_db_dep():
        try:
            yield wireless_db
        finally:
            pass
    
    def override_get_apclient_db_dep():
        try:
            yield apclient_db
        finally:
            pass
    
    app.dependency_overrides[get_wireless_db] = override_get_wireless_db
    app.dependency_overrides[get_apclient_db] = override_get_apclient_db
    app.dependency_overrides[get_wireless_db_dep] = override_get_wireless_db_dep
    app.dependency_overrides[get_apclient_db_dep] = override_get_apclient_db_dep
    
    # Add scheduler to app state
    app.state.scheduler = scheduler
    
    with TestClient(app) as test_client:
        yield test_client
    
    app.dependency_overrides.clear()
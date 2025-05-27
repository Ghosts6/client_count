import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi.testclient import TestClient
from ap_monitor.app.db import get_wireless_db, get_apclient_db
from ap_monitor.app.main import app, update_ap_data_task, update_client_count_task
from ap_monitor.app.models import (
    AccessPoint, ClientCount, Building, Floor, Campus, ApBuilding, Room, RadioType, ClientCountAP
)
from datetime import datetime, timezone
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from ap_monitor.app.db import WirelessBase, APClientBase
from sqlalchemy import event
from apscheduler.schedulers.background import BackgroundScheduler
import os
import logging
from contextlib import asynccontextmanager

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Mock the lifespan context
@asynccontextmanager
async def mock_lifespan(app):
    yield

# Replace the app's lifespan with our mock
app.router.lifespan_context = mock_lifespan

@pytest.fixture
def scheduler():
    scheduler = BackgroundScheduler()
    scheduler.start()
    yield scheduler
    if scheduler.running:
        scheduler.shutdown()

@pytest.fixture
def session():
    logger.info("Creating test database sessions")
    
    # Create separate in-memory databases for each base with check_same_thread=False
    wireless_engine = create_engine("sqlite:///:memory:", echo=True, connect_args={"check_same_thread": False})
    apclient_engine = create_engine("sqlite:///:memory:", echo=True, connect_args={"check_same_thread": False})
    
    # Enable foreign key support for SQLite
    def _fk_pragma_on_connect(dbapi_con, con_record):
        dbapi_con.execute('pragma foreign_keys=ON')
    
    event.listen(wireless_engine, 'connect', _fk_pragma_on_connect)
    event.listen(apclient_engine, 'connect', _fk_pragma_on_connect)
    
    # Create all tables
    WirelessBase.metadata.create_all(bind=wireless_engine)
    APClientBase.metadata.create_all(bind=apclient_engine)
    
    # Create sessions
    WirelessSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=wireless_engine)
    APClientSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=apclient_engine)
    
    wireless_session = WirelessSessionLocal()
    apclient_session = APClientSessionLocal()
    
    try:
        yield (wireless_session, apclient_session)
    finally:
        wireless_session.close()
        apclient_session.close()

def get_table_dependencies(session):
    """Get all table dependencies in the database."""
    inspector = inspect(session.get_bind())
    dependencies = {}
    
    for table_name in inspector.get_table_names():
        foreign_keys = inspector.get_foreign_keys(table_name)
        dependencies[table_name] = [fk['referred_table'] for fk in foreign_keys]
    
    return dependencies

@pytest.fixture
def override_get_db_with_mock_ap():
    mock_ap = MagicMock()
    mock_ap.apid = 1
    mock_ap.apname = "AP01"
    mock_ap.macaddress = "00:11:22:33:44:55"
    mock_ap.ipaddress = "192.168.1.1"
    mock_ap.modelname = "ModelX"
    mock_ap.isactive = True
    mock_ap.buildingid = 1
    mock_ap.floorid = 1
    mock_ap.roomid = None

    mock_query = MagicMock()
    mock_query.all.return_value = [mock_ap]

    mock_session = MagicMock()
    mock_session.query.return_value = mock_query

    def override():
        yield mock_session

    app.dependency_overrides[get_wireless_db] = override
    app.dependency_overrides[get_apclient_db] = override
    yield
    app.dependency_overrides.clear()

@pytest.fixture
def override_get_db_with_mock_buildings():
    mock_building = MagicMock()
    # Patch to match the API's expected attribute names and values
    mock_building.building_id = 1
    mock_building.building_name = "BuildingA"

    mock_query = MagicMock()
    mock_query.all.return_value = [mock_building]

    mock_session = MagicMock()
    mock_session.query.return_value = mock_query

    def override():
        yield mock_session

    app.dependency_overrides[get_wireless_db] = override
    app.dependency_overrides[get_apclient_db] = override
    yield
    app.dependency_overrides.clear()

@pytest.fixture
def client(session, scheduler):
    wireless_session, apclient_session = session
    
    def override_get_wireless_db():
        try:
            yield wireless_session
        finally:
            pass
    
    def override_get_apclient_db():
        try:
            yield apclient_session
        finally:
            pass
    
    app.dependency_overrides[get_wireless_db] = override_get_wireless_db
    app.dependency_overrides[get_apclient_db] = override_get_apclient_db
    
    # Add scheduler to app state
    app.state.scheduler = scheduler
    
    with TestClient(app) as test_client:
        yield test_client
    
    app.dependency_overrides.clear()

@pytest.fixture
def test_data(session):
    logger.info("Starting test_data fixture")
    wireless_session, apclient_session = session
    
    # Get table dependencies
    wireless_dependencies = get_table_dependencies(wireless_session)
    apclient_dependencies = get_table_dependencies(apclient_session)
    logger.debug(f"Wireless table dependencies: {wireless_dependencies}")
    logger.debug(f"APClient table dependencies: {apclient_dependencies}")
    
    def delete_records(session, model, model_name):
        try:
            logger.info(f"Attempting to delete records from {model_name}")
            count = session.query(model).count()
            logger.debug(f"Found {count} records in {model_name}")
            session.query(model).delete()
            session.commit()
            logger.info(f"Successfully deleted records from {model_name}")
        except Exception as e:
            logger.error(f"Error deleting records from {model_name}: {str(e)}")
            session.rollback()
            raise
    
    # Clean up existing data in correct order respecting foreign key constraints
    try:
        logger.info("Starting cleanup of existing data")
        
        # Delete from apclientcount database first
        delete_records(apclient_session, ClientCountAP, "ClientCountAP")
        delete_records(apclient_session, AccessPoint, "AccessPoint")
        delete_records(apclient_session, Room, "Room")
        delete_records(apclient_session, Floor, "Floor")
        delete_records(apclient_session, ApBuilding, "ApBuilding")
        delete_records(apclient_session, RadioType, "RadioType")
        
        # Delete from wireless_count database
        delete_records(wireless_session, Building, "Building")
        delete_records(wireless_session, Campus, "Campus")
        
        logger.info("Cleanup completed successfully")
    except Exception as e:
        logger.error(f"Error during cleanup: {str(e)}")
        wireless_session.rollback()
        apclient_session.rollback()
        raise
    
    # Create test data
    try:
        logger.info("Starting creation of test data")
        
        # Create radio type in apclientcount DB
        logger.debug("Creating RadioType")
        radio = RadioType(radioname="radio0")
        apclient_session.add(radio)
        apclient_session.commit()
        logger.debug(f"Created RadioType with ID: {radio.radioid}")
        
        # Create campus in wireless_count DB
        logger.debug("Creating Campus")
        campus = Campus(campus_name="Test Campus")
        wireless_session.add(campus)
        wireless_session.commit()
        logger.debug(f"Created Campus with ID: {campus.campus_id}")
        
        # Create building in wireless_count DB
        logger.debug("Creating Building")
        building = Building(
            building_name="Test Building",
            campus_id=campus.campus_id,
            latitude=37.7749,
            longitude=-122.4194
        )
        wireless_session.add(building)
        wireless_session.commit()
        logger.debug(f"Created Building with ID: {building.building_id}")
        
        # Create building in apclientcount DB
        logger.debug("Creating ApBuilding")
        ap_building = ApBuilding(buildingname="Test AP Building")
        apclient_session.add(ap_building)
        apclient_session.commit()
        logger.debug(f"Created ApBuilding with ID: {ap_building.buildingid}")
        
        # Create floor in apclientcount DB
        logger.debug("Creating Floor")
        floor = Floor(
            buildingid=ap_building.buildingid,
            floorname="1st Floor"
        )
        apclient_session.add(floor)
        apclient_session.commit()
        logger.debug(f"Created Floor with ID: {floor.floorid}")
        
        # Create room in apclientcount DB
        logger.debug("Creating Room")
        room = Room(
            floorid=floor.floorid,
            roomname="Test Room"
        )
        apclient_session.add(room)
        apclient_session.commit()
        logger.debug(f"Created Room with ID: {room.roomid}")
        
        # Create access point in apclientcount DB
        logger.debug("Creating AccessPoint")
        ap = AccessPoint(
            apname="Test AP",
            macaddress="00:11:22:33:44:55",
            ipaddress="192.168.1.1",
            modelname="Test Model",
            buildingid=ap_building.buildingid,
            floorid=floor.floorid,
            roomid=room.roomid,
            isactive=True
        )
        apclient_session.add(ap)
        apclient_session.commit()
        logger.debug(f"Created AccessPoint with ID: {ap.apid}")
        
        # Create client count in apclientcount DB
        logger.debug("Creating ClientCountAP")
        client_count = ClientCountAP(
            apid=ap.apid,
            radioid=radio.radioid,
            clientcount=10,
            timestamp=datetime.now(timezone.utc)
        )
        apclient_session.add(client_count)
        apclient_session.commit()
        logger.debug(f"Created ClientCountAP with ID: {client_count.countid}")
        
        logger.info("Test data creation completed successfully")
        
        return {
            "campus": campus,
            "building": building,
            "ap_building": ap_building,
            "floor": floor,
            "room": room,
            "radio": radio,
            "ap": ap,
            "client_count": client_count
        }
    except Exception as e:
        logger.error(f"Error during test data creation: {str(e)}")
        wireless_session.rollback()
        apclient_session.rollback()
        raise

def test_get_aps(client, override_get_db_with_mock_ap):
    logger.info("Running test_get_aps")
    response = client.get("/aps")
    assert response.status_code == 200
    assert response.json() == [{
        "apid": 1,
        "apname": "AP01",
        "macaddress": "00:11:22:33:44:55",
        "ipaddress": "192.168.1.1",
        "modelname": "ModelX",
        "isactive": True,
        "buildingid": 1,
        "floorid": 1,
        "roomid": None
    }]

def test_get_buildings(client, override_get_db_with_mock_buildings):
    logger.info("Running test_get_buildings")
    response = client.get("/buildings")
    assert response.status_code == 200
    # The new API returns a list of dicts with 'building_id' and 'building_name'
    assert response.json() == [{
        "building_id": 1,
        "building_name": "BuildingA"
    }]

def test_get_client_counts(client, test_data):
    logger.info("Running test_get_client_counts")
    response = client.get("/client-counts/")
    assert response.status_code == 200
    data = response.json()
    logger.debug(f"Client counts response: {data}")
    assert len(data) == 1  # Should have one client count record
    assert data[0]["clientcount"] == 10
    assert data[0]["apname"] == "Test AP"
    assert data[0]["radioname"] == "radio0"
    assert data[0]["apid"] == test_data["ap"].apid
    assert data[0]["radioid"] == test_data["radio"].radioid

@patch("ap_monitor.app.main.fetch_client_counts")
@patch("ap_monitor.app.main.auth_manager")
def test_update_client_count_task(mock_auth, mock_fetch, client, test_data):
    logger.info("Running test_update_client_count_task")
    # Mock the auth manager
    mock_auth.get_token.return_value = "test_token"
    mock_auth.headers = {"Authorization": "Bearer test_token"}
    
    # Mock the client count fetch
    mock_fetch.return_value = [
        {
            "siteName": "TestFloor",
            "parentSiteName": "TestBuilding",
            "clientCount": {"radio0": 10},
            "numberOfWirelessClients": 10,
            "macAddress": "00:11:22:33:44:55",
            "name": "TestAP",
            "ipAddress": "192.168.1.1",
            "type": "ModelX",
            "healthScore": 10
        }
    ]
    
    response = client.post("/tasks/update-client-count/")
    assert response.status_code == 200
    assert response.json() == {"message": "Client count update task started"}

@patch("ap_monitor.app.main.fetch_ap_data")
@patch("ap_monitor.app.main.auth_manager")
def test_update_ap_data_task(mock_auth, mock_fetch, client, test_data):
    logger.info("Running test_update_ap_data_task")
    # Mock the auth manager
    mock_auth.get_token.return_value = "test_token"
    mock_auth.headers = {"Authorization": "Bearer test_token"}
    
    # Mock the AP data fetch
    mock_fetch.return_value = [
        {
            "name": "TestAP",
            "macAddress": "00:11:22:33:44:55",
            "ipAddress": "192.168.1.1",
            "model": "ModelX",
            "reachabilityHealth": "UP",
            "clientCount": {"radio0": 10},
            "location": "/Global/Campus/TestBuilding/TestFloor/TestRoom"
        }
    ]
    
    response = client.post("/tasks/update-ap-data/")
    assert response.status_code == 200
    assert response.json() == {"message": "AP data update task started"}



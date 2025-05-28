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
from contextlib import asynccontextmanager
from sqlalchemy import func
from unittest.mock import ANY
import logging
from sqlalchemy import text
from sqlalchemy.orm import Session

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
    mock_ap.building_id = 1
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
    
    app.dependency_overrides[get_wireless_db] = override_get_wireless_db
    app.dependency_overrides[get_apclient_db] = override_get_apclient_db
    
    # Add scheduler to app state
    app.state.scheduler = scheduler
    
    with TestClient(app) as test_client:
        yield test_client
    
    app.dependency_overrides.clear()

@pytest.fixture(scope="function")
def wireless_db():
    """Create a test database for wireless_count."""
    # Create test database with check_same_thread=False to allow cross-thread access
    test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=test_engine)
    session = Session()
    
    # Create tables in correct order
    WirelessBase.metadata.create_all(test_engine)
    
    # Create test data
    try:
        # Create campus
        campus = Campus(campus_id=1, campus_name="Test Campus")
        session.add(campus)
        session.commit()
        
        # Create building
        building = Building(
            building_id=1,
            building_name="Test Building",
            campus_id=1,
            latitude=37.7749,
            longitude=-122.4194
        )
        session.add(building)
        session.commit()
        
        # Create client counts
        client_counts = [
            ClientCount(
                building_id=1,
                client_count=10,
                time_inserted=datetime.now(timezone.utc)
            )
        ]
        session.add_all(client_counts)
        session.commit()
        
        yield session
    finally:
        session.close()
        WirelessBase.metadata.drop_all(test_engine)

@pytest.fixture(scope="function")
def apclient_db():
    """Create a test database for apclientcount."""
    # Create test database with check_same_thread=False to allow cross-thread access
    test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=test_engine)
    session = Session()
    
    # Create tables in correct order
    APClientBase.metadata.create_all(test_engine)
    
    # Create test data
    try:
        # Create building
        building = ApBuilding(
            building_id=1,
            building_name="Test Building"
        )
        session.add(building)
        session.commit()
        
        # Create floor
        floor = Floor(
            floorid=1,
            building_id=1,
            floorname="1st Floor"
        )
        session.add(floor)
        session.commit()
        
        # Create room
        room = Room(
            roomid=1,
            floorid=1,
            roomname="Room 101"
        )
        session.add(room)
        session.commit()
        
        # Create access point
        ap = AccessPoint(
            apid=1,
            building_id=1,
            floorid=1,
            roomid=1,
            apname="AP-01",
            macaddress="00:11:22:33:44:55",
            ipaddress="192.168.1.1",
            modelname="AIR-CAP3702I-A-K9",
            isactive=True
        )
        session.add(ap)
        session.commit()
        
        yield session
    finally:
        session.close()
        APClientBase.metadata.drop_all(test_engine)

@pytest.fixture
def test_data(wireless_db, apclient_db):
    logger.info("Setting up test data")
    try:
        # Create test data
        # Create wireless_count data
        logger.debug("Creating campus in wireless database")
        campus = Campus(campus_name="Keele Campus")
        wireless_db.add(campus)
        wireless_db.commit()

        logger.debug("Creating building in wireless database")
        building = Building(
            building_name="Keele Campus",
            campus_id=campus.campus_id,
            latitude=43.7735473000,
            longitude=-79.5062752000
        )
        wireless_db.add(building)
        wireless_db.commit()

        # Create apclientcount data
        logger.debug("Creating radio types in apclient database")
        radio_types = [
            RadioType(radioname="radio0", radioid=1),
            RadioType(radioname="radio1", radioid=2),
            RadioType(radioname="radio2", radioid=3)
        ]
        for radio_type in radio_types:
            apclient_db.add(radio_type)
        apclient_db.commit()

        logger.debug("Creating building in apclient database")
        ap_building = ApBuilding(building_name="Keele Campus")
        apclient_db.add(ap_building)
        apclient_db.commit()

        logger.debug("Creating floor in apclient database")
        floor = Floor(
            building_id=ap_building.building_id,
            floorname="Floor 5"
        )
        apclient_db.add(floor)
        apclient_db.commit()

        logger.debug("Creating access point in apclient database")
        ap = AccessPoint(
            building_id=ap_building.building_id,
            floorid=floor.floorid,
            roomid=None,
            apname="k372-ross-5-28",
            macaddress="a8:9d:21:b9:67:a0",
            ipaddress="10.30.2.154",
            modelname="Cisco 3700I Unified Access Point",
            isactive=True
        )
        apclient_db.add(ap)
        apclient_db.commit()

        # Create client count records for each radio
        logger.debug("Creating client count records in apclient database")
        for radio_type in radio_types:
            client_count = ClientCountAP(
                apid=ap.apid,
                radioid=radio_type.radioid,
                clientcount=10,
                timestamp=datetime.now(timezone.utc)
            )
            apclient_db.add(client_count)
        apclient_db.commit()

        # Create client count in wireless database
        logger.debug("Creating client count in wireless database")
        wireless_client_count = ClientCount(
            building_id=building.building_id,
            client_count=30  # Total of all radio counts
        )
        wireless_db.add(wireless_client_count)
        wireless_db.commit()

        logger.info("Test data setup completed successfully")
        return {
            "campus": campus,
            "building": building,
            "ap_building": ap_building,
            "floor": floor,
            "ap": ap,
            "radio_types": radio_types,
            "client_counts": apclient_db.query(ClientCountAP).all(),
            "wireless_client_count": wireless_client_count
        }
    except Exception as e:
        logger.error(f"Error setting up test data: {str(e)}")
        wireless_db.rollback()
        apclient_db.rollback()
        raise

def test_get_aps(client, override_get_db_with_mock_ap):
    response = client.get("/aps")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["apname"] == "AP01"
    assert data[0]["macaddress"] == "00:11:22:33:44:55"

def test_get_buildings(client, override_get_db_with_mock_buildings):
    response = client.get("/buildings")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["building_name"] == "BuildingA"

def test_get_client_counts(client, test_data):
    response = client.get("/client-counts")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 3  # We expect 3 records (one for each radio type)
    # Verify the data matches our test data
    for record in data:
        assert record["apname"] == "k372-ross-5-28"
        assert record["clientcount"] == 10
        assert record["radioid"] in [1, 2, 3]  # radio0, radio1, radio2

@patch("ap_monitor.app.main.auth_manager")
def test_update_client_count_task(mock_auth, client, test_data, apclient_db):
    logger.info("Starting client count update test")
    mock_auth.get_token.return_value = "test_token"
    mock_fetch = MagicMock()
    mock_fetch.return_value = [
        {
            "name": "k372-ross-5-28",
            "macAddress": "a8:9d:21:b9:67:a0",
            "ipAddress": "10.30.2.154",
            "model": "Cisco 3700I Unified Access Point",
            "reachabilityHealth": "UP",
            "location": "Global/Keele Campus/Bethune Residence/Floor 5",
            "clientCount": {
                "radio0": 15,
                "radio1": 20,
                "radio2": 25
            }
        }
    ]
    try:
        logger.debug("Running update_client_count_task")
        with patch("ap_monitor.app.main.scheduler.add_job"):
            # Run the update task
            update_client_count_task(db=apclient_db, auth_manager_obj=mock_auth, fetch_client_counts_func=mock_fetch, fetch_ap_data_func=MagicMock())
            apclient_db.commit()  # Ensure changes are committed

        mock_fetch.assert_called_once_with(mock_auth, ANY)
        
        logger.debug("Fetching updated client counts")
        response = client.get("/client-counts")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 3  # We expect 3 records (one for each radio type)
        
        # Verify the updated counts
        for record in data:
            logger.debug(f"Verifying record: {record}")
            assert record["apname"] == "k372-ross-5-28"
            assert record["clientcount"] in [15, 20, 25]  # Updated counts for each radio
            assert record["radioid"] in [1, 2, 3]  # radio0, radio1, radio2
        logger.info("Client count update test completed successfully")
    except Exception as e:
        logger.error(f"Error in client count update test: {str(e)}")
        apclient_db.rollback()
        raise

@patch("ap_monitor.app.main.auth_manager")
def test_update_ap_data_task(mock_auth, client, test_data, apclient_db):
    logger.info("Starting AP data update test")
    mock_auth.get_token.return_value = "test_token"
    mock_fetch = MagicMock()
    mock_fetch.return_value = [
        {
            "name": "k372-ross-5-28",
            "macAddress": "a8:9d:21:b9:67:a0",
            "ipAddress": "10.30.2.154",
            "model": "Cisco 3700I Unified Access Point",
            "reachabilityHealth": "UP",
            "location": "Global/Keele Campus/Bethune Residence/Floor 5"
        }
    ]
    try:
        logger.debug("Running update_ap_data_task")
        with patch("ap_monitor.app.main.scheduler.add_job"):
            # Run the update task
            update_ap_data_task(db=apclient_db, auth_manager_obj=mock_auth, fetch_ap_data_func=mock_fetch)
            apclient_db.commit()  # Ensure changes are committed

        mock_fetch.assert_called_once_with(mock_auth, ANY)
        
        logger.debug("Fetching updated AP data")
        response = client.get("/aps")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["apname"] == "k372-ross-5-28"
        assert data[0]["macaddress"] == "a8:9d:21:b9:67:a0"
        assert data[0]["ipaddress"] == "10.30.2.154"
        assert data[0]["modelname"] == "Cisco 3700I Unified Access Point"
        assert data[0]["isactive"] == True
        logger.info("AP data update test completed successfully")
    except Exception as e:
        logger.error(f"Error in AP data update test: {str(e)}")
        apclient_db.rollback()
        raise



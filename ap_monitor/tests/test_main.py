import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi.testclient import TestClient
from ap_monitor.app.db import get_wireless_db, get_apclient_db
from ap_monitor.app.main import app, update_ap_data_task, update_client_count_task
from ap_monitor.app.models import (
    AccessPoint, ClientCount, Building, Floor, Campus, ApBuilding, Room, RadioType, ClientCountAP
)
from datetime import datetime, timezone, timedelta
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
from unittest.mock import Mock
from apscheduler.triggers.date import DateTrigger
from ap_monitor.app.main import (
    cleanup_job,
    reschedule_job,
    calculate_next_run_time,
    health_check
)

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
    test_engine = create_engine("sqlite:///test_wireless.db", connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=test_engine)
    session = Session()
    
    try:
        # Drop all tables first to ensure clean state
        WirelessBase.metadata.drop_all(test_engine)
        # Create tables in correct order
        WirelessBase.metadata.create_all(test_engine)
        
        yield session
    finally:
        session.close()
        WirelessBase.metadata.drop_all(test_engine)
        if os.path.exists("test_wireless.db"):
            os.remove("test_wireless.db")

@pytest.fixture(scope="function")
def apclient_db():
    """Create a test database for apclientcount."""
    # Use a file-based SQLite DB to share across connections
    test_engine = create_engine("sqlite:///test_apclient.db", connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=test_engine)
    session = Session()
    
    try:
        # Drop all tables first to ensure clean state
        APClientBase.metadata.drop_all(test_engine)
        # Create tables in correct order
        APClientBase.metadata.create_all(test_engine)
        
        # Create radio types
        radio_types = [
            RadioType(radioname="radio0", radioid=1),
            RadioType(radioname="radio1", radioid=2),
            RadioType(radioname="radio2", radioid=3)
        ]
        for radio_type in radio_types:
            session.add(radio_type)
        session.commit()
        
        # Create test building
        ap_building = ApBuilding(buildingname="Test Building")
        session.add(ap_building)
        session.commit()
        
        # Create test floor
        floor = Floor(buildingid=ap_building.buildingid, floorname="Floor 1")
        session.add(floor)
        session.commit()
        
        yield session
    finally:
        session.close()
        APClientBase.metadata.drop_all(test_engine)
        if os.path.exists("test_apclient.db"):
            os.remove("test_apclient.db")

@pytest.fixture
def test_data(wireless_db, apclient_db):
    logger.info("Setting up test data")
    try:
        # Create wireless_count data
        campus = Campus(campus_name="Keele Campus")
        wireless_db.add(campus)
        wireless_db.commit()

        building = Building(
            building_name="Keele Campus",
            campus_id=campus.campus_id,
            latitude=43.7735473000,
            longitude=-79.5062752000
        )
        wireless_db.add(building)
        wireless_db.commit()

        # Create apclientcount data
        ap_building = ApBuilding(buildingname="Keele Campus")
        apclient_db.add(ap_building)
        apclient_db.commit()

        floor = Floor(
            buildingid=ap_building.buildingid,
            floorname="Floor 5"
        )
        apclient_db.add(floor)
        apclient_db.commit()

        ap = AccessPoint(
            buildingid=ap_building.buildingid,
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
        radio_types = apclient_db.query(RadioType).all()
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

@pytest.fixture
def override_get_db_with_mock_client_counts():
    """Mock fixture for client counts endpoint."""
    # Create mock objects
    mock_ap = MagicMock()
    mock_ap.apname = "k372-ross-5-28"
    mock_ap.apid = 1

    mock_radio = MagicMock()
    mock_radio.radioname = "radio0"
    mock_radio.radioid = 1

    mock_cc = MagicMock()
    mock_cc.clientcount = 15
    mock_cc.apid = 1
    mock_cc.radioid = 1
    mock_cc.timestamp = datetime.now(timezone.utc)
    mock_cc.accesspoint = mock_ap
    mock_cc.radio = mock_radio

    # Set up the query chain
    mock_query = MagicMock()
    mock_query.join.return_value = mock_query
    mock_query.filter.return_value = mock_query
    mock_query.order_by.return_value = mock_query
    mock_query.limit.return_value = mock_query
    mock_query.all.return_value = [mock_cc]

    mock_session = MagicMock()
    mock_session.query.return_value = mock_query

    def override():
        return mock_session

    app.dependency_overrides[get_wireless_db] = override
    app.dependency_overrides[get_apclient_db] = override
    yield
    app.dependency_overrides.clear()

@pytest.fixture
def override_get_db_with_mock_aps():
    """Mock fixture for APs endpoint."""
    mock_ap = MagicMock()
    mock_ap.apid = 1
    mock_ap.apname = "k372-ross-5-28"
    mock_ap.macaddress = "a8:9d:21:b9:67:a0"
    mock_ap.ipaddress = "10.30.2.154"
    mock_ap.modelname = "Cisco 3700I Unified Access Point"
    mock_ap.isactive = True
    mock_ap.buildingid = 1
    mock_ap.floorid = 1
    mock_ap.roomid = None

    mock_query = MagicMock()
    mock_query.all.return_value = [mock_ap]

    mock_session = MagicMock()
    mock_session.query.return_value = mock_query

    def override():
        return mock_session

    app.dependency_overrides[get_wireless_db] = override
    app.dependency_overrides[get_apclient_db] = override
    yield
    app.dependency_overrides.clear()

def test_get_client_counts(client, override_get_db_with_mock_client_counts):
    """Test getting client counts with mock data."""
    response = client.get("/client-counts")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["apname"] == "k372-ross-5-28"
    assert data[0]["clientcount"] == 15

@patch("ap_monitor.app.main.auth_manager")
def test_update_client_count_task(mock_auth, client, override_get_db_with_mock_client_counts):
    """Test client count update task with mock data."""
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
            update_client_count_task(db=MagicMock(), auth_manager_obj=mock_auth, fetch_client_counts_func=mock_fetch, fetch_ap_data_func=MagicMock())

        mock_fetch.assert_called_once_with(mock_auth, ANY)

        logger.debug("Fetching updated client counts")
        response = client.get("/client-counts")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["apname"] == "k372-ross-5-28"
        assert data[0]["clientcount"] == 15
    except Exception as e:
        logger.error(f"Error in client count update test: {str(e)}")
        raise

@patch("ap_monitor.app.main.auth_manager")
def test_update_ap_data_task(mock_auth, client, override_get_db_with_mock_aps):
    """Test AP data update task with mock data."""
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
            "location": "Global/Keele Campus/Bethune Residence/Floor 5/Room 123"
        }
    ]
    try:
        logger.debug("Running update_ap_data_task")
        with patch("ap_monitor.app.main.scheduler.add_job"):
            # Run the update task
            update_ap_data_task(db=MagicMock(), auth_manager_obj=mock_auth, fetch_ap_data_func=mock_fetch)

        mock_fetch.assert_called_once_with(mock_auth, ANY)

        logger.debug("Fetching updated AP data")
        response = client.get("/aps")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["apname"] == "k372-ross-5-28"
        assert data[0]["macaddress"] == "a8:9d:21:b9:67:a0"
    except Exception as e:
        logger.error(f"Error in AP data update test: {str(e)}")
        raise

@pytest.fixture
def mock_scheduler():
    """Create a mock scheduler for testing."""
    scheduler = Mock(spec=BackgroundScheduler)
    scheduler.get_job.return_value = None
    scheduler.get_jobs.return_value = []
    scheduler.running = True
    return scheduler

@pytest.fixture
def mock_db():
    """Create a mock database session."""
    db = Mock()
    db.commit = Mock()
    db.rollback = Mock()
    db.close = Mock()
    return db

@pytest.fixture
def mock_auth_manager():
    """Create a mock auth manager."""
    auth_manager = Mock()
    auth_manager.get_token.return_value = "test_token"
    return auth_manager

def test_cleanup_job(mock_scheduler):
    """Test job cleanup functionality."""
    # Test successful cleanup
    job_id = "test_job"
    mock_scheduler.get_job.return_value = Mock()
    cleanup_job(job_id, scheduler_obj=mock_scheduler)
    mock_scheduler.remove_job.assert_called_once_with(job_id)

    # Test cleanup of non-existent job
    mock_scheduler.reset_mock()
    mock_scheduler.get_job.return_value = None
    cleanup_job(job_id, scheduler_obj=mock_scheduler)
    mock_scheduler.remove_job.assert_not_called()

def test_reschedule_job(mock_scheduler):
    """Test job rescheduling functionality."""
    job_id = "test_job"
    func = Mock()
    next_run = datetime.now(timezone.utc) + timedelta(minutes=5)

    reschedule_job(job_id, func, next_run, scheduler_obj=mock_scheduler)
    mock_scheduler.add_job.assert_called_once()
    call_args = mock_scheduler.add_job.call_args[1]
    assert call_args["func"] == func
    assert call_args["trigger"].run_date == next_run
    assert call_args["id"] == job_id
    assert call_args["replace_existing"] is True

def test_update_ap_data_task_success(mock_db, mock_auth_manager):
    """Test successful AP data update task."""
    mock_ap_data = [
        {
            "deviceName": "test_ap",
            "macAddress": "00:11:22:33:44:55",
            "location": "Test/Location",
            "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000)
        }
    ]

    with patch("ap_monitor.app.main.fetch_ap_data", return_value=mock_ap_data):
        update_ap_data_task(mock_db, mock_auth_manager)
        mock_db.commit.assert_called_once()
        mock_db.rollback.assert_not_called()

def test_update_ap_data_task_failure(mock_db, mock_auth_manager):
    """Test AP data update task with error handling."""
    with patch("ap_monitor.app.main.fetch_ap_data", side_effect=Exception("API Error")):
        with pytest.raises(Exception):
            update_ap_data_task(mock_db, mock_auth_manager)
        mock_db.rollback.assert_called_once()
        mock_db.commit.assert_not_called()

def test_update_client_count_task_success(mock_db, mock_auth_manager, wireless_db):
    """Test successful client count update task."""
    mock_ap_data = [
        {
            "deviceName": "test_ap",
            "macAddress": "00:11:22:33:44:55",
            "location": "Test/Location",
            "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000)
        }
    ]
    mock_site_data = [
        {
            "siteName": "Test Site",
            "clientCount": 10,
            "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000)
        }
    ]

    with patch("ap_monitor.app.main.fetch_ap_data", return_value=mock_ap_data), \
         patch("ap_monitor.app.main.fetch_client_counts", return_value=mock_site_data):
        update_client_count_task(mock_db, mock_auth_manager, wireless_db=wireless_db)
        mock_db.commit.assert_called_once()
        mock_db.rollback.assert_not_called()

def test_update_client_count_task_failure(mock_db, mock_auth_manager):
    """Test client count update task with error handling."""
    with patch("ap_monitor.app.main.fetch_ap_data", side_effect=Exception("API Error")):
        with pytest.raises(Exception):
            update_client_count_task(mock_db, mock_auth_manager)
        mock_db.rollback.assert_called_once()
        mock_db.commit.assert_not_called()

def test_health_check_healthy(mock_scheduler):
    """Test health check endpoint when system is healthy."""
    mock_job = Mock()
    mock_job.id = "test_job"
    mock_job.name = "Test Job"
    mock_job.next_run_time = datetime.now(timezone.utc) + timedelta(minutes=5)
    mock_scheduler.get_jobs.return_value = [mock_job]

    with patch("ap_monitor.app.main.scheduler", mock_scheduler):
        response = health_check()
        assert response["status"] == "healthy"
        assert response["scheduler"]["running"] is True
        assert len(response["scheduler"]["jobs"]) == 1
        assert response["scheduler"]["jobs"][0]["id"] == "test_job"
        assert response["scheduler"]["jobs"][0]["state"] == "running"

def test_health_check_unhealthy(mock_scheduler):
    """Test health check endpoint when system is unhealthy."""
    mock_scheduler.get_jobs.side_effect = Exception("Scheduler Error")

    with patch("ap_monitor.app.main.scheduler", mock_scheduler):
        response = health_check()
        assert response["status"] == "unhealthy"
        assert "error" in response
        assert "Scheduler Error" in response["error"]

def test_calculate_next_run_time():
    """Test next run time calculation."""
    now = datetime.now(timezone.utc)
    next_run = calculate_next_run_time()
    
    # Next run should be in the future
    assert next_run > now
    
    # Next run should be within 5 minutes
    assert next_run - now <= timedelta(minutes=5)
    
    # Next run should be on a 5-minute boundary
    assert next_run.minute % 5 == 0
    assert next_run.second == 0
    assert next_run.microsecond == 0

def test_wireless_count_db_creation(wireless_db):
    """Test that wireless_count database tables are created correctly."""
    # Check if tables exist
    inspector = inspect(wireless_db.get_bind())
    tables = inspector.get_table_names()
    
    # Verify essential tables exist
    assert 'buildings' in tables
    assert 'client_counts' in tables
    assert 'campuses' in tables
    
    # Verify table structures
    buildings_columns = {col['name'] for col in inspector.get_columns('buildings')}
    assert 'building_id' in buildings_columns
    assert 'building_name' in buildings_columns
    assert 'campus_id' in buildings_columns
    assert 'latitude' in buildings_columns
    assert 'longitude' in buildings_columns
    
    client_counts_columns = {col['name'] for col in inspector.get_columns('client_counts')}
    assert 'count_id' in client_counts_columns
    assert 'building_id' in client_counts_columns
    assert 'client_count' in client_counts_columns
    assert 'time_inserted' in client_counts_columns

def test_wireless_count_data_update(wireless_db, apclient_db):
    """Test that client counts are properly aggregated and stored in wireless_count DB."""
    # Create test data
    campus = Campus(campus_name="Test Campus 1")  # Changed to avoid unique constraint
    wireless_db.add(campus)
    wireless_db.commit()
    
    building = Building(
        building_name="Test Building 1",  # Changed to avoid unique constraint
        campus_id=campus.campus_id,
        latitude=43.7735473000,
        longitude=-79.5062752000
    )
    wireless_db.add(building)
    wireless_db.commit()
    
    # Create AP building and access points
    ap_building = ApBuilding(buildingname="Test Building 1")  # Match the building name
    apclient_db.add(ap_building)
    apclient_db.commit()
    
    floor = Floor(buildingid=ap_building.buildingid, floorname="Floor 1")
    apclient_db.add(floor)
    apclient_db.commit()
    
    # Create test APs with client counts
    test_aps = [
        {
            "name": "AP1",
            "macAddress": "00:11:22:33:44:55",
            "ipAddress": "192.168.1.1",
            "model": "Test Model",
            "reachabilityHealth": "UP",
            "location": "Test Building 1/Floor 1",  # Match the building name
            "clientCount": {
                "radio0": 10,
                "radio1": 15,
                "radio2": 5
            }
        },
        {
            "name": "AP2",
            "macAddress": "00:11:22:33:44:56",
            "ipAddress": "192.168.1.2",
            "model": "Test Model",
            "reachabilityHealth": "UP",
            "location": "Test Building 1/Floor 1",  # Match the building name
            "clientCount": {
                "radio0": 20,
                "radio1": 25,
                "radio2": 15
            }
        }
    ]
    
    # Mock the API responses
    mock_auth = Mock()
    mock_auth.get_token.return_value = "test_token"
    
    with patch("ap_monitor.app.main.fetch_ap_data", return_value=test_aps), \
         patch("ap_monitor.app.main.fetch_client_counts", return_value=[]), \
         patch("ap_monitor.app.main.scheduler.add_job"):
        
        # Run the update task
        update_client_count_task(db=apclient_db, auth_manager_obj=mock_auth, wireless_db=wireless_db)
        
        # Verify wireless_count DB updates
        client_counts = wireless_db.query(ClientCount).filter_by(building_id=building.building_id).all()
        assert len(client_counts) > 0
        
        # Calculate expected total (sum of all radio counts for all APs)
        expected_total = sum(
            sum(ap["clientCount"].values())
            for ap in test_aps
        )
        
        # Verify the total client count
        latest_count = wireless_db.query(ClientCount)\
            .filter_by(building_id=building.building_id)\
            .order_by(ClientCount.time_inserted.desc())\
            .first()
        
        assert latest_count is not None
        assert latest_count.client_count == expected_total

def test_wireless_count_multiple_updates(wireless_db, apclient_db):
    """Test that multiple updates to wireless_count DB work correctly."""
    # Create test data
    campus = Campus(campus_name="Test Campus 2")  # Changed to avoid unique constraint
    wireless_db.add(campus)
    wireless_db.commit()
    
    building = Building(
        building_name="Test Building 2",  # Changed to avoid unique constraint
        campus_id=campus.campus_id,
        latitude=43.7735473000,
        longitude=-79.5062752000
    )
    wireless_db.add(building)
    wireless_db.commit()
    
    # Create AP building
    ap_building = ApBuilding(buildingname="Test Building 2")  # Match the building name
    apclient_db.add(ap_building)
    apclient_db.commit()
    
    floor = Floor(buildingid=ap_building.buildingid, floorname="Floor 1")
    apclient_db.add(floor)
    apclient_db.commit()
    
    # Test data for two different time points
    test_data = [
        {
            "aps": [
                {
                    "name": "AP1",
                    "macAddress": "00:11:22:33:44:55",
                    "ipAddress": "192.168.1.1",
                    "model": "Test Model",
                    "reachabilityHealth": "UP",
                    "location": "Test Building 2/Floor 1",  # Match the building name
                    "clientCount": {
                        "radio0": 10,
                        "radio1": 15,
                        "radio2": 5
                    }
                }
            ],
            "expected_total": 30
        },
        {
            "aps": [
                {
                    "name": "AP1",
                    "macAddress": "00:11:22:33:44:55",
                    "ipAddress": "192.168.1.1",
                    "model": "Test Model",
                    "reachabilityHealth": "UP",
                    "location": "Test Building 2/Floor 1",  # Match the building name
                    "clientCount": {
                        "radio0": 20,
                        "radio1": 25,
                        "radio2": 15
                    }
                }
            ],
            "expected_total": 60
        }
    ]
    
    mock_auth = Mock()
    mock_auth.get_token.return_value = "test_token"
    
    # Run updates for each test data set
    for test_set in test_data:
        with patch("ap_monitor.app.main.fetch_ap_data", return_value=test_set["aps"]), \
             patch("ap_monitor.app.main.fetch_client_counts", return_value=[]), \
             patch("ap_monitor.app.main.scheduler.add_job"):
            
            update_client_count_task(db=apclient_db, auth_manager_obj=mock_auth, wireless_db=wireless_db)
            
            # Verify the update
            latest_count = wireless_db.query(ClientCount)\
                .filter_by(building_id=building.building_id)\
                .order_by(ClientCount.time_inserted.desc())\
                .first()
            
            assert latest_count is not None
            assert latest_count.client_count == test_set["expected_total"]
    
    # Verify we have multiple records
    all_counts = wireless_db.query(ClientCount)\
        .filter_by(building_id=building.building_id)\
        .order_by(ClientCount.time_inserted.desc())\
        .all()
    
    assert len(all_counts) >= len(test_data)



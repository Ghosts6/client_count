import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi.testclient import TestClient
from ap_monitor.app.db import get_wireless_db, get_apclient_db
from ap_monitor.app.main import app, update_ap_data_task, update_client_count_task, TORONTO_TZ
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
from apscheduler.triggers.cron import CronTrigger
from ap_monitor.app.dna_api import fetch_ap_client_data_with_fallback
from urllib.error import HTTPError

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

    from ap_monitor.app.db import get_wireless_db_dep, get_apclient_db_dep
    app.dependency_overrides[get_wireless_db] = override
    app.dependency_overrides[get_apclient_db] = override
    app.dependency_overrides[get_wireless_db_dep] = override
    app.dependency_overrides[get_apclient_db_dep] = override
    yield
    app.dependency_overrides.clear()

@pytest.fixture
def override_get_db_with_mock_buildings():
    mock_building = MagicMock()
    mock_building.building_id = 1
    mock_building.building_name = "BuildingA"

    mock_query = MagicMock()
    mock_query.all.return_value = [mock_building]

    mock_session = MagicMock()
    mock_session.query.return_value = mock_query

    def override():
        yield mock_session

    from ap_monitor.app.db import get_wireless_db_dep, get_apclient_db_dep
    app.dependency_overrides[get_wireless_db] = override
    app.dependency_overrides[get_apclient_db] = override
    app.dependency_overrides[get_wireless_db_dep] = override
    app.dependency_overrides[get_apclient_db_dep] = override
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
    """Mock fixture for AP client counts endpoint (ClientCountAP model)."""
    # Create mock objects
    mock_ap = MagicMock()
    mock_ap.apname = "k372-ross-5-28"
    mock_ap.apid = 1

    mock_radio = MagicMock()
    mock_radio.radioname = "radio0"
    mock_radio.radioid = 1

    mock_cc = MagicMock()
    mock_cc.countid = 1
    mock_cc.clientcount = 15
    mock_cc.apid = 1
    mock_cc.radioid = 1
    mock_cc.timestamp = datetime.now(timezone.utc)
    mock_cc.accesspoint = mock_ap
    mock_cc.radio = mock_radio
    # No building_id for ClientCountAP

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
    from ap_monitor.app.db import get_apclient_db_dep
    app.dependency_overrides[get_apclient_db_dep] = override
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
        yield mock_session

    from ap_monitor.app.db import get_wireless_db_dep, get_apclient_db_dep
    app.dependency_overrides[get_wireless_db] = override
    app.dependency_overrides[get_apclient_db] = override
    app.dependency_overrides[get_wireless_db_dep] = override
    app.dependency_overrides[get_apclient_db_dep] = override
    yield
    app.dependency_overrides.clear()

def test_get_client_counts(client, override_get_db_with_mock_client_counts):
    """Test getting AP client counts with mock data (ClientCountAP model)."""
    response = client.get("/client-counts")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    # Check that the response contains the expected keys
    assert "apid" in data[0]
    assert "radioid" in data[0]
    assert "client_count" in data[0]
    assert "timestamp" in data[0]
    assert "count_id" in data[0]

@patch("ap_monitor.app.main.auth_manager")
def test_update_client_count_task(mock_auth, client, override_get_db_with_mock_client_counts):
    """Test client count update task with mock data."""
    logger.info("Starting client count update test")
    mock_auth.get_token.return_value = "test_token"
    test_data = [
        {
            "hostname": "k372-ross-5-28",
            "macAddress": "a8:9d:21:b9:67:a0",
            "ipAddress": "10.30.2.154",
            "model": "Cisco 3700I Unified Access Point",
            "reachabilityStatus": "UP",
            "location": "Global/Keele Campus/Bethune Residence/Floor 5",
            "clientCount": 60
        }
    ]
    try:
        logger.debug("Running update_client_count_task")
        with patch("ap_monitor.app.main.fetch_ap_client_data_with_fallback") as mock_fetch:
            mock_fetch.return_value = {'source': 'networkDevices', 'data': test_data}
            update_client_count_task(db=MagicMock(), auth_manager_obj=mock_auth)
            mock_fetch.assert_called_once_with(mock_auth)
    except Exception as e:
        logger.error(f"Error in client count update test: {e}")
        raise

def raise_http_500(*args, **kwargs):
    from urllib.error import HTTPError
    raise HTTPError(url=None, code=500, msg="Internal Server Error", hdrs=None, fp=None)

import ap_monitor.app.main as main_module

def test_update_ap_data_task_sets_global_maintenance(monkeypatch, caplog):
    # Patch db session
    mock_db = Mock()
    # Reset global maintenance window
    main_module.MAINTENANCE_UNTIL = None
    # Run the task with the mock fetch_ap_data_func that always raises HTTP 500
    with caplog.at_level("ERROR"):
        main_module.update_ap_data_task(db=mock_db, fetch_ap_data_func=raise_http_500)
    # Check that the maintenance window is set
    assert main_module.MAINTENANCE_UNTIL is not None
    # Check that the log contains the maintenance message
    assert any("Entering maintenance until" in r.message for r in caplog.records)


def test_update_ap_data_task_skips_during_maintenance(monkeypatch, caplog):
    # Patch db session
    mock_db = Mock()
    # Set global maintenance window to the future
    from datetime import datetime, timedelta, timezone
    future_time = datetime.now(timezone.utc) + timedelta(minutes=30)
    main_module.MAINTENANCE_UNTIL = future_time
    # Patch fetch_ap_data_func to fail if called
    def fail_fetch(*args, **kwargs):
        pytest.fail("fetch_ap_data_func should not be called during maintenance window")
    # Run the task
    with caplog.at_level("WARNING"):
        main_module.update_ap_data_task(db=mock_db, fetch_ap_data_func=fail_fetch)
    # Check that the log contains the skip message
    assert any("In maintenance window until" in r.message for r in caplog.records)

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
    main_module.MAINTENANCE_UNTIL = None  # Ensure not in maintenance
    mock_ap_data = [
        {
            "deviceName": "test_ap",
            "macAddress": "00:11:22:33:44:55",
            "location": "Test/Location",
            "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000)
        }
    ]

    with patch("ap_monitor.app.main.fetch_ap_data", return_value=mock_ap_data):
        main_module.update_ap_data_task(mock_db, mock_auth_manager)
        mock_db.commit.assert_called_once()
        mock_db.rollback.assert_not_called()

def test_update_ap_data_task_failure(mock_db, mock_auth_manager):
    main_module.MAINTENANCE_UNTIL = None  # Ensure not in maintenance
    with patch("ap_monitor.app.main.fetch_ap_data", side_effect=Exception("API Error")):
        with pytest.raises(Exception):
            main_module.update_ap_data_task(mock_db, mock_auth_manager)
        mock_db.rollback.assert_called_once()
        mock_db.commit.assert_not_called()

def test_update_client_count_task_success(mock_db, mock_auth_manager, wireless_db):
    main_module.MAINTENANCE_UNTIL = None  # Ensure not in maintenance
    mock_ap_data = [
        {
            "macAddress": "00:11:22:33:44:55",
            "name": "test_ap",
            "location": "Test/Location",
            "clientCount": 10,
            "status": "ok"
        }
    ]
    with patch("ap_monitor.app.main.fetch_ap_client_data_with_fallback") as mock_fetch:
        mock_fetch.return_value = mock_ap_data
        main_module.update_client_count_task(mock_db, mock_auth_manager, wireless_db=wireless_db)
        mock_db.commit.assert_called_once()

@pytest.mark.parametrize("mock_ap_data,expected_status,expect_commit", [
    ([{"macAddress": "00:11:22:33:44:55", "name": "test_ap", "location": "Test/Location", "clientCount": 10, "status": "ok"}], "ok", True),
    ([{"macAddress": "00:11:22:33:44:56", "name": "test_ap2", "location": "Test/Location2", "clientCount": 0, "status": "fallback"}], "fallback", True),
    ([{"macAddress": "00:11:22:33:44:57", "name": "test_ap3", "location": "Test/Location3", "clientCount": None, "status": "unavailable"}], "unavailable", True),
    ([], None, False),
])
def test_update_client_count_task_fallback_cases(mock_db, mock_auth_manager, wireless_db, mock_ap_data, expected_status, expect_commit):
    main_module.MAINTENANCE_UNTIL = None  # Ensure not in maintenance
    with patch("ap_monitor.app.main.fetch_ap_client_data_with_fallback") as mock_fetch:
        mock_fetch.return_value = mock_ap_data
        main_module.update_client_count_task(mock_db, mock_auth_manager, wireless_db=wireless_db)
        if expect_commit:
            mock_db.commit.assert_called()
        else:
            mock_db.commit.assert_not_called()

def test_update_client_count_task_failure(mock_db, mock_auth_manager):
    main_module.MAINTENANCE_UNTIL = None  # Ensure not in maintenance
    with patch("ap_monitor.app.main.fetch_ap_client_data_with_fallback", side_effect=Exception("API Error")) as mock_fetch:
        with pytest.raises(Exception):
            main_module.update_client_count_task(mock_db, mock_auth_manager)
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

def test_scheduler_configuration():
    """Test that scheduler is configured correctly with 5-minute intervals."""
    # Create scheduler
    scheduler = BackgroundScheduler(timezone=timezone.utc)
    
    try:
        # Add test jobs
        scheduler.add_job(
            lambda: None,
            'cron',
            minute='*/5',
            second=0,
            id='test_job'
        )
        
        # Start the scheduler
        scheduler.start()
        
        # Get job
        job = scheduler.get_job('test_job')
        
        # Verify job configuration
        assert isinstance(job.trigger, CronTrigger)
        
        # Calculate next run time
        now = datetime.now(timezone.utc)
        next_run = job.next_run_time
        
        # Verify next run is at next 5-minute mark
        assert next_run.minute % 5 == 0
        assert next_run.second == 0
        assert next_run.microsecond == 0
        
        # Verify next run is in the future
        assert next_run > now
        
        # Verify time difference is less than 5 minutes
        time_diff = next_run - now
        assert timedelta(0) <= time_diff <= timedelta(minutes=5)
        
        # Calculate next few run times to verify 5-minute intervals
        next_runs = []
        current_time = next_run
        for _ in range(3):
            current_time = job.trigger.get_next_fire_time(current_time, current_time)
            if current_time:
                next_runs.append(current_time)
        
        # Verify intervals between runs are 5 minutes
        for i in range(len(next_runs) - 1):
            interval = next_runs[i + 1] - next_runs[i]
            assert interval == timedelta(minutes=5)
            
    finally:
        # Clean up
        scheduler.shutdown()

def test_calculate_next_run_time():
    """Test next run time calculation."""
    now = datetime.now(TORONTO_TZ)
    next_run = calculate_next_run_time()
    
    # Next run should be in the future
    assert next_run > now
    
    # Next run should be approximately 4 to 5 minutes from now (allowing for execution time and rounding)
    time_diff = next_run - now
    assert timedelta(minutes=4) <= time_diff <= timedelta(minutes=5)
    
    # Next run should have zero seconds and microseconds
    assert next_run.second == 0
    assert next_run.microsecond == 0

def test_task_rescheduling():
    """Test that tasks are rescheduled 5 minutes after completion."""
    # Create scheduler
    scheduler = BackgroundScheduler(timezone=timezone.utc)
    
    try:
        # Add test job
        scheduler.add_job(
            lambda: None,
            'interval',
            minutes=5,
            id='test_job'
        )
        
        # Start the scheduler
        scheduler.start()
        
        # Get job
        job = scheduler.get_job('test_job')
        
        # Simulate task completion and rescheduling
        now = datetime.now(timezone.utc)
        
        # Reschedule the job with a new trigger
        scheduler.reschedule_job(
            'test_job',
            trigger='interval',
            minutes=5
        )
        
        # Get updated job
        job = scheduler.get_job('test_job')
        
        # Verify interval
        assert job.trigger.interval == timedelta(minutes=5)
        
        # Verify next run is approximately 5 minutes from now
        time_diff = job.next_run_time - now
        assert timedelta(minutes=4, seconds=55) <= time_diff <= timedelta(minutes=5, seconds=5)
        
    finally:
        scheduler.shutdown()

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
    campus = Campus(campus_name="Test Campus 1")
    wireless_db.add(campus)
    wireless_db.commit()
    building = Building(
        building_name="Test Building 1",
        campus_id=campus.campus_id,
        latitude=43.7735473000,
        longitude=-79.5062752000
    )
    wireless_db.add(building)
    wireless_db.commit()
    fresh_building = wireless_db.query(Building).filter_by(building_name="Test Building 1").first()
    building_id = fresh_building.building_id
    ap_building = ApBuilding(buildingname="Test Building 1")
    apclient_db.add(ap_building)
    apclient_db.commit()
    floor = Floor(buildingid=ap_building.buildingid, floorname="Floor 1")
    apclient_db.add(floor)
    apclient_db.commit()
    test_aps = [
        {
            "macAddress": "00:11:22:33:44:55",
            "name": "AP1",
            "location": "Test Building 1/Floor 1",
            "clientCount": 30,
            "status": "ok"
        },
        {
            "macAddress": "00:11:22:33:44:56",
            "name": "AP2",
            "location": "Test Building 1/Floor 1",
            "clientCount": 60,
            "status": "ok"
        }
    ]
    mock_auth = Mock()
    mock_auth.get_token.return_value = "test_token"
    with patch("ap_monitor.app.main.fetch_ap_client_data_with_fallback") as mock_fetch:
        mock_fetch.return_value = test_aps
        update_client_count_task(db=apclient_db, auth_manager_obj=mock_auth, wireless_db=wireless_db)
        client_counts = wireless_db.query(ClientCount).filter_by(building_id=building_id).all()
        assert len(client_counts) > 0

def test_wireless_count_multiple_updates(wireless_db, apclient_db):
    """Test that multiple updates to wireless_count DB work correctly."""
    campus = Campus(campus_name="Test Campus 2")
    wireless_db.add(campus)
    wireless_db.commit()
    building = Building(
        building_name="Test Building 2",
        campus_id=campus.campus_id,
        latitude=43.7735473000,
        longitude=-79.5062752000
    )
    wireless_db.add(building)
    wireless_db.commit()
    fresh_building = wireless_db.query(Building).filter_by(building_name="Test Building 2").first()
    building_id = fresh_building.building_id
    ap_building = ApBuilding(buildingname="Test Building 2")
    apclient_db.add(ap_building)
    apclient_db.commit()
    floor = Floor(buildingid=ap_building.buildingid, floorname="Floor 1")
    apclient_db.add(floor)
    apclient_db.commit()
    test_data = [
        {
            "macAddress": "00:11:22:33:44:55",
            "name": "AP1",
            "location": "Test Building 2/Floor 1",
            "clientCount": 30,
            "status": "ok"
        },
        {
            "macAddress": "00:11:22:33:44:55",
            "name": "AP1",
            "location": "Test Building 2/Floor 1",
            "clientCount": 60,
            "status": "ok"
        }
    ]
    mock_auth = Mock()
    mock_auth.get_token.return_value = "test_token"
    for ap_data in test_data:
        with patch("ap_monitor.app.main.fetch_ap_client_data_with_fallback") as mock_fetch:
            mock_fetch.return_value = [ap_data]
            update_client_count_task(db=apclient_db, auth_manager_obj=mock_auth, wireless_db=wireless_db)
            latest_count = wireless_db.query(ClientCount)\
                .filter_by(building_id=building_id)\
                .order_by(ClientCount.time_inserted.desc())\
                .first()
            assert latest_count is not None

def test_get_client_counts_with_new_dep(client):
    """Test /client-counts endpoint with the new FastAPI-compatible dependency."""
    from ap_monitor.app.db import get_apclient_db_dep
    # Prepare mock session and data
    mock_ap = MagicMock()
    mock_ap.apname = "k372-ross-5-28"
    mock_ap.apid = 1

    mock_radio = MagicMock()
    mock_radio.radioname = "radio0"
    mock_radio.radioid = 1

    mock_cc = MagicMock()
    mock_cc.countid = 1
    mock_cc.clientcount = 15
    mock_cc.apid = 1
    mock_cc.radioid = 1
    mock_cc.timestamp = datetime.now(timezone.utc)
    mock_cc.accesspoint = mock_ap
    mock_cc.radio = mock_radio
    # No building_id for ClientCountAP

    mock_query = MagicMock()
    mock_query.filter.return_value = mock_query
    mock_query.all.return_value = [mock_cc]

    mock_session = MagicMock()
    mock_session.query.return_value = mock_query

    def override():
        yield mock_session

    app.dependency_overrides[get_apclient_db_dep] = override
    response = client.get("/client-counts")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["client_count"] == 15
    assert data[0]["count_id"] == 1
    assert data[0]["apid"] == 1
    assert data[0]["radioid"] == 1
    assert "timestamp" in data[0]
    app.dependency_overrides.clear()

def test_update_client_count_task_fallback_network_devices(apclient_db, wireless_db):
    campus = Campus(campus_name="Test Campus")
    wireless_db.add(campus)
    wireless_db.commit()
    building = Building(building_name="Ross", campus_id=campus.campus_id, latitude=0, longitude=0)
    wireless_db.add(building)
    wireless_db.commit()
    ap_building = ApBuilding(buildingname="Ross")
    apclient_db.add(ap_building)
    apclient_db.commit()
    floor = Floor(buildingid=ap_building.buildingid, floorname="Floor 1")
    apclient_db.add(floor)
    apclient_db.commit()
    ap_data = {
        "macAddress": "00:11:22:33:44:55",
        "name": "AP1",
        "location": "Ross/Floor 1",
        "clientCount": 5,
        "status": "ok"
    }
    with patch('ap_monitor.app.main.fetch_ap_client_data_with_fallback') as mock_fetch:
        mock_fetch.return_value = [ap_data]
        update_client_count_task(db=apclient_db, auth_manager_obj=Mock(), wireless_db=wireless_db)
        result = wireless_db.query(ClientCount).all()
        assert any(cc.client_count == 5 for cc in result)

def test_update_client_count_task_fallback_clients(apclient_db, wireless_db):
    campus = Campus(campus_name="Test Campus")
    wireless_db.add(campus)
    wireless_db.commit()
    building = Building(building_name="Scott Library", campus_id=campus.campus_id, latitude=0, longitude=0)
    wireless_db.add(building)
    wireless_db.commit()
    ap_building = ApBuilding(buildingname="Scott Library")
    apclient_db.add(ap_building)
    apclient_db.commit()
    floor = Floor(buildingid=ap_building.buildingid, floorname="Floor 2")
    apclient_db.add(floor)
    apclient_db.commit()
    ap_data = {
        "macAddress": "00:11:22:33:44:66",
        "name": "AP2",
        "location": "Scott Library/Floor 2",
        "clientCount": 2,
        "status": "fallback"
    }
    with patch('ap_monitor.app.main.fetch_ap_client_data_with_fallback') as mock_fetch:
        mock_fetch.return_value = [ap_data]
        update_client_count_task(db=apclient_db, auth_manager_obj=Mock(), wireless_db=wireless_db)
        result = wireless_db.query(ClientCount).all()
        assert any(cc.client_count == 2 for cc in result)

def test_update_client_count_task_fallback_site_health(apclient_db, wireless_db):
    campus = Campus(campus_name="Test Campus")
    wireless_db.add(campus)
    wireless_db.commit()
    building = Building(building_name="BuildingC", campus_id=campus.campus_id, latitude=0, longitude=0)
    wireless_db.add(building)
    wireless_db.commit()
    building_id = building.building_id  # Store before session expires
    ap_data = {
        "macAddress": None,
        "name": "BuildingC",
        "location": "BuildingC",
        "clientCount": 7,
        "status": "siteHealth"
    }
    with patch('ap_monitor.app.main.fetch_ap_client_data_with_fallback') as mock_fetch:
        mock_fetch.return_value = [ap_data]
        update_client_count_task(db=apclient_db, auth_manager_obj=Mock(), wireless_db=wireless_db)
        result = wireless_db.query(ClientCount).filter_by(building_id=building_id).all()
        assert any(cc.client_count == 0 for cc in result)

def test_update_client_count_task_fallback_clients_count(apclient_db, wireless_db):
    campus = Campus(campus_name="Test Campus")
    wireless_db.add(campus)
    wireless_db.commit()
    building = Building(building_name="Unknown", campus_id=campus.campus_id, latitude=0, longitude=0)
    wireless_db.add(building)
    wireless_db.commit()
    building_id = building.building_id  # Store before session expires
    ap_data = {
        "macAddress": None,
        "name": "Unknown",
        "location": "Unknown",
        "clientCount": 3,
        "status": "clients/count"
    }
    with patch('ap_monitor.app.main.fetch_ap_client_data_with_fallback') as mock_fetch:
        mock_fetch.return_value = [ap_data]
        update_client_count_task(db=apclient_db, auth_manager_obj=Mock(), wireless_db=wireless_db)
        result = wireless_db.query(ClientCount).filter_by(building_id=building_id).all()
        assert any(cc.client_count == 0 for cc in result)

def test_update_client_count_task_fallback_none(apclient_db, wireless_db):
    with patch('ap_monitor.app.main.fetch_ap_client_data_with_fallback') as mock_fetch:
        mock_fetch.return_value = {
            'source': 'none',
            'data': []
        }
        update_client_count_task(db=apclient_db, auth_manager_obj=Mock(), wireless_db=wireless_db)
        result = wireless_db.query(ClientCount).all()
        assert len(result) == 0

def test_update_client_count_task_dict_response(mock_db, mock_auth_manager, caplog):
    """
    Test update_client_count_task handles the case where fetch_ap_client_data_with_fallback returns a dict (API error/rate limit).
    Should log the error and return early without processing or committing.
    """
    import ap_monitor.app.main as main_module
    main_module.MAINTENANCE_UNTIL = None  # Ensure not in maintenance
    error_dict = {"error": "API rate limit", "status": 429}
    with patch("ap_monitor.app.main.fetch_ap_client_data_with_fallback", return_value=error_dict) as mock_fetch:
        with caplog.at_level("ERROR"):
            main_module.update_client_count_task(mock_db, mock_auth_manager)
            # Should log the error about dict response
            assert any("fetch_ap_client_data_with_fallback returned a dict" in r for r in caplog.text.splitlines())
        mock_db.commit.assert_not_called()
        mock_db.rollback.assert_not_called()

def test_update_ap_data_task_without_db(monkeypatch):
    """Test update_ap_data_task creates and closes its own DB session if none is provided."""
    # Mock fetch_ap_data_func to return minimal valid AP data
    mock_ap_data = [{
        "name": "TestAP",
        "macAddress": "00:11:22:33:44:55",
        "ipAddress": "192.168.1.100",
        "model": "ModelX",
        "reachabilityHealth": "UP",
        "location": "Test Building/Floor 1",
        "clientCount": {"radio0": 5, "radio1": 3}
    }]
    def mock_fetch_ap_data(auth_manager_obj, rounded_unix_timestamp):
        return mock_ap_data
    # Patch time.sleep to avoid real waiting
    monkeypatch.setattr("time.sleep", lambda x: None)
    # Call the function without db argument
    try:
        update_ap_data_task(db=None, auth_manager_obj=None, fetch_ap_data_func=mock_fetch_ap_data, retries=0)
    except Exception as e:
        pytest.fail(f"update_ap_data_task raised an exception when called without db: {e}")



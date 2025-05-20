import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from ap_monitor.app.db import get_db
from ap_monitor.app.main import app, update_ap_data_task, update_client_count_task
from ap_monitor.app.models import AccessPoint, ClientCount, Building, Floor, Campus, ApBuilding, Room, RadioType, ClientCountAP
from datetime import datetime, timezone

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

    app.dependency_overrides[get_db] = override
    yield
    app.dependency_overrides.clear()

@pytest.fixture
def override_get_db_with_mock_buildings():
    mock_building = MagicMock()
    mock_building.buildingid = 1
    mock_building.buildingname = "BuildingA"
    
    # Create mock floors with access points
    mock_floor1 = MagicMock()
    mock_floor2 = MagicMock()
    mock_floor1.accesspoints = MagicMock()
    mock_floor2.accesspoints = MagicMock()
    
    # Set up the len() behavior for floors and accesspoints
    mock_building.floors = MagicMock()
    mock_building.floors.__iter__.return_value = [mock_floor1, mock_floor2]
    mock_building.floors.__len__.return_value = 2
    
    mock_floor1.accesspoints.__iter__.return_value = [MagicMock(), MagicMock(), MagicMock()]
    mock_floor1.accesspoints.__len__.return_value = 3
    mock_floor2.accesspoints.__iter__.return_value = [MagicMock(), MagicMock()]
    mock_floor2.accesspoints.__len__.return_value = 2

    mock_query = MagicMock()
    mock_query.all.return_value = [mock_building]

    mock_session = MagicMock()
    mock_session.query.return_value = mock_query

    def override():
        yield mock_session

    app.dependency_overrides[get_db] = override
    yield
    app.dependency_overrides.clear()

@pytest.fixture()
def client(session):
    app.dependency_overrides[get_db] = lambda: iter([session])
    yield TestClient(app=app)  # Explicitly pass 'app' as a keyword argument
    app.dependency_overrides.clear()

def test_get_aps(client, override_get_db_with_mock_ap):
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
    response = client.get("/buildings")
    assert response.status_code == 200
    assert response.json() == [{
        "buildingid": 1,
        "buildingname": "BuildingA",
        "floor_count": 2,
        "ap_count": 5
    }]

def test_get_client_counts(client, session):
    building = ApBuilding(buildingname="TestBuilding")
    session.add(building)
    session.flush()

    floor = Floor(floorname="1", buildingid=building.buildingid)
    session.add(floor)
    session.flush()

    radio = RadioType(radioid=1, radioname="radio0")
    session.add(radio)
    session.flush()

    ap = AccessPoint(
        apname="TestAP",
        macaddress="00:11:22:33:44:55",
        ipaddress="192.168.0.1",
        modelname="ModelX",
        isactive=True,
        floorid=floor.floorid,
        buildingid=building.buildingid
    )
    session.add(ap)
    session.flush()

    client_count = ClientCountAP(
        apid=ap.apid,
        radioid=radio.radioid,
        clientcount=5,
        timestamp=datetime.now(timezone.utc)
    )
    session.add(client_count)
    session.commit()

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    
    try:
        response = client.get("/client-counts")
        assert response.status_code == 200
        data = response.json()
        assert len(data) > 0
        assert data[0]["clientcount"] == 5
        assert data[0]["apname"] == "TestAP"
    finally:
        app.dependency_overrides.clear()

@patch("ap_monitor.app.main.fetch_client_counts")
@patch("ap_monitor.app.main.auth_manager")
@patch("ap_monitor.app.main.fetch_ap_data")
def test_update_client_count_task(mock_fetch_ap, mock_auth, mock_fetch, session):
    # Mock the auth manager
    mock_auth.get_token.return_value = "test_token"
    mock_auth.headers = {"Authorization": "Bearer test_token"}

    # Mock the AP data fetch
    mock_fetch_ap.return_value = [
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
    
    # Clean up any existing data
    session.query(ClientCountAP).delete()
    session.query(AccessPoint).delete()
    session.query(ApBuilding).delete()
    session.query(Floor).delete()
    session.query(RadioType).delete()
    session.commit()

    # Create required records
    building = ApBuilding(buildingname="TestBuilding")
    session.add(building)
    session.flush()

    floor = Floor(floorname="TestFloor", buildingid=building.buildingid)
    session.add(floor)
    session.flush()

    radio = RadioType(radioid=1, radioname="radio0")
    session.add(radio)
    session.commit()

    # Create an access point
    ap = AccessPoint(
        apname="TestAP",
        macaddress="00:11:22:33:44:55",
        ipaddress="192.168.1.1",
        modelname="ModelX",
        isactive=True,
        floorid=floor.floorid,
        buildingid=building.buildingid
    )
    session.add(ap)
    session.commit()

    app.dependency_overrides[get_db] = lambda: (s for s in [session])
    update_client_count_task(session)
    app.dependency_overrides.clear()

    results = session.query(ClientCountAP).all()
    assert len(results) > 0
    assert results[0].clientcount == 10
    assert results[0].radioid == 1

@patch("ap_monitor.app.main.fetch_ap_data")
@patch("ap_monitor.app.main.auth_manager")
def test_update_ap_data_task(mock_auth, mock_fetch, session):
    mock_fetch.return_value = [
        {
            "name": "AP01",
            "macAddress": "AA:BB:CC:DD:EE:FF",
            "ipAddress": "10.0.0.1",
            "model": "ModelY",
            "reachabilityHealth": "UP",
            "clientCount": {"radio0": 4},
            "location": "/Global/Campus/TestBuilding/TestFloor/TestRoom",
            "latitude": 43.7,
            "longitude": -79.4
        }
    ]

    # Clean up dependent records in the correct order
    session.query(ClientCountAP).delete()
    session.query(AccessPoint).delete()
    session.query(RadioType).delete()
    session.commit()

    radio = RadioType(radioid=1, radioname="radio0")
    session.add(radio)
    session.commit()

    # Override get_db to use the in-memory SQLite session
    app.dependency_overrides[get_db] = lambda: iter([session])

    try:
        update_ap_data_task(session)

        aps = session.query(AccessPoint).all()
        assert len(aps) == 1
        assert aps[0].macaddress == "AA:BB:CC:DD:EE:FF"
        assert aps[0].apname == "AP01"
        assert aps[0].isactive is True

        # Check client count was created
        client_counts = session.query(ClientCountAP).all()
        assert len(client_counts) == 1
        assert client_counts[0].clientcount == 4
        assert client_counts[0].radioid == 1
    finally:
        app.dependency_overrides.clear()



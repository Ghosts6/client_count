import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from app.db import get_db
from app.main import app, update_ap_data_task, update_client_count_task
from app.models import AccessPoint, ClientCount, Radio, Floor, Building
from datetime import datetime

@pytest.fixture
def override_get_db_with_mock_ap():
    mock_ap = MagicMock()
    mock_ap.id = 1
    mock_ap.name = "AP01"
    mock_ap.status = "UP"
    mock_ap.clients = 5
    mock_ap.updated_at = "2024-01-01T12:00:00"

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
    mock_query = MagicMock()
    mock_query.distinct.return_value.all.return_value = [("BuildingA",)]

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
    yield TestClient(app)
    app.dependency_overrides.clear()

def test_get_aps(client, override_get_db_with_mock_ap):
    response = client.get("/aps")
    assert response.status_code == 200
    assert response.json() == [{
        "id": 1,
        "name": "AP01",
        "status": "UP",
        "clients": 5,
        "updated_at": "2024-01-01T12:00:00"
    }]

def test_get_buildings(client, override_get_db_with_mock_buildings):
    response = client.get("/buildings")
    assert response.status_code == 200
    assert response.json() == ["BuildingA"]

def test_get_client_counts(client, session):
    building = Building(name="TestBuilding", latitude=0.0, longitude=0.0)
    session.add(building)
    session.flush()

    floor = Floor(number=1, building_id=building.id)
    session.add(floor)
    session.flush()

    radio = Radio(id=1, name="radio0", description="2.4 GHz")
    session.add(radio)
    session.flush()

    ap = AccessPoint(
        name="TestAP",
        mac_address="00:11:22:33:44:55",
        ip_address="192.168.0.1",
        model_name="ModelX",
        is_active=True,
        floor_id=floor.id,
        clients=5,
    )
    session.add(ap)
    session.flush()

    client_count = ClientCount(
        ap_id=ap.id,
        radio_id=radio.id,
        client_count=5,
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    session.add(client_count)
    session.commit()

    response = client.get("/client-counts")
    assert response.status_code == 200
    assert any("ap_name" in item for item in response.json())

@patch("app.main.fetch_client_counts")
@patch("app.main.auth_manager")
def test_update_client_count_task(mock_auth, mock_fetch, session):
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

    building = Building(name="TestBuilding", latitude=0.0, longitude=0.0)
    session.add(building)
    session.flush()

    floor = Floor(number=1, building_id=building.id)
    session.add(floor)
    session.flush()

    radio = Radio(id=1, name="radio0", description="2.4 GHz")
    session.merge(radio)  # Use merge to avoid UNIQUE constraint if already exists
    session.commit()

    app.dependency_overrides[get_db] = lambda: iter([session])
    update_client_count_task()
    app.dependency_overrides.clear()

    results = session.query(ClientCount).all()
    assert len(results) > 0

@patch("app.main.fetch_ap_data")
@patch("app.main.auth_manager")
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

    radio = Radio(id=1, name="radio0", description="2.4 GHz")
    session.merge(radio)  # Avoid UNIQUE constraint error
    session.commit()

    app.dependency_overrides[get_db] = lambda: iter([session])
    update_ap_data_task()
    app.dependency_overrides.clear()

    aps = session.query(AccessPoint).all()
    assert len(aps) > 0

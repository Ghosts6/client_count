import pytest
import os
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
from ap_monitor.app.diagnostics import (
    analyze_zero_count_buildings,
    monitor_building_health,
    generate_diagnostic_report,
    is_diagnostics_enabled
)
from ap_monitor.app.models import Building, Campus, ClientCount, ApBuilding, AccessPoint
from ap_monitor.app.db import get_wireless_db, get_apclient_db
from fastapi.testclient import TestClient
from ap_monitor.app.main import app
import tempfile
import json

@pytest.fixture(autouse=True)
def reset_environment():
    """Fixture to reset environment variables before and after each test."""
    original_value = os.environ.get('ENABLE_DIAGNOSTICS')
    if 'ENABLE_DIAGNOSTICS' in os.environ:
        del os.environ['ENABLE_DIAGNOSTICS']
    yield
    if original_value is not None:
        os.environ['ENABLE_DIAGNOSTICS'] = original_value
    elif 'ENABLE_DIAGNOSTICS' in os.environ:
        del os.environ['ENABLE_DIAGNOSTICS']

@pytest.fixture
def enable_diagnostics():
    """Fixture to enable diagnostics for testing."""
    os.environ['ENABLE_DIAGNOSTICS'] = 'true'
    yield
    if 'ENABLE_DIAGNOSTICS' in os.environ:
        del os.environ['ENABLE_DIAGNOSTICS']

@pytest.fixture
def mock_wireless_db():
    db = MagicMock()
    
    # Mock buildings and campuses
    building1 = Building(building_id=1, building_name="Test Building 1", campus_id=1)
    building2 = Building(building_id=2, building_name="Test Building 2", campus_id=1)
    campus = Campus(campus_id=1, campus_name="Test Campus")
    
    # Mock client counts with different scenarios
    count1 = ClientCount(
        client_count=0,
        time_inserted=datetime.now(timezone.utc),
        building_id=1
    )
    count2 = ClientCount(
        client_count=5,
        time_inserted=datetime.now(timezone.utc),
        building_id=2
    )
    
    # Setup query results for zero count analysis
    db.query.return_value.join.return_value.outerjoin.return_value.filter.return_value.all.return_value = [
        (building1, campus)
    ]
    
    # Setup query results for health monitoring
    db.query.return_value.join.return_value.filter.return_value.all.return_value = [
        (building1, count1),
        (building2, count2)
    ]
    
    # Mock historical average query
    db.query.return_value.filter.return_value.scalar.side_effect = [25.0, 5.0]
    
    return db

@pytest.fixture
def mock_apclient_db():
    db = MagicMock()
    
    # Mock AP building
    ap_building = ApBuilding(
        buildingid=1,
        buildingname="Test Building 1"
    )
    
    # Mock access points with different states
    ap1 = AccessPoint(
        apid=1,
        buildingid=1,
        isactive=True
    )
    ap2 = AccessPoint(
        apid=2,
        buildingid=1,
        isactive=False
    )
    
    # Setup query results
    db.query.return_value.filter.return_value.first.return_value = ap_building
    db.query.return_value.filter.return_value.all.return_value = [ap1, ap2]
    
    return db

@pytest.fixture
def mock_auth_manager():
    return MagicMock()

def test_diagnostics_disabled():
    """Test that diagnostics return appropriate message when disabled."""
    assert not is_diagnostics_enabled()
    result = generate_diagnostic_report(None, None, None)
    assert result == {"message": "Diagnostics are not enabled"}

def test_analyze_zero_count_buildings(mock_wireless_db, mock_apclient_db, mock_auth_manager, enable_diagnostics):
    """Test the zero count building analysis function with various scenarios."""
    with patch('ap_monitor.app.dna_api.fetch_ap_data') as mock_fetch:
        # Mock DNA Center API response with different scenarios
        mock_fetch.return_value = [
            {
                "location": "Test Building 1",
                "clientCount": {"2.4GHz": 0, "5GHz": 0}
            },
            {
                "location": "Test Building 1",
                "clientCount": {"2.4GHz": 1, "5GHz": 2}
            }
        ]
        
        report = analyze_zero_count_buildings(
            mock_wireless_db,
            mock_apclient_db,
            mock_auth_manager
        )
        
        assert report["timestamp"] is not None
        assert len(report["zero_count_buildings"]) == 1
        building_analysis = report["zero_count_buildings"][0]
        assert building_analysis["building_name"] == "Test Building 1"
        assert building_analysis["ap_status"]["total_aps"] == 2
        assert building_analysis["ap_status"]["active_aps"] == 1
        assert building_analysis["ap_status"]["inactive_aps"] == 1
        assert "issues" in building_analysis
        assert "recommendations" in building_analysis

def test_monitor_building_health(mock_wireless_db, mock_apclient_db, mock_auth_manager, enable_diagnostics):
    """Test the building health monitoring function with various scenarios."""
    # Mock recent counts with different patterns
    building1 = Building(building_id=1, building_name="Test Building 1", campus_id=1)
    building2 = Building(building_id=2, building_name="Test Building 2", campus_id=1)
    count1 = ClientCount(
        client_count=0,
        time_inserted=datetime.now(timezone.utc),
        building_id=1
    )
    count2 = ClientCount(
        client_count=5,
        time_inserted=datetime.now(timezone.utc),
        building_id=2
    )
    
    mock_wireless_db.query.return_value.join.return_value.filter.return_value.all.return_value = [
        (building1, count1),
        (building2, count2)
    ]
    
    # Mock historical averages
    mock_wireless_db.query.return_value.filter.return_value.scalar.side_effect = [25.0, 5.0]
    
    alerts = monitor_building_health(
        mock_wireless_db,
        mock_apclient_db,
        mock_auth_manager
    )
    
    assert len(alerts) == 1  # Only building1 should trigger an alert
    alert = alerts[0]
    assert alert["building_name"] == "Test Building 1"
    assert alert["current_count"] == 0
    assert alert["historical_avg"] == 25.0
    assert alert["severity"] == "medium"
    assert "message" in alert

def test_generate_diagnostic_report(mock_wireless_db, mock_apclient_db, mock_auth_manager, enable_diagnostics):
    """Test the comprehensive diagnostic report generation with various scenarios."""
    with patch('ap_monitor.app.dna_api.fetch_ap_data') as mock_fetch:
        # Mock DNA Center API response with mixed scenarios
        mock_fetch.return_value = [
            {
                "location": "Test Building 1",
                "clientCount": {"2.4GHz": 0, "5GHz": 0}
            },
            {
                "location": "Test Building 1",
                "clientCount": {"2.4GHz": 1, "5GHz": 2}
            }
        ]
        
        report = generate_diagnostic_report(
            mock_wireless_db,
            mock_apclient_db,
            mock_auth_manager
        )
        
        assert report["timestamp"] is not None
        assert "zero_count_buildings" in report
        assert "health_alerts" in report
        assert "summary" in report
        assert report["summary"]["total_buildings_analyzed"] == 1
        assert len(report["zero_count_buildings"]) == 1
        assert report["zero_count_buildings"][0]["building_name"] == "Test Building 1"
        assert "issues" in report["zero_count_buildings"][0]
        assert "recommendations" in report["zero_count_buildings"][0]

def test_diagnostics_with_missing_building(mock_wireless_db, mock_apclient_db, mock_auth_manager, enable_diagnostics):
    """Test diagnostics when a building is missing from the database."""
    # Mock a building that exists in wireless_db but not in apclient_db
    building = Building(building_id=1, building_name="Test Building 1", campus_id=1)
    campus = Campus(campus_id=1, campus_name="Test Campus")
    
    # Setup wireless_db query results
    mock_wireless_db.query.return_value.join.return_value.outerjoin.return_value.filter.return_value.all.return_value = [
        (building, campus)
    ]
    
    # Setup apclient_db to return None for the building
    mock_apclient_db.query.return_value.filter.return_value.first.return_value = None
    
    # Mock DNA Center API response
    with patch('ap_monitor.app.dna_api.fetch_ap_data') as mock_fetch:
        mock_fetch.return_value = [
            {
                "location": "Test Building 1",
                "clientCount": {"2.4GHz": 0, "5GHz": 0}
            }
        ]
        
        report = analyze_zero_count_buildings(
            mock_wireless_db,
            mock_apclient_db,
            mock_auth_manager
        )
        
        assert len(report["zero_count_buildings"]) == 1
        building_analysis = report["zero_count_buildings"][0]
        assert "Building not found in apclientcount database" in building_analysis["issues"]
        assert "Verify building name mapping between databases" in building_analysis["recommendations"]

def test_diagnostics_with_dna_center_error(mock_wireless_db, mock_apclient_db, mock_auth_manager, enable_diagnostics):
    """Test diagnostics when DNA Center API returns an error."""
    with patch('ap_monitor.app.dna_api.fetch_ap_data') as mock_fetch:
        mock_fetch.side_effect = Exception("DNA Center API error")
        
        report = analyze_zero_count_buildings(
            mock_wireless_db,
            mock_apclient_db,
            mock_auth_manager
        )
        
        assert len(report["zero_count_buildings"]) == 1
        building_analysis = report["zero_count_buildings"][0]
        assert "Error checking DNA Center" in building_analysis["issues"][0]
        assert "Verify DNA Center connectivity and credentials" in building_analysis["recommendations"]

def test_database_session_context_manager():
    """Test that the database session context managers work correctly."""
    with get_wireless_db() as wireless_db:
        assert wireless_db is not None
        # Perform a simple query to ensure the session is active
        result = wireless_db.query(Building).first()
        # If no records exist, the result will be None, but the session is still valid
        assert wireless_db is not None

    with get_apclient_db() as apclient_db:
        assert apclient_db is not None
        # Perform a simple query to ensure the session is active
        result = apclient_db.query(ApBuilding).first()
        # If no records exist, the result will be None, but the session is still valid
        assert apclient_db is not None 

def test_incomplete_devices_endpoint(enable_diagnostics, monkeypatch):
    """Test the /diagnostics/incomplete-devices endpoint returns correct data and respects diagnostics flag."""
    # Prepare a fake diagnostics_incomplete.json file
    fake_data = [
        {"key": "ap1", "missing_fields": ["macAddress"], "fields": {"name": "AP1"}},
        {"key": "ap2", "missing_fields": ["location", "clientCount"], "fields": {"name": "AP2"}}
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        # Patch the incomplete_json_file path in diagnostics.py
        incomplete_file = tmpdir + "/diagnostics_incomplete.json"
        monkeypatch.setattr("ap_monitor.app.diagnostics.incomplete_json_file", incomplete_file)
        with open(incomplete_file, 'w') as f:
            json.dump(fake_data, f)
        client = TestClient(app)
        response = client.get("/diagnostics/incomplete-devices")
        assert response.status_code == 200
        data = response.json()
        assert "incomplete_devices" in data
        assert data["count"] == 2
        assert data["incomplete_devices"][0]["key"] == "ap1"
        assert data["incomplete_devices"][1]["key"] == "ap2"

    # Test with diagnostics disabled
    os.environ['ENABLE_DIAGNOSTICS'] = 'false'
    client = TestClient(app)
    response = client.get("/diagnostics/incomplete-devices")
    assert response.status_code == 403 
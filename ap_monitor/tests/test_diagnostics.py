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

@pytest.fixture(autouse=True)
def reset_environment():
    """Fixture to reset environment variables before and after each test."""
    # Store original value
    original_value = os.environ.get('ENABLE_DIAGNOSTICS')
    
    # Reset to default (disabled)
    if 'ENABLE_DIAGNOSTICS' in os.environ:
        del os.environ['ENABLE_DIAGNOSTICS']
    
    yield
    
    # Restore original value
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
    
    # Mock client counts
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
    
    # Setup query results
    db.query.return_value.join.return_value.outerjoin.return_value.filter.return_value.all.return_value = [
        (building1, campus)
    ]
    
    return db

@pytest.fixture
def mock_apclient_db():
    db = MagicMock()
    
    # Mock AP building
    ap_building = ApBuilding(
        buildingid=1,
        buildingname="Test Building 1"
    )
    
    # Mock access points
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
    """Test the zero count building analysis function."""
    with patch('ap_monitor.app.diagnostics.fetch_ap_data') as mock_fetch:
        # Mock DNA Center API response
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
        
        assert report["timestamp"] is not None
        assert len(report["zero_count_buildings"]) == 1
        building_analysis = report["zero_count_buildings"][0]
        assert building_analysis["building_name"] == "Test Building 1"
        assert building_analysis["ap_status"]["total_aps"] == 2
        assert building_analysis["ap_status"]["active_aps"] == 1
        assert building_analysis["ap_status"]["inactive_aps"] == 1

def test_monitor_building_health(mock_wireless_db, mock_apclient_db, mock_auth_manager, enable_diagnostics):
    """Test the building health monitoring function."""
    # Mock recent counts
    building = Building(building_id=1, building_name="Test Building 1", campus_id=1)
    count = ClientCount(
        client_count=0,
        time_inserted=datetime.now(timezone.utc),
        building_id=1
    )
    
    mock_wireless_db.query.return_value.join.return_value.filter.return_value.all.return_value = [
        (building, count)
    ]
    
    # Mock historical average
    mock_wireless_db.query.return_value.filter.return_value.scalar.return_value = 25.0
    
    alerts = monitor_building_health(
        mock_wireless_db,
        mock_apclient_db,
        mock_auth_manager
    )
    
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert["building_name"] == "Test Building 1"
    assert alert["current_count"] == 0
    assert alert["historical_avg"] == 25.0
    assert alert["severity"] == "medium"

def test_generate_diagnostic_report(mock_wireless_db, mock_apclient_db, mock_auth_manager, enable_diagnostics):
    """Test the comprehensive diagnostic report generation."""
    with patch('ap_monitor.app.diagnostics.fetch_ap_data') as mock_fetch:
        # Mock DNA Center API response
        mock_fetch.return_value = [
            {
                "location": "Test Building 1",
                "clientCount": {"2.4GHz": 0, "5GHz": 0}
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
import pytest
from datetime import datetime, timezone
from ap_monitor.app.models import (
    Building, Campus, ClientCount,
    ApBuilding, Floor, AccessPoint, RadioType, ClientCountAP
)
from ap_monitor.app.main import update_client_count_task
from unittest.mock import patch, MagicMock

@pytest.fixture
def test_buildings(wireless_db, apclient_db):
    """Set up test buildings with different name cases and mappings."""
    # Create wireless_count buildings
    campus = Campus(campus_name="Keele Campus")
    wireless_db.add(campus)
    wireless_db.commit()

    buildings = [
        Building(
            building_name="Keele Campus",
            campus_id=campus.campus_id,
            latitude=43.7735473000,
            longitude=-79.5062752000
        ),
        Building(
            building_name="Ross Building",
            campus_id=campus.campus_id,
            latitude=43.7735473000,
            longitude=-79.5062752000
        ),
        Building(
            building_name="Vari Hall",
            campus_id=campus.campus_id,
            latitude=43.7735473000,
            longitude=-79.5062752000
        ),
        Building(
            building_name="Scott Library",
            campus_id=campus.campus_id,
            latitude=43.7735473000,
            longitude=-79.5062752000
        )
    ]
    for building in buildings:
        wireless_db.add(building)
    wireless_db.commit()

    # Create apclientcount buildings with different cases
    ap_buildings = [
        ApBuilding(buildingname="KEELE CAMPUS"),
        ApBuilding(buildingname="Ross Building"),
        ApBuilding(buildingname="vari hall"),
        ApBuilding(buildingname="SCOTT LIBRARY")
    ]
    for building in ap_buildings:
        apclient_db.add(building)
    apclient_db.commit()

    return buildings, ap_buildings

@pytest.fixture
def test_aps_with_counts(apclient_db, test_buildings):
    """Set up test APs with different client count scenarios."""
    _, ap_buildings = test_buildings
    
    # Create floors for each building
    floors = []
    for building in ap_buildings:
        floor = Floor(buildingid=building.buildingid, floorname="Floor 1")
        apclient_db.add(floor)
        floors.append(floor)
    apclient_db.commit()

    # Create APs with different scenarios
    aps = []
    for i, (building, floor) in enumerate(zip(ap_buildings, floors)):
        ap = AccessPoint(
            buildingid=building.buildingid,
            floorid=floor.floorid,
            apname=f"AP{i+1}",
            macaddress=f"00:11:22:33:44:{i:02x}",
            ipaddress=f"192.168.1.{i+1}",
            modelname="Test Model",
            isactive=True
        )
        apclient_db.add(ap)
        aps.append(ap)
    apclient_db.commit()

    # Add client counts for each AP
    radio_types = apclient_db.query(RadioType).all()
    for i, ap in enumerate(aps):
        # First AP has normal counts
        # Second AP has zero counts
        # Third AP has no counts
        # Fourth AP has mixed counts
        if i == 0:  # Normal counts
            for radio in radio_types:
                client_count = ClientCountAP(
                    apid=ap.apid,
                    radioid=radio.radioid,
                    clientcount=10,
                    timestamp=datetime.now(timezone.utc)
                )
                apclient_db.add(client_count)
        elif i == 1:  # Zero counts
            for radio in radio_types:
                client_count = ClientCountAP(
                    apid=ap.apid,
                    radioid=radio.radioid,
                    clientcount=0,
                    timestamp=datetime.now(timezone.utc)
                )
                apclient_db.add(client_count)
        elif i == 3:  # Mixed counts
            for j, radio in enumerate(radio_types):
                client_count = ClientCountAP(
                    apid=ap.apid,
                    radioid=radio.radioid,
                    clientcount=5 if j % 2 == 0 else 0,
                    timestamp=datetime.now(timezone.utc)
                )
                apclient_db.add(client_count)
    
    apclient_db.commit()
    return aps

def test_building_name_case_insensitive_mapping(wireless_db, apclient_db, test_buildings):
    """Test that building names are matched case-insensitively."""
    buildings, ap_buildings = test_buildings
    
    # Verify all buildings are created
    assert wireless_db.query(Building).count() == 4
    assert apclient_db.query(ApBuilding).count() == 4
    
    # Test case-insensitive matching
    for wireless_building in buildings:
        matching_ap_building = apclient_db.query(ApBuilding).filter(
            ApBuilding.buildingname.ilike(wireless_building.building_name)
        ).first()
        assert matching_ap_building is not None, f"No matching AP building found for {wireless_building.building_name}"

def test_zero_client_count_handling(wireless_db, apclient_db, test_buildings, test_aps_with_counts):
    """Test that zero client counts are properly handled and recorded."""
    # Mock the DNA Center API responses
    mock_ap_data = [
        {
            "name": "AP1",
            "macAddress": "00:11:22:33:44:00",
            "location": "Global/Keele Campus/Keele Campus/Floor 1",
            "clientCount": {"radio0": 10, "radio1": 10, "radio2": 10}
        },
        {
            "name": "AP2",
            "macAddress": "00:11:22:33:44:01",
            "location": "Global/Keele Campus/Ross Building/Floor 1",
            "clientCount": {"radio0": 0, "radio1": 0, "radio2": 0}
        },
        {
            "name": "AP3",
            "macAddress": "00:11:22:33:44:02",
            "location": "Global/Keele Campus/Vari Hall/Floor 1",
            "clientCount": {}
        },
        {
            "name": "AP4",
            "macAddress": "00:11:22:33:44:03",
            "location": "Global/Keele Campus/Scott Library/Floor 1",
            "clientCount": {"radio0": 5, "radio1": 0, "radio2": 5}
        }
    ]

    with patch("ap_monitor.app.main.fetch_ap_data", return_value=mock_ap_data) as mock_fetch_ap_data, \
         patch("ap_monitor.app.main.fetch_client_counts", return_value=[]), \
         patch("ap_monitor.app.main.auth_manager"):

        # Run the update task with correct session order and pass fetch_ap_data_func
        update_client_count_task(db=apclient_db, wireless_db=wireless_db, fetch_ap_data_func=mock_fetch_ap_data)

        # Verify client counts in wireless_count database
        client_counts = wireless_db.query(ClientCount).all()
        assert len(client_counts) == 4  # One count per building

        # Verify counts for each building
        for count in client_counts:
            building = wireless_db.get(Building, count.building_id)
            if building.building_name == "Keele Campus":
                assert count.client_count == 30  # 10 per radio * 3 radios
            elif building.building_name == "Ross Building":
                assert count.client_count == 0  # All zeros
            elif building.building_name == "Vari Hall":
                assert count.client_count == 0  # No counts
            elif building.building_name == "Scott Library":
                assert count.client_count == 10  # 5 + 0 + 5

def test_missing_building_handling(wireless_db, apclient_db, test_buildings):
    """Test that buildings not found in wireless_count are properly logged."""
    # Add a building that only exists in apclientcount
    extra_building = ApBuilding(buildingname="Extra Building")
    apclient_db.add(extra_building)
    apclient_db.commit()
    
    # Create an AP for the extra building
    floor = Floor(buildingid=extra_building.buildingid, floorname="Floor 1")
    apclient_db.add(floor)
    apclient_db.commit()
    
    ap = AccessPoint(
        buildingid=extra_building.buildingid,
        floorid=floor.floorid,
        apname="Extra AP",
        macaddress="00:11:22:33:44:99",
        ipaddress="192.168.1.99",
        modelname="Test Model",
        isactive=True
    )
    apclient_db.add(ap)
    apclient_db.commit()
    
    # Mock the DNA Center API response
    mock_ap_data = [{
        "name": "Extra AP",
        "macAddress": "00:11:22:33:44:99",
        "location": "Global/Keele Campus/Extra Building/Floor 1",
        "clientCount": {"radio0": 5, "radio1": 5, "radio2": 5}
    }]
    
    with patch("ap_monitor.app.main.fetch_ap_data", return_value=mock_ap_data) as mock_fetch_ap_data, \
         patch("ap_monitor.app.main.fetch_client_counts", return_value=[]), \
         patch("ap_monitor.app.main.auth_manager"), \
         patch("ap_monitor.app.main.logger") as mock_logger:
        
        # Run the update task with correct session order and pass fetch_ap_data_func
        update_client_count_task(db=apclient_db, wireless_db=wireless_db, fetch_ap_data_func=mock_fetch_ap_data)
        
        # Verify that the building was logged as missing
        mock_logger.warning.assert_any_call("Building Extra Building not found in wireless_count database")
        
        # Verify no client count was created for the missing building
        assert wireless_db.query(ClientCount).filter_by(building_id=None).count() == 0

def test_building_with_no_aps(wireless_db, apclient_db, test_buildings):
    """Test that buildings with no APs get zero counts."""
    buildings, _ = test_buildings
    
    # Add a building with no APs
    no_ap_building = Building(
        building_name="No AP Building",
        campus_id=buildings[0].campus_id,
        latitude=43.7735473000,
        longitude=-79.5062752000
    )
    wireless_db.add(no_ap_building)
    wireless_db.commit()
    
    with patch("ap_monitor.app.main.fetch_ap_data", return_value=[]), \
         patch("ap_monitor.app.main.fetch_client_counts", return_value=[]), \
         patch("ap_monitor.app.main.auth_manager"):
        
        # Run the update task
        update_client_count_task(wireless_db, apclient_db)
        
        # Verify zero count was created for the building with no APs
        zero_count = wireless_db.query(ClientCount).filter_by(
            building_id=no_ap_building.building_id
        ).first()
        assert zero_count is not None
        assert zero_count.client_count == 0 
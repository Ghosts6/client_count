import pytest
from datetime import datetime, timezone
from ap_monitor.app.models import (
    Building, Campus, ClientCount,
    ApBuilding, Floor, AccessPoint, RadioType, ClientCountAP
)
from ap_monitor.app.main import update_client_count_task
from unittest.mock import patch, MagicMock
from ap_monitor.app.mapping import parse_ap_name_for_location

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
    mock_ap_data = [
        {
            "macAddress": "00:11:22:33:44:00",
            "name": "AP1",
            "location": "Global/Keele Campus/Keele Campus/Floor 1",
            "clientCount": 30,
            "status": "ok"
        },
        {
            "macAddress": "00:11:22:33:44:01",
            "name": "AP2",
            "location": "Global/Keele Campus/Ross Building/Floor 1",
            "clientCount": 0,
            "status": "ok"
        },
        {
            "macAddress": "00:11:22:33:44:02",
            "name": "AP3",
            "location": "Global/Keele Campus/Vari Hall/Floor 1",
            "clientCount": 0,
            "status": "ok"
        },
        {
            "macAddress": "00:11:22:33:44:03",
            "name": "AP4",
            "location": "Global/Keele Campus/Scott Library/Floor 1",
            "clientCount": 10,
            "status": "ok"
        }
    ]
    with patch("ap_monitor.app.main.fetch_ap_client_data_with_fallback") as mock_fetch:
        mock_fetch.return_value = mock_ap_data
        update_client_count_task(db=apclient_db, wireless_db=wireless_db)
        client_counts = wireless_db.query(ClientCount).all()
        assert len(client_counts) == 4  # One count per building

def test_missing_building_handling(wireless_db, apclient_db, test_buildings):
    """Test that buildings not found in wireless_count are properly logged."""
    extra_building = ApBuilding(buildingname="Extra Building")
    apclient_db.add(extra_building)
    apclient_db.commit()
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
    mock_ap_data = [{
        "macAddress": "00:11:22:33:44:99",
        "name": "Extra AP",
        "location": "Global/Keele Campus/Extra Building/Floor 1",
        "clientCount": 15,
        "status": "ok"
    }]
    with patch("ap_monitor.app.main.fetch_ap_client_data_with_fallback") as mock_fetch, \
         patch("ap_monitor.app.main.logger") as mock_logger:
        mock_fetch.return_value = mock_ap_data
        update_client_count_task(db=apclient_db, wireless_db=wireless_db)
        mock_logger.warning.assert_any_call("Skipping AP Extra AP due to unmapped building name: Extra Building")

def test_building_with_no_aps(wireless_db, apclient_db, test_buildings):
    """Test that buildings with no APs get zero counts."""
    buildings, _ = test_buildings
    no_ap_building = Building(
        building_name="No AP Building",
        campus_id=buildings[0].campus_id,
        latitude=43.7735473000,
        longitude=-79.5062752000
    )
    wireless_db.add(no_ap_building)
    wireless_db.commit()
    fresh_building = wireless_db.query(Building).filter_by(building_name="No AP Building").first()
    building_id = fresh_building.building_id
    with patch("ap_monitor.app.main.fetch_ap_client_data_with_fallback") as mock_fetch:
        mock_fetch.return_value = []
        update_client_count_task(db=apclient_db, wireless_db=wireless_db)
        # Should insert a zero count for the building
        client_counts = wireless_db.query(ClientCount).filter_by(building_id=building_id).all()
        assert all(cc.client_count == 0 for cc in client_counts) 

def test_parse_ap_name_for_location_examples():
    # k388-studc-b-1 → Student Centre, Basement, 1
    assert parse_ap_name_for_location("k388-studc-b-1") == ("Student Centre", "Basement", "1")
    # k372-ross-6-7 → Ross Building, 6, 7
    assert parse_ap_name_for_location("k372-ross-6-7") == ("Ross Building", "6", "7")
    # k410-beth-r-1236 → Bethune Residence, Room, 1236
    assert parse_ap_name_for_location("k410-beth-r-1236") == ("Bethune Residence", "Room", "1236")
    # k367-cb-1-14 → Chemistry Building, 1, 14
    assert parse_ap_name_for_location("k367-cb-1-14") == ("Chemistry Building", "1", "14")
    # k389-st-r-1024 → Stong College, Room, 1024
    assert parse_ap_name_for_location("k389-st-r-1024") == ("Stong College", "Room", "1024")
    # k483-tel-3-26 → Victor Phillip Dahdaleh, 3, 26
    assert parse_ap_name_for_location("k483-tel-3-26") == ("Victor Phillip Dahdaleh", "3", "26")
    # k402-as380-r-511 → Atkinson, Room, 511
    assert parse_ap_name_for_location("k402-as380-r-511") == ("Atkinson", "Room", "511")
    # k383-yl-2-5 → York Lanes, 2, 5
    assert parse_ap_name_for_location("k383-yl-2-5") == ("York Lanes", "2", "5")
    # Not enough parts
    assert parse_ap_name_for_location("k383-yl-2") == (None, None, None)
    # Unknown short form
    assert parse_ap_name_for_location("k999-unknown-b-1") == ("Unknown", "Basement", "1") 

def test_normalize_building_name():
    from ap_monitor.app.mapping import normalize_building_name
    # Direct canonical names
    assert normalize_building_name('Ross') == 'Ross'
    assert normalize_building_name('Scott Library') == 'Scott Library'
    # Case-insensitive
    assert normalize_building_name('ross') == 'Ross'
    assert normalize_building_name('scott library') == 'Scott Library'
    # Short forms
    assert normalize_building_name('st') == 'Stong College'
    assert normalize_building_name('yl') == 'York Lanes'
    assert normalize_building_name('tel') == 'Victor Phillip Dahdaleh'
    # Common variants
    assert normalize_building_name('Ross Building') == 'Ross'
    assert normalize_building_name('Victor Phillip Dahdaleh Building') == 'Victor Phillip Dahdaleh'
    # Suffix/variant
    assert normalize_building_name('Stong College Building') == 'Stong College'
    # Partial/contains
    assert normalize_building_name('Scott') == 'Scott Library'
    # Unmappable
    assert normalize_building_name('Nonexistent Building') is None 
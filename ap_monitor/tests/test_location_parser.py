import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from ap_monitor.app.models import ApBuilding, Floor, Room, AccessPoint, ClientCountAP, RadioType, APClientBase
from ap_monitor.app.db import APClientBase as DBAPClientBase
from ap_monitor.app.main import insert_apclientcount_data

# Mock data for testing different location patterns
MOCK_LOCATIONS = {
    "standard_format": {
        "location": "Global/Keele Campus/BuildingA/Floor 1",
        "expected_building": "BuildingA",
        "expected_floor": "Floor 1"
    },
    "basement": {
        "location": "Global/Keele Campus/BuildingB/Basement",
        "expected_building": "BuildingB",
        "expected_floor": "Basement"
    },
    "ground_floor": {
        "location": "Global/Keele Campus/BuildingC/Ground",
        "expected_building": "BuildingC",
        "expected_floor": "Ground"
    },
    "directional_floor": {
        "location": "Global/Keele Campus/BuildingD/Floor 1 North",
        "expected_building": "BuildingD",
        "expected_floor": "Floor 1 North"
    },
    "basement_directional": {
        "location": "Global/Keele Campus/BuildingE/Basement South",
        "expected_building": "BuildingE",
        "expected_floor": "Basement South"
    },
    "complex_building_name": {
        "location": "Global/Keele Campus/Health Nursing and Enviromental Studies/Floor 1",
        "expected_building": "Health Nursing and Enviromental Studies",
        "expected_floor": "Floor 1"
    },
    "with_room": {
        "location": "Global/Keele Campus/BuildingG/Floor 1/Room 101",
        "expected_building": "BuildingG",
        "expected_floor": "Floor 1"
    },
    "numbered_building": {
        "location": "Global/Keele Campus/Assiniboine 320/Floor 12",
        "expected_building": "Assiniboine 320",
        "expected_floor": "Floor 12"
    },
    "special_chars": {
        "location": "Global/Keele Campus/Building-H/Floor 3",
        "expected_building": "Building-H",
        "expected_floor": "Floor 3"
    },
    "multi_word_building": {
        "location": "Global/Keele Campus/Centre for Film and Theatre/Floor 1",
        "expected_building": "Centre for Film and Theatre",
        "expected_floor": "Floor 1"
    },
    "short_format": {
        "location": "BuildingJ/Floor 2",
        "expected_building": "BuildingJ",
        "expected_floor": "Floor 2"
    },
    "dome_location": {
        "location": "Global/Keele Campus/York Lions Stadium/Dome",
        "expected_building": "York Lions Stadium",
        "expected_floor": "Dome"
    },
    "central_square_ne": {
        "location": "Global/Keele Campus/Central Square/Floor 1 NE",
        "expected_building": "Central Square",
        "expected_floor": "Floor 1 NE"
    },
    "central_square_se": {
        "location": "Global/Keele Campus/Central Square/Floor 1 SE",
        "expected_building": "Central Square",
        "expected_floor": "Floor 1 SE"
    },
    "central_square_sw": {
        "location": "Global/Keele Campus/Central Square/Floor 1 SW",
        "expected_building": "Central Square",
        "expected_floor": "Floor 1 SW"
    },
    "central_square_nw": {
        "location": "Global/Keele Campus/Central Square/Floor 1 NW",
        "expected_building": "Central Square",
        "expected_floor": "Floor 1 NW"
    },
    "outdoor_location": {
        "location": "Global/Keele Campus/HAC Outdoor/Floor 1",
        "expected_building": "HAC Outdoor",
        "expected_floor": "Floor 1"
    },
    "passy_building": {
        "location": "Global/Keele Campus/Passy 14/Floor 2",
        "expected_building": "Passy 14",
        "expected_floor": "Floor 2"
    }
}

@pytest.fixture
def session():
    # Create test database
    engine = create_engine("sqlite:///:memory:")
    
    # Enable foreign key support for SQLite
    def _fk_pragma_on_connect(dbapi_con, con_record):
        dbapi_con.execute('pragma foreign_keys=ON')
    
    event.listen(engine, 'connect', _fk_pragma_on_connect)
    
    # Create all tables
    DBAPClientBase.metadata.create_all(bind=engine)
    
    # Create session
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    
    try:
        # Initialize radio types
        radio_types = [
            RadioType(radioid=1, radioname="2.4GHz"),
            RadioType(radioid=2, radioname="5GHz")
        ]
        session.add_all(radio_types)
        session.commit()
        
        yield session
    finally:
        session.close()

@pytest.fixture
def current_timestamp():
    return datetime.now(timezone.utc)

def test_location_parsing_standard_format(session, current_timestamp):
    """Test standard location format parsing"""
    mock_data = MOCK_LOCATIONS["standard_format"]
    device_info = [{
        "name": "AP1",
        "location": mock_data["location"],
        "macAddress": "00:11:22:33:44:55",
        "clientCount": {"2.4GHz": 10},
        "radioType": "2.4GHz",
        "ipAddress": "192.168.1.1",
        "model": "AIR-CAP3702I-A-K9",
        "reachabilityHealth": "UP"
    }]
    
    insert_apclientcount_data(device_info, current_timestamp, session)
    
    building = session.query(ApBuilding).filter_by(buildingname=mock_data["expected_building"]).first()
    assert building is not None
    floor = session.query(Floor).filter_by(floorname=mock_data["expected_floor"], buildingid=building.buildingid).first()
    assert floor is not None

def test_location_parsing_special_floors(session, current_timestamp):
    """Test parsing of special floor types (Basement, Ground)"""
    for test_case in ["basement", "ground_floor"]:
        mock_data = MOCK_LOCATIONS[test_case]
        device_info = [{
            "name": f"AP_{test_case}",
            "location": mock_data["location"],
            "macAddress": f"00:11:22:33:44:{test_case[-2:]}",
            "clientCount": {"2.4GHz": 5},
            "radioType": "2.4GHz",
            "ipAddress": "192.168.1.2",
            "model": "AIR-CAP3702I-A-K9",
            "reachabilityHealth": "UP"
        }]
        
        insert_apclientcount_data(device_info, current_timestamp, session)
        
        building = session.query(ApBuilding).filter_by(buildingname=mock_data["expected_building"]).first()
        assert building is not None
        floor = session.query(Floor).filter_by(floorname=mock_data["expected_floor"], buildingid=building.buildingid).first()
        assert floor is not None

def test_location_parsing_directional_floors(session, current_timestamp):
    """Test parsing of floors with directional indicators"""
    for test_case in ["directional_floor", "basement_directional"]:
        mock_data = MOCK_LOCATIONS[test_case]
        device_info = [{
            "name": f"AP_{test_case}",
            "location": mock_data["location"],
            "macAddress": f"00:11:22:33:44:{test_case[-2:]}",
            "clientCount": {"5GHz": 8},
            "radioType": "5GHz",
            "ipAddress": "192.168.1.3",
            "model": "AIR-CAP3702I-A-K9",
            "reachabilityHealth": "UP"
        }]
        
        insert_apclientcount_data(device_info, current_timestamp, session)
        
        building = session.query(ApBuilding).filter_by(buildingname=mock_data["expected_building"]).first()
        assert building is not None
        floor = session.query(Floor).filter_by(floorname=mock_data["expected_floor"], buildingid=building.buildingid).first()
        assert floor is not None

def test_location_parsing_complex_buildings(session, current_timestamp):
    """Test parsing of buildings with complex names"""
    for test_case in ["complex_building_name", "numbered_building", "special_chars", "multi_word_building"]:
        mock_data = MOCK_LOCATIONS[test_case]
        device_info = [{
            "name": f"AP_{test_case}",
            "location": mock_data["location"],
            "macAddress": f"00:11:22:33:44:{test_case[-2:]}",
            "clientCount": {"2.4GHz": 12},
            "radioType": "2.4GHz",
            "ipAddress": "192.168.1.4",
            "model": "AIR-CAP3702I-A-K9",
            "reachabilityHealth": "UP"
        }]
        
        insert_apclientcount_data(device_info, current_timestamp, session)
        
        building = session.query(ApBuilding).filter_by(buildingname=mock_data["expected_building"]).first()
        assert building is not None
        floor = session.query(Floor).filter_by(floorname=mock_data["expected_floor"], buildingid=building.buildingid).first()
        assert floor is not None

def test_location_parsing_special_locations(session, current_timestamp):
    """Test parsing of special locations (Dome, Central Square directions)"""
    for test_case in ["dome_location", "central_square_ne", "central_square_se", "central_square_sw", "central_square_nw"]:
        mock_data = MOCK_LOCATIONS[test_case]
        device_info = [{
            "name": f"AP_{test_case}",
            "location": mock_data["location"],
            "macAddress": f"00:11:22:33:44:{test_case[-2:]}",
            "clientCount": {"5GHz": 15},
            "radioType": "5GHz",
            "ipAddress": "192.168.1.5",
            "model": "AIR-CAP3702I-A-K9",
            "reachabilityHealth": "UP"
        }]
        
        insert_apclientcount_data(device_info, current_timestamp, session)
        
        building = session.query(ApBuilding).filter_by(buildingname=mock_data["expected_building"]).first()
        assert building is not None
        floor = session.query(Floor).filter_by(floorname=mock_data["expected_floor"], buildingid=building.buildingid).first()
        assert floor is not None

def test_location_parsing_outdoor_and_numbered(session, current_timestamp):
    """Test parsing of outdoor locations and numbered buildings"""
    for test_case in ["outdoor_location", "passy_building"]:
        mock_data = MOCK_LOCATIONS[test_case]
        device_info = [{
            "name": f"AP_{test_case}",
            "location": mock_data["location"],
            "macAddress": f"00:11:22:33:44:{test_case[-2:]}",
            "clientCount": {"2.4GHz": 6},
            "radioType": "2.4GHz",
            "ipAddress": "192.168.1.6",
            "model": "AIR-CAP3702I-A-K9",
            "reachabilityHealth": "UP"
        }]
        
        insert_apclientcount_data(device_info, current_timestamp, session)
        
        building = session.query(ApBuilding).filter_by(buildingname=mock_data["expected_building"]).first()
        assert building is not None
        floor = session.query(Floor).filter_by(floorname=mock_data["expected_floor"], buildingid=building.buildingid).first()
        assert floor is not None

def test_location_parsing_invalid_formats(session, current_timestamp):
    """Test handling of invalid location formats"""
    invalid_locations = [
        "",  # Empty location
        "Invalid",  # Too short
        "Global/Invalid",  # Missing parts
        "Global/Keele Campus/Invalid",  # Missing floor
        "Global/Keele Campus/Building/",  # Empty floor
        "/Global/Keele Campus/Building/Floor 1",  # Leading slash
        "Global/Keele Campus/Building/Floor 1/",  # Trailing slash
        "Global/Keele Campus/Building/Invalid",  # Invalid floor
    ]
    
    for location in invalid_locations:
        # Clear all related tables before each sub-test
        session.query(ClientCountAP).delete()
        session.query(AccessPoint).delete()
        session.query(Room).delete()
        session.query(Floor).delete()
        session.query(ApBuilding).delete()
        session.commit()
        device_info = [{
            "name": f"AP_invalid_{location[:10]}",
            "location": location,
            "macAddress": f"00:11:22:33:44:{hash(location) % 100:02d}",
            "clientCount": {"2.4GHz": 3},
            "radioType": "2.4GHz",
            "ipAddress": "192.168.1.7",
            "model": "AIR-CAP3702I-A-K9",
            "reachabilityHealth": "UP"
        }]
        
        before_count = session.query(ClientCountAP).count()
        insert_apclientcount_data(device_info, current_timestamp, session)
        after_count = session.query(ClientCountAP).count()
        if before_count != after_count:
            print(f"DEBUG: Inserted records for location '{location}':", list(session.query(ClientCountAP).all()))
        assert before_count == after_count, f"Client count should not be inserted for invalid location: {location}"

def test_location_parsing_existing_ap_update(session, current_timestamp):
    """Test updating an existing AP's information"""
    # First, create an initial AP
    initial_device_info = [{
        "name": "AP_Initial",
        "location": "Global/Keele Campus/BuildingA/Floor 1",
        "macAddress": "00:11:22:33:44:55",
        "clientCount": {"2.4GHz": 5},
        "radioType": "2.4GHz",
        "ipAddress": "192.168.1.1",
        "model": "AIR-CAP3702I-A-K9",
        "reachabilityHealth": "UP"
    }]
    
    # Insert initial AP
    insert_apclientcount_data(initial_device_info, current_timestamp, session)
    
    # Now update the same AP with new information
    updated_device_info = [{
        "name": "AP_Updated",
        "location": "Global/Keele Campus/BuildingB/Floor 2",  # Changed building and floor
        "macAddress": "00:11:22:33:44:55",  # Same MAC address
        "clientCount": {"2.4GHz": 10},  # Updated client count
        "radioType": "2.4GHz",
        "ipAddress": "192.168.1.2",  # Changed IP
        "model": "AIR-CAP3702I-A-K9",
        "reachabilityHealth": "DOWN"  # Changed status
    }]
    
    # Update AP
    insert_apclientcount_data(updated_device_info, current_timestamp, session)
    
    # Verify the AP was updated correctly
    ap = session.query(AccessPoint).filter_by(macaddress="00:11:22:33:44:55").first()
    assert ap is not None
    assert ap.apname == "AP_Updated"
    assert ap.ipaddress == "192.168.1.2"
    assert ap.isactive is False
    
    # Verify building and floor were updated
    building = session.query(ApBuilding).filter_by(buildingname="BuildingB").first()
    assert building is not None
    floor = session.query(Floor).filter_by(floorname="Floor 2", buildingid=building.buildingid).first()
    assert floor is not None
    assert ap.buildingid == building.buildingid
    assert ap.floorid == floor.floorid
    
    # Verify client count was updated
    client_count = session.query(ClientCountAP).filter_by(
        apid=ap.apid,
        timestamp=current_timestamp
    ).first()
    assert client_count is not None
    assert client_count.clientcount == 10
    assert client_count.radio.radioname == "2.4GHz"

def test_location_parsing_existing_ap_multiple_radios(session, current_timestamp):
    """Test updating an existing AP with multiple radio types"""
    # First, create an initial AP with one radio
    initial_device_info = [{
        "name": "AP_Multi_Radio",
        "location": "Global/Keele Campus/BuildingA/Floor 1",
        "macAddress": "00:11:22:33:44:66",
        "clientCount": {"2.4GHz": 5},
        "radioType": "2.4GHz",
        "ipAddress": "192.168.1.3",
        "model": "AIR-CAP3702I-A-K9",
        "reachabilityHealth": "UP"
    }]
    
    # Insert initial AP
    insert_apclientcount_data(initial_device_info, current_timestamp, session)
    
    # Now update the same AP with multiple radios
    updated_device_info = [{
        "name": "AP_Multi_Radio",
        "location": "Global/Keele Campus/BuildingA/Floor 1",
        "macAddress": "00:11:22:33:44:66",
        "clientCount": {
            "2.4GHz": 8,
            "5GHz": 12
        },
        "radioType": "2.4GHz",
        "ipAddress": "192.168.1.3",
        "model": "AIR-CAP3702I-A-K9",
        "reachabilityHealth": "UP"
    }]
    
    # Update AP
    insert_apclientcount_data(updated_device_info, current_timestamp, session)
    
    # Verify the AP exists
    ap = session.query(AccessPoint).filter_by(macaddress="00:11:22:33:44:66").first()
    assert ap is not None
    
    # Verify both radio client counts were updated
    client_counts = session.query(ClientCountAP).filter_by(
        apid=ap.apid,
        timestamp=current_timestamp
    ).all()
    assert len(client_counts) == 2
    
    # Create a map of radio types to counts
    radio_counts = {cc.radio.radioname: cc.clientcount for cc in client_counts}
    assert radio_counts["2.4GHz"] == 8
    assert radio_counts["5GHz"] == 12 
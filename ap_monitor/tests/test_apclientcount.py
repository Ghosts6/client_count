import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from ap_monitor.app.models import ApBuilding, Floor, Room, AccessPoint, ClientCountAP, RadioType, APClientBase
from ap_monitor.app.db import APClientBase as DBAPClientBase
from ap_monitor.app.main import insert_apclientcount_data

# Helper for radio mapping
radioId_map = {'radio0': 1, 'radio1': 2, 'radio2': 3}

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
        yield session
    finally:
        session.close()

def test_insert_apclientcount_data(session):
    # Clean up tables before test
    session.query(ClientCountAP).delete()
    session.query(AccessPoint).delete()
    session.query(Floor).delete()
    session.query(ApBuilding).delete()
    session.commit()
    # Insert radios
    for rname, rid in radioId_map.items():
        session.add(RadioType(radioid=rid, radioname=rname))
    session.commit()

    device_info_list = [
        {
            "name": "TestAP",
            "location": "Global/Campus/TestBuilding/TestFloor/TestRoom",
            "macAddress": "00:11:22:33:44:55",
            "ipAddress": "192.168.0.1",
            "model": "ModelX",
            "reachabilityHealth": "UP",
            "clientCount": {"radio0": 5, "radio1": 3}
        }
    ]
    timestamp = datetime.now()
    insert_apclientcount_data(device_info_list, timestamp, session=session)
    session.flush()
    # Debug: print all buildings
    all_buildings = session.query(ApBuilding).all()
    print(f"All buildings in DB: {[b.building_name for b in all_buildings]}")
    # Check Building
    building = session.query(ApBuilding).filter_by(building_name="TestBuilding").first()
    assert building is not None
    # Check Floor
    floor = session.query(Floor).filter_by(floorname="TestFloor", building_id=building.building_id).first()
    assert floor is not None
    # Check AccessPoint
    ap = session.query(AccessPoint).filter_by(macaddress="00:11:22:33:44:55").first()
    assert ap is not None
    assert ap.apname == "TestAP"
    # Check ClientCount
    client_counts = session.query(ClientCountAP).filter_by(apid=ap.apid).all()
    assert len(client_counts) == 2
    radio_counts = {cc.radioid: cc.clientcount for cc in client_counts}
    assert radio_counts[1] == 5  # radio0
    assert radio_counts[2] == 3  # radio1

def test_insert_apclientcount_data_invalid_location(session):
    # Should skip device with invalid location
    device_info_list = [
        {
            "name": "BadAP",
            "location": "/Global/CampusOnly",
            "macAddress": "00:11:22:33:44:99",
            "ipAddress": "192.168.0.2",
            "model": "ModelY",
            "reachabilityHealth": "UP",
            "clientCount": {"radio0": 2}
        }
    ]
    timestamp = datetime.now()
    insert_apclientcount_data(device_info_list, timestamp, session=session)
    session.flush()
    # Should not insert anything
    assert session.query(ApBuilding).count() == 0
    assert session.query(AccessPoint).count() == 0

def test_insert_apclientcount_data_existing_ap_update(session):
    # Should update existing AP, not duplicate
    for rname, rid in radioId_map.items():
        session.add(RadioType(radioid=rid, radioname=rname))
    session.commit()
    device_info_list = [
        {
            "name": "TestAP",
            "location": "/Global/Campus/TestBuilding/TestFloor/TestRoom",
            "macAddress": "00:11:22:33:44:55",
            "ipAddress": "192.168.0.1",
            "model": "ModelX",
            "reachabilityHealth": "UP",
            "clientCount": {"radio0": 5}
        }
    ]
    timestamp = datetime.now()
    insert_apclientcount_data(device_info_list, timestamp, session=session)
    # Insert again with different client count and status
    device_info_list[0]["clientCount"] = {"radio0": 7}
    device_info_list[0]["reachabilityHealth"] = "DOWN"
    insert_apclientcount_data(device_info_list, timestamp, session=session)
    session.flush()
    ap = session.query(AccessPoint).filter_by(macaddress="00:11:22:33:44:55").first()
    assert ap is not None
    # Check updated client count
    client_counts = session.query(ClientCountAP).filter_by(apid=ap.apid).all()
    assert len(client_counts) == 1
    assert client_counts[0].clientcount == 7
    assert client_counts[0].radioid == 1  # radio0
    assert ap.isactive is False

def test_insert_apclientcount_data_unexpected_radio(session):
    # Should skip unexpected radio keys
    session.add(RadioType(radioid=1, radioname="radio0"))
    session.commit()
    device_info_list = [
        {
            "name": "TestAP",
            "location": "/Global/Campus/TestBuilding/TestFloor/TestRoom",
            "macAddress": "00:11:22:33:44:77",
            "ipAddress": "192.168.0.3",
            "model": "ModelZ",
            "reachabilityHealth": "UP",
            "clientCount": {"radioX": 9, "radio0": 2}
        }
    ]
    timestamp = datetime.now()
    insert_apclientcount_data(device_info_list, timestamp, session=session)
    session.flush()
    ap = session.query(AccessPoint).filter_by(macaddress="00:11:22:33:44:77").first()
    assert ap is not None
    # Only radio0 should be inserted
    client_counts = session.query(ClientCountAP).filter_by(apid=ap.apid).all()
    assert len(client_counts) == 1
    assert client_counts[0].radioid == 1
    assert client_counts[0].clientcount == 2

def test_create_ap_building(session):
    # Clean up existing data
    session.query(ClientCountAP).delete()
    session.query(AccessPoint).delete()
    session.query(Room).delete()
    session.query(Floor).delete()
    session.query(ApBuilding).delete()
    session.query(RadioType).delete()
    session.commit()
    
    # Create test data
    building = ApBuilding(building_name="Test Building")
    session.add(building)
    session.commit()
    
    assert building.building_id is not None
    assert building.building_name == "Test Building"

def test_create_floor(session):
    # Clean up existing data
    session.query(ClientCountAP).delete()
    session.query(AccessPoint).delete()
    session.query(Room).delete()
    session.query(Floor).delete()
    session.query(ApBuilding).delete()
    session.query(RadioType).delete()
    session.commit()
    
    # Create test data
    building = ApBuilding(building_name="Test Building")
    session.add(building)
    session.commit()
    
    floor = Floor(
        building_id=building.building_id,
        floorname="1st Floor"
    )
    session.add(floor)
    session.commit()
    
    assert floor.floorid is not None
    assert floor.floorname == "1st Floor"
    assert floor.building_id == building.building_id

def test_create_room(session):
    # Clean up existing data
    session.query(ClientCountAP).delete()
    session.query(AccessPoint).delete()
    session.query(Room).delete()
    session.query(Floor).delete()
    session.query(ApBuilding).delete()
    session.query(RadioType).delete()
    session.commit()
    
    # Create test data
    building = ApBuilding(building_name="Test Building")
    session.add(building)
    session.commit()
    
    floor = Floor(
        building_id=building.building_id,
        floorname="1st Floor"
    )
    session.add(floor)
    session.commit()
    
    room = Room(
        floorid=floor.floorid,
        roomname="Test Room"
    )
    session.add(room)
    session.commit()
    
    assert room.roomid is not None
    assert room.roomname == "Test Room"
    assert room.floorid == floor.floorid

def test_create_access_point(session):
    # Clean up existing data
    session.query(ClientCountAP).delete()
    session.query(AccessPoint).delete()
    session.query(Room).delete()
    session.query(Floor).delete()
    session.query(ApBuilding).delete()
    session.query(RadioType).delete()
    session.commit()
    
    # Create test data
    building = ApBuilding(building_name="Test Building")
    session.add(building)
    session.commit()
    
    floor = Floor(
        building_id=building.building_id,
        floorname="1st Floor"
    )
    session.add(floor)
    session.commit()
    
    room = Room(
        floorid=floor.floorid,
        roomname="Test Room"
    )
    session.add(room)
    session.commit()
    
    ap = AccessPoint(
        building_id=building.building_id,
        floorid=floor.floorid,
        roomid=room.roomid,
        apname="Test AP",
        macaddress="00:11:22:33:44:55",
        ipaddress="192.168.1.1",
        modelname="Test Model"
    )
    session.add(ap)
    session.commit()
    
    assert ap.apid is not None
    assert ap.apname == "Test AP"
    assert ap.macaddress == "00:11:22:33:44:55"
    assert ap.ipaddress == "192.168.1.1"
    assert ap.modelname == "Test Model"
    assert ap.building_id == building.building_id
    assert ap.floorid == floor.floorid
    assert ap.roomid == room.roomid

def test_create_client_count(session):
    # Clean up existing data
    session.query(ClientCountAP).delete()
    session.query(AccessPoint).delete()
    session.query(Room).delete()
    session.query(Floor).delete()
    session.query(ApBuilding).delete()
    session.query(RadioType).delete()
    session.commit()
    
    # Create test data
    building = ApBuilding(building_name="Test Building")
    session.add(building)
    session.commit()
    
    floor = Floor(
        building_id=building.building_id,
        floorname="1st Floor"
    )
    session.add(floor)
    session.commit()
    
    room = Room(
        floorid=floor.floorid,
        roomname="Test Room"
    )
    session.add(room)
    session.commit()
    
    ap = AccessPoint(
        building_id=building.building_id,
        floorid=floor.floorid,
        roomid=room.roomid,
        apname="Test AP",
        macaddress="00:11:22:33:44:55",
        ipaddress="192.168.1.1",
        modelname="Test Model"
    )
    session.add(ap)
    session.commit()
    
    radio = RadioType(radioname="radio0", radioid=1)
    session.add(radio)
    session.commit()
    
    client_count = ClientCountAP(
        apid=ap.apid,
        radioid=radio.radioid,
        clientcount=10,
        timestamp=datetime.now(timezone.utc)
    )
    session.add(client_count)
    session.commit()
    
    assert client_count.countid is not None
    assert client_count.apid == ap.apid
    assert client_count.radioid == radio.radioid
    assert client_count.clientcount == 10

def test_get_client_count(session):
    # Clean up any existing data in the correct order
    session.query(ClientCountAP).delete()
    session.query(AccessPoint).delete()
    session.query(Room).delete()
    session.query(Floor).delete()
    session.query(ApBuilding).delete()
    session.query(RadioType).delete()
    session.commit()

    # Create required records
    building = ApBuilding(building_name="TestBuilding")
    session.add(building)
    session.flush()

    floor = Floor(floorname="TestFloor", building_id=building.building_id)
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
        building_id=building.building_id
    )
    session.add(ap)
    session.commit()

    # Create a client count
    client_count = ClientCountAP(
        apid=ap.apid,
        radioid=radio.radioid,
        clientcount=10,
        timestamp=datetime.now()
    )
    session.add(client_count)
    session.commit()

    # Get the client count
    result = session.query(ClientCountAP).first()
    assert result is not None
    assert result.clientcount == 10
    assert result.apid == ap.apid
    assert result.radioid == radio.radioid

def test_update_client_count(session):
    # Clean up any existing data in the correct order
    session.query(ClientCountAP).delete()
    session.query(AccessPoint).delete()
    session.query(Room).delete()
    session.query(Floor).delete()
    session.query(ApBuilding).delete()
    session.query(RadioType).delete()
    session.commit()

    # Create required records
    building = ApBuilding(building_name="TestBuilding")
    session.add(building)
    session.flush()

    floor = Floor(floorname="TestFloor", building_id=building.building_id)
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
        building_id=building.building_id
    )
    session.add(ap)
    session.commit()

    # Create a client count
    client_count = ClientCountAP(
        apid=ap.apid,
        radioid=radio.radioid,
        clientcount=10,
        timestamp=datetime.now()
    )
    session.add(client_count)
    session.commit()

    # Update the client count
    client_count.clientcount = 20
    session.commit()

    # Verify the update
    result = session.query(ClientCountAP).first()
    assert result is not None
    assert result.clientcount == 20
    assert result.apid == ap.apid
    assert result.radioid == radio.radioid

def test_delete_client_count(session):
    # Clean up any existing data in the correct order
    session.query(ClientCountAP).delete()
    session.query(AccessPoint).delete()
    session.query(Room).delete()
    session.query(Floor).delete()
    session.query(ApBuilding).delete()
    session.query(RadioType).delete()
    session.commit()

    # Create required records
    building = ApBuilding(building_name="TestBuilding")
    session.add(building)
    session.flush()

    floor = Floor(floorname="TestFloor", building_id=building.building_id)
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
        building_id=building.building_id
    )
    session.add(ap)
    session.commit()

    # Create a client count
    client_count = ClientCountAP(
        apid=ap.apid,
        radioid=radio.radioid,
        clientcount=10,
        timestamp=datetime.now()
    )
    session.add(client_count)
    session.commit()

    # Delete the client count
    session.delete(client_count)
    session.commit()

    # Verify the deletion
    result = session.query(ClientCountAP).first()
    assert result is None

def test_insert_apclientcount_data_partial_location(session):
    """Test handling of partial location information in AP data."""
    # Insert radios
    for rname, rid in radioId_map.items():
        session.add(RadioType(radioname=rname, radioid=rid))
    session.commit()

    # Test case 1: Two-part location format (Building/Floor)
    device_info_list = [
        {
            "name": "APOnlyBuilding",
            "location": "Bethune Residence/Floor 1",
            "macAddress": "00:11:22:33:44:01",
            "ipAddress": "192.168.0.10",
            "model": "ModelA",
            "reachabilityHealth": "UP",
            "clientCount": {"radio0": 1}
        }
    ]
    timestamp = datetime.now()
    insert_apclientcount_data(device_info_list, timestamp, session=session)
    session.flush()

    # Check Building
    building = session.query(ApBuilding).filter_by(building_name="Bethune Residence").first()
    assert building is not None, "Building should be created with name 'Bethune Residence'"

    # Check Floor
    floor = session.query(Floor).filter_by(floorname="Floor 1", building_id=building.building_id).first()
    assert floor is not None, "Floor should be created for the building"
    assert floor.floorname == "Floor 1", "Floor should have name 'Floor 1'"

    ap = session.query(AccessPoint).filter_by(macaddress="00:11:22:33:44:01").first()
    assert ap is not None, "Access point should be created"
    assert ap.apname == "APOnlyBuilding"
    assert ap.building_id == building.building_id
    assert ap.floorid == floor.floorid

    session.commit()  # Ensure building and floor are persisted before next insert

    # Test case 2: Five-part location format (Global/Campus/Building/Floor/Room)
    device_info_list = [
        {
            "name": "APBuildingFloor",
            "location": "Global/Keele Campus/Bethune Residence/Floor 5/Room 501",
            "macAddress": "00:11:22:33:44:02",
            "ipAddress": "192.168.0.11",
            "model": "ModelB",
            "reachabilityHealth": "UP",
            "clientCount": {"radio0": 2}
        }
    ]
    insert_apclientcount_data(device_info_list, timestamp, session=session)
    session.flush()

    # Check Building (should be "Bethune Residence" from location_parts[3])
    building = session.query(ApBuilding).filter_by(building_name="Bethune Residence").first()
    assert building is not None, "Building should still exist"

    # Check Floor (should be "Floor 5" from location_parts[4])
    floor = session.query(Floor).filter_by(floorname="Floor 5", building_id=building.building_id).first()
    assert floor is not None, "New floor should be created with name 'Floor 5'"

    ap = session.query(AccessPoint).filter_by(macaddress="00:11:22:33:44:02").first()
    assert ap is not None, "Second access point should be created"
    assert ap.apname == "APBuildingFloor"
    assert ap.building_id == building.building_id
    assert ap.floorid == floor.floorid

    client_count = session.query(ClientCountAP).filter_by(apid=ap.apid).first()
    assert client_count is not None, "Client count should be created"
    assert client_count.clientcount == 2
    assert client_count.radioid == 1  # radio0
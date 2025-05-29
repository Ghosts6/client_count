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
            "location": "Global/Keele Campus/TestBuilding/Floor 1",
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
    
    # Check Building
    building = session.query(ApBuilding).filter_by(buildingname="TestBuilding").first()
    assert building is not None
    # Check Floor
    floor = session.query(Floor).filter_by(floorname="Floor 1", buildingid=building.buildingid).first()
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

def test_insert_apclientcount_data_existing_ap_update(session):
    # Should update existing AP, not duplicate
    for rname, rid in radioId_map.items():
        session.add(RadioType(radioid=rid, radioname=rname))
    session.commit()
    device_info_list = [
        {
            "name": "TestAP",
            "location": "Global/Keele Campus/TestBuilding/Floor 1",
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
            "location": "Global/Keele Campus/TestBuilding/Floor 1",
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
    building = ApBuilding(buildingname="Test Building")
    session.add(building)
    session.commit()
    
    # Verify building was created
    assert building.buildingid is not None
    assert building.buildingname == "Test Building"

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
    building = ApBuilding(buildingname="Test Building")
    session.add(building)
    session.commit()
    
    floor = Floor(buildingid=building.buildingid, floorname="1st Floor")
    session.add(floor)
    session.commit()
    
    # Verify floor was created
    assert floor.floorid is not None
    assert floor.buildingid == building.buildingid
    assert floor.floorname == "1st Floor"

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
    building = ApBuilding(buildingname="Test Building")
    session.add(building)
    session.commit()
    
    floor = Floor(buildingid=building.buildingid, floorname="1st Floor")
    session.add(floor)
    session.commit()
    
    room = Room(floorid=floor.floorid, roomname="Room 101")
    session.add(room)
    session.commit()
    
    # Verify room was created
    assert room.roomid is not None
    assert room.floorid == floor.floorid
    assert room.roomname == "Room 101"

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
    building = ApBuilding(buildingname="Test Building")
    session.add(building)
    session.commit()
    
    floor = Floor(buildingid=building.buildingid, floorname="1st Floor")
    session.add(floor)
    session.commit()
    
    room = Room(floorid=floor.floorid, roomname="Room 101")
    session.add(room)
    session.commit()
    
    ap = AccessPoint(
        buildingid=building.buildingid,
        floorid=floor.floorid,
        roomid=room.roomid,
        apname="AP-01",
        macaddress="00:11:22:33:44:55",
        ipaddress="192.168.1.1",
        modelname="AIR-CAP3702I-A-K9",
        isactive=True
    )
    session.add(ap)
    session.commit()
    
    # Verify access point was created
    assert ap.apid is not None
    assert ap.buildingid == building.buildingid
    assert ap.floorid == floor.floorid
    assert ap.roomid == room.roomid
    assert ap.apname == "AP-01"
    assert ap.macaddress == "00:11:22:33:44:55"
    assert ap.ipaddress == "192.168.1.1"
    assert ap.modelname == "AIR-CAP3702I-A-K9"
    assert ap.isactive == True

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
    building = ApBuilding(buildingname="Test Building")
    session.add(building)
    session.commit()
    
    floor = Floor(buildingid=building.buildingid, floorname="1st Floor")
    session.add(floor)
    session.commit()
    
    room = Room(floorid=floor.floorid, roomname="Room 101")
    session.add(room)
    session.commit()
    
    ap = AccessPoint(
        buildingid=building.buildingid,
        floorid=floor.floorid,
        roomid=room.roomid,
        apname="AP-01",
        macaddress="00:11:22:33:44:55",
        ipaddress="192.168.1.1",
        modelname="AIR-CAP3702I-A-K9",
        isactive=True
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
    
    # Verify client count was created
    assert client_count.countid is not None
    assert client_count.apid == ap.apid
    assert client_count.radioid == radio.radioid
    assert client_count.clientcount == 10
    assert client_count.timestamp is not None

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
    building = ApBuilding(buildingname="TestBuilding")
    session.add(building)
    session.commit()

    floor = Floor(buildingid=building.buildingid, floorname="1st Floor")
    session.add(floor)
    session.commit()

    room = Room(floorid=floor.floorid, roomname="Room 101")
    session.add(room)
    session.commit()

    ap = AccessPoint(
        buildingid=building.buildingid,
        floorid=floor.floorid,
        roomid=room.roomid,
        apname="AP-01",
        macaddress="00:11:22:33:44:55",
        ipaddress="192.168.1.1",
        modelname="AIR-CAP3702I-A-K9",
        isactive=True
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

    # Test getting client count
    result = session.query(ClientCountAP).filter_by(apid=ap.apid).first()
    assert result is not None
    assert result.clientcount == 10
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
    building = ApBuilding(buildingname="TestBuilding")
    session.add(building)
    session.commit()

    floor = Floor(buildingid=building.buildingid, floorname="1st Floor")
    session.add(floor)
    session.commit()

    room = Room(floorid=floor.floorid, roomname="Room 101")
    session.add(room)
    session.commit()

    ap = AccessPoint(
        buildingid=building.buildingid,
        floorid=floor.floorid,
        roomid=room.roomid,
        apname="AP-01",
        macaddress="00:11:22:33:44:55",
        ipaddress="192.168.1.1",
        modelname="AIR-CAP3702I-A-K9",
        isactive=True
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

    # Update client count
    client_count.clientcount = 20
    session.commit()

    # Verify update
    result = session.query(ClientCountAP).filter_by(apid=ap.apid).first()
    assert result is not None
    assert result.clientcount == 20

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
    building = ApBuilding(buildingname="TestBuilding")
    session.add(building)
    session.commit()

    floor = Floor(buildingid=building.buildingid, floorname="1st Floor")
    session.add(floor)
    session.commit()

    room = Room(floorid=floor.floorid, roomname="Room 101")
    session.add(room)
    session.commit()

    ap = AccessPoint(
        buildingid=building.buildingid,
        floorid=floor.floorid,
        roomid=room.roomid,
        apname="AP-01",
        macaddress="00:11:22:33:44:55",
        ipaddress="192.168.1.1",
        modelname="AIR-CAP3702I-A-K9",
        isactive=True
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

    # Delete client count
    session.delete(client_count)
    session.commit()

    # Verify deletion
    result = session.query(ClientCountAP).filter_by(apid=ap.apid).first()
    assert result is None
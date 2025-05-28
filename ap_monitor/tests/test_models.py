from ap_monitor.app.models import (
    AccessPoint, ClientCount, Building, Floor, Campus, 
    ApBuilding, Room, RadioType, ClientCountAP
)
from datetime import datetime, timezone
from sqlalchemy.exc import IntegrityError
import pytest
import time
from decimal import Decimal
import ipaddress

@pytest.fixture(autouse=True)
def cleanup_database(wireless_db, apclient_db):
    """Clean up the database before each test."""
    # Clean wireless_count database
    wireless_db.query(ClientCount).delete()
    wireless_db.query(Building).delete()
    wireless_db.query(Campus).delete()
    wireless_db.commit()

    # Clean apclientcount database
    apclient_db.query(ClientCountAP).delete()
    apclient_db.query(AccessPoint).delete()
    apclient_db.query(Room).delete()
    apclient_db.query(Floor).delete()
    apclient_db.query(ApBuilding).delete()
    apclient_db.query(RadioType).delete()
    apclient_db.commit()

def test_create_campus(wireless_db):
    """Test creating a campus."""
    campus = Campus(campus_name="Test Campus 1")
    wireless_db.add(campus)
    wireless_db.commit()
    assert campus.campus_id is not None
    assert campus.campus_name == "Test Campus 1"

def test_create_building(wireless_db):
    """Test creating a building."""
    # Create campus first
    campus = Campus(campus_name="Test Campus 2")
    wireless_db.add(campus)
    wireless_db.commit()

    # Create building
    building = Building(
        building_name="Test Building 1",
        campus_id=campus.campus_id,
        latitude=37.7749,
        longitude=-122.4194
    )
    wireless_db.add(building)
    wireless_db.commit()
    assert building.building_id is not None
    assert building.building_name == "Test Building 1"
    assert building.campus_id == campus.campus_id

def test_create_client_count(wireless_db):
    """Test creating a client count."""
    # Create campus and building
    campus = Campus(campus_name="Test Campus 3")
    wireless_db.add(campus)
    wireless_db.commit()

    building = Building(
        building_name="Test Building 2",
        campus_id=campus.campus_id,
        latitude=37.7749,
        longitude=-122.4194
    )
    wireless_db.add(building)
    wireless_db.commit()

    # Create client count
    client_count = ClientCount(
        building_id=building.building_id,
        client_count=10
    )
    wireless_db.add(client_count)
    wireless_db.commit()
    assert client_count.count_id is not None
    assert client_count.client_count == 10
    assert client_count.building_id == building.building_id

def test_create_room_and_access_point(apclient_db):
    """Test creating a room and access point."""
    # Create building, floor, and room
    building = ApBuilding(buildingname="Test Building 3")
    apclient_db.add(building)
    apclient_db.commit()

    floor = Floor(buildingid=building.buildingid, floorname="1st Floor")
    apclient_db.add(floor)
    apclient_db.commit()

    room = Room(floorid=floor.floorid, roomname="Room 101")
    apclient_db.add(room)
    apclient_db.commit()

    # Create access point
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
    apclient_db.add(ap)
    apclient_db.commit()

    # Verify relationships
    assert ap.buildingid == building.buildingid
    assert ap.floorid == floor.floorid
    assert ap.roomid == room.roomid

def test_create_radio_and_client_count(apclient_db):
    """Test creating a radio type and client count."""
    # Create building, floor, room, and AP
    building = ApBuilding(buildingname="Test Building 4")
    apclient_db.add(building)
    apclient_db.commit()

    floor = Floor(buildingid=building.buildingid, floorname="1st Floor")
    apclient_db.add(floor)
    apclient_db.commit()

    room = Room(floorid=floor.floorid, roomname="Room 101")
    apclient_db.add(room)
    apclient_db.commit()

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
    apclient_db.add(ap)
    apclient_db.commit()

    # Create radio type
    radio = RadioType(radioname="radio0", radioid=1)
    apclient_db.add(radio)
    apclient_db.commit()

    # Create client count
    client_count = ClientCountAP(
        apid=ap.apid,
        radioid=radio.radioid,
        clientcount=10,
        timestamp=datetime.now(timezone.utc)
    )
    apclient_db.add(client_count)
    apclient_db.commit()

    # Verify relationships
    assert client_count.apid == ap.apid
    assert client_count.radioid == radio.radioid

def test_unique_constraints(wireless_db, apclient_db):
    """Test unique constraints."""
    # Test campus name uniqueness
    campus1 = Campus(campus_name="Test Campus 5")
    wireless_db.add(campus1)
    wireless_db.commit()

    campus2 = Campus(campus_name="Test Campus 5")
    wireless_db.add(campus2)
    with pytest.raises(IntegrityError):
        wireless_db.commit()
    wireless_db.rollback()

    # Test building name uniqueness
    building1 = ApBuilding(buildingname="Test Building 5")
    apclient_db.add(building1)
    apclient_db.commit()

    building2 = ApBuilding(buildingname="Test Building 5")
    apclient_db.add(building2)
    with pytest.raises(IntegrityError):
        apclient_db.commit()
    apclient_db.rollback()

def test_cascade_delete(wireless_db, apclient_db):
    """Test cascade delete functionality."""
    # Test wireless_count cascade
    campus = Campus(campus_name="Test Campus 6")
    wireless_db.add(campus)
    wireless_db.commit()

    building = Building(
        building_name="Test Building 6",
        campus_id=campus.campus_id,
        latitude=37.7749,
        longitude=-122.4194
    )
    wireless_db.add(building)
    wireless_db.commit()

    client_count = ClientCount(
        building_id=building.building_id,
        client_count=10
    )
    wireless_db.add(client_count)
    wireless_db.commit()

    # Delete campus and verify cascade
    wireless_db.delete(campus)
    wireless_db.commit()

    assert wireless_db.query(Building).filter_by(building_id=building.building_id).first() is None
    assert wireless_db.query(ClientCount).filter_by(count_id=client_count.count_id).first() is None

    # Test apclientcount cascade
    building = ApBuilding(buildingname="Test Building 7")
    apclient_db.add(building)
    apclient_db.commit()

    floor = Floor(buildingid=building.buildingid, floorname="1st Floor")
    apclient_db.add(floor)
    apclient_db.commit()

    room = Room(floorid=floor.floorid, roomname="Room 101")
    apclient_db.add(room)
    apclient_db.commit()

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
    apclient_db.add(ap)
    apclient_db.commit()

    radio = RadioType(radioname="radio0", radioid=1)
    apclient_db.add(radio)
    apclient_db.commit()

    client_count = ClientCountAP(
        apid=ap.apid,
        radioid=radio.radioid,
        clientcount=10,
        timestamp=datetime.now(timezone.utc)
    )
    apclient_db.add(client_count)
    apclient_db.commit()

    # Delete building and verify cascade
    apclient_db.delete(building)
    apclient_db.commit()

    assert apclient_db.query(Floor).filter_by(floorid=floor.floorid).first() is None
    assert apclient_db.query(Room).filter_by(roomid=room.roomid).first() is None
    assert apclient_db.query(AccessPoint).filter_by(apid=ap.apid).first() is None
    assert apclient_db.query(ClientCountAP).filter_by(countid=client_count.countid).first() is None
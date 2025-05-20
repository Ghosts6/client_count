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

# Tests for wireless_count schema
def test_create_campus_and_building(session):
    campus = Campus(campus_name="Main Campus")
    session.add(campus)
    session.commit()
    
    building = Building(
        building_name="Engineering Building",
        campus_id=campus.campus_id,
        latitude=37.7749,
        longitude=-122.4194
    )
    session.add(building)
    session.commit()
    
    assert building.building_id is not None
    assert building.campus.campus_name == "Main Campus"

def test_create_client_count(session):
    campus = Campus(campus_name="North Campus")
    session.add(campus)
    session.commit()
    
    building = Building(
        building_name="Science Building",
        campus_id=campus.campus_id,
        latitude=37.7749,
        longitude=-122.4194
    )
    session.add(building)
    session.commit()
    
    client_count = ClientCount(
        building_id=building.building_id,
        client_count=50
    )
    session.add(client_count)
    session.commit()
    
    assert client_count.count_id is not None
    assert client_count.client_count == 50

# Tests for apclientcount schema
def test_create_ap_building_and_floor(session):
    building = ApBuilding(buildingname="Main Hall")
    session.add(building)
    session.commit()
    
    floor = Floor(floorname="1", buildingid=building.buildingid)
    session.add(floor)
    session.commit()
    
    assert floor.floorid is not None
    assert floor.building.buildingname == "Main Hall"

def test_create_room_and_access_point(session):
    building = ApBuilding(buildingname="Main Hall")
    session.add(building)
    session.commit()
    
    floor = Floor(floorname="1", buildingid=building.buildingid)
    session.add(floor)
    session.commit()
    
    room = Room(roomname="Lab A", floorid=floor.floorid)
    session.add(room)
    session.commit()
    
    # Convert IP address to string for SQLite compatibility
    ip_str = str(ipaddress.ip_address("192.168.1.100"))
    ap = AccessPoint(
        apname="AP-SH-1",
        macaddress="11:22:33:44:55:66",
        ipaddress=ip_str,
        modelname="Cisco 3800",
        isactive=True,
        floorid=floor.floorid,
        roomid=room.roomid,
        buildingid=building.buildingid
    )
    session.add(ap)
    session.commit()
    
    assert ap.apid is not None
    assert ap.apname == "AP-SH-1"

def test_create_radio_and_client_count(session):
    radio = RadioType(radioname="5GHz")
    session.add(radio)
    session.commit()
    
    building = ApBuilding(buildingname="Test Building")
    session.add(building)
    session.commit()
    
    floor = Floor(floorname="1", buildingid=building.buildingid)
    session.add(floor)
    session.commit()
    
    ap = AccessPoint(
        apname="TestAP",
        macaddress="FF:EE:DD:CC:BB:AA",
        floorid=floor.floorid,
        buildingid=building.buildingid
    )
    session.add(ap)
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
    assert client_count.clientcount == 10

def test_unique_constraints(session):
    # Test unique campus name
    campus1 = Campus(campus_name="Unique Campus")
    session.add(campus1)
    session.commit()

    # Try to create another campus with the same name
    campus2 = Campus(campus_name="Unique Campus")
    session.add(campus2)
    
    # This should raise an IntegrityError
    with pytest.raises(Exception) as exc_info:
        session.commit()
    assert "UNIQUE constraint failed" in str(exc_info.value)

def test_cascade_delete(session):
    # Test cascade delete for AP building
    building = ApBuilding(buildingname="CascadeTest")
    session.add(building)
    session.commit()
    
    floor = Floor(floorname="1", buildingid=building.buildingid)
    session.add(floor)
    session.commit()
    
    room = Room(roomname="Test Room", floorid=floor.floorid)
    session.add(room)
    session.commit()
    
    ap = AccessPoint(
        apname="TestAP",
        macaddress="11:22:33:44:55:66",
        floorid=floor.floorid,
        roomid=room.roomid,
        buildingid=building.buildingid
    )
    session.add(ap)
    session.commit()
    
    # Delete the building
    session.delete(building)
    session.commit()
    
    # Verify cascade delete
    assert session.query(Floor).filter_by(floorid=floor.floorid).first() is None
    assert session.query(Room).filter_by(roomid=room.roomid).first() is None
    assert session.query(AccessPoint).filter_by(apid=ap.apid).first() is None
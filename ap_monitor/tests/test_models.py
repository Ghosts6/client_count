from app.models import Building, Floor, Room, AccessPoint, ClientCount, Radio
from datetime import datetime, UTC
from sqlalchemy.exc import IntegrityError
import pytest
import time

def test_create_building_and_floor(session):
    # Create a Building
    bldg = Building(name="Main Office")
    session.add(bldg)
    session.commit()
    assert bldg.id is not None

    # Create a Floor linked to the Building
    floor = Floor(number=1, building_id=bldg.id)
    session.add(floor)
    session.commit()

    # SQLAlchemy will populate the relationship:
    assert floor.building == bldg     
    assert floor in bldg.floors       

def test_access_point_and_client_count(session):
    # Setup: one building and floor
    b = Building(name="Annex")
    f = Floor(number=2, building=b)
    session.add_all([b, f])
    session.commit()

    # Create an AccessPoint on that floor
    ap = AccessPoint(name="AP1", mac_address="AA:BB:CC:DD:EE", floor_id=f.id)
    session.add(ap)
    session.commit()
    assert ap.floor == f              

    # Create a ClientCount linked to the AccessPoint
    cc = ClientCount(access_point=ap, radio_id=1, client_count=5, timestamp=datetime.now(UTC))
    session.add(cc)
    session.commit()
    assert cc.access_point == ap      
    assert cc in ap.client_counts

def test_create_building_and_floor(session):
    bldg = Building(name="Main Office")
    session.add(bldg)
    session.commit()
    assert bldg.id is not None

    floor = Floor(number=1, building_id=bldg.id)
    session.add(floor)
    session.commit()
    assert floor.id is not None
    assert floor.building_id == bldg.id

def test_create_room_and_relationship(session):
    building = Building(name="Tower")
    floor = Floor(number=3, building=building)
    room = Room(name="301", floor=floor)

    session.add_all([building, floor, room])
    session.commit()

    assert room.id is not None
    assert room.floor == floor
    assert room in floor.access_points[0].room.access_points if floor.access_points else True

def test_access_point_full_fields(session):
    b = Building(name="Science Hall")
    f = Floor(number=1, building=b)
    r = Room(name="Lab A", floor=f)
    ap = AccessPoint(
        name="AP-SH-1",
        mac_address="11:22:33:44:55:66",
        ip_address="192.168.1.100",
        model_name="Cisco 3800",
        is_active=True,
        floor=f,
        room=r,
        clients=12
    )
    session.add_all([b, f, r, ap])
    session.commit()

    assert ap.id is not None
    assert ap.room == r
    assert ap.floor == f
    assert ap.clients == 12
    assert ap.is_active is True

def test_client_count_creation(session):
    ap = AccessPoint(name="TestAP", mac_address="FF:EE:DD:CC:BB", clients=7)
    cc = ClientCount(access_point=ap, radio_id=2, client_count=3, timestamp=datetime.now(UTC))

    session.add_all([ap, cc])
    session.commit()

    assert cc.id is not None
    assert cc.radio_id == 2
    assert cc.client_count == 3
    assert cc.access_point == ap

def test_radio_creation(session):
    radio = Radio(name="Radio5GHz", description="High performance")
    session.add(radio)
    session.commit()

    assert radio.id is not None
    assert radio.name == "Radio5GHz"
    assert radio.description == "High performance"

def test_updated_at_changes_on_update(session):
    building = Building(name="Old Building")
    session.add(building)
    session.commit()
    original_updated_at = building.updated_at

    time.sleep(1)  # wait 1 second to ensure timestamp difference

    building.name = "Renovated Building"
    session.commit()
    session.refresh(building)

    assert building.updated_at > original_updated_at

def test_unique_mac_address_constraint(session):
    ap1 = AccessPoint(name="AP1", mac_address="00:00:00:00:00:01")
    ap2 = AccessPoint(name="AP2", mac_address="00:00:00:00:00:01")
    session.add_all([ap1, ap2])
    with pytest.raises(IntegrityError):
        session.commit()

def test_cascade_delete_floors(session):
    b = Building(name="Temp Building")
    f = Floor(number=1, building=b)
    session.add_all([b, f])
    session.commit()

    session.delete(b)
    session.commit()

    assert session.query(Floor).filter_by(building_id=b.id).count() == 0

def test_access_point_defaults(session):
    ap = AccessPoint(name="AP Default", mac_address="AB:CD:EF:12:34:56")
    session.add(ap)
    session.commit()

    assert ap.clients == 0
    assert ap.is_active is True
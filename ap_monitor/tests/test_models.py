from app.models import Building, Floor, Room, AccessPoint, ClientCount, Radio
from datetime import datetime, UTC

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
import pytest
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from ap_monitor.app.models import ApBuilding, Floor, Room, AccessPoint, ClientCountAP, RadioType, Base
from ap_monitor.app.main import insert_apclientcount_data

# Helper for radio mapping
radioId_map = {'radio0': 1, 'radio1': 2, 'radio2': 3}

def test_insert_apclientcount_data():
    # Create an in-memory SQLite DB for testing, using the same Base as the app
    engine = create_engine("sqlite:///:memory:")
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
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
            "location": "/Global/Campus/TestBuilding/TestFloor/TestRoom",
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
    print(f"All buildings in DB: {[b.buildingname for b in all_buildings]}")
    # Check Building
    building = session.query(ApBuilding).filter_by(buildingname="TestBuilding").first()
    assert building is not None
    # Check Floor
    floor = session.query(Floor).filter_by(floorname="TestFloor", buildingid=building.buildingid).first()
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
    session.close()

def test_insert_apclientcount_data_invalid_location():
    # Should skip device with invalid location
    engine = create_engine("sqlite:///:memory:")
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
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
    session.close()

def test_insert_apclientcount_data_existing_ap_update():
    # Should update existing AP, not duplicate
    engine = create_engine("sqlite:///:memory:")
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
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
    session.close()

def test_insert_apclientcount_data_unexpected_radio():
    # Should skip unexpected radio keys
    engine = create_engine("sqlite:///:memory:")
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
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
    session.close()
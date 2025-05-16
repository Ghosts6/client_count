import pytest
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models import Building, Floor, AccessPoint, ClientCount, Base
from app.main import insert_apclientcount_data

def test_insert_apclientcount_data():
    # Create an in-memory SQLite DB for testing, using the same Base as the app
    engine = create_engine("sqlite:///:memory:")
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    # Clean up tables before test
    session.query(ClientCount).delete()
    session.query(AccessPoint).delete()
    session.query(Floor).delete()
    session.query(Building).delete()
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
    all_buildings = session.query(Building).all()
    print(f"All buildings in DB: {[b.name for b in all_buildings]}")
    # Check Building
    building = session.query(Building).filter_by(name="TestBuilding").first()
    assert building is not None
    # Check Floor
    floor = session.query(Floor).filter_by(number="TestFloor", building_id=building.id).first()
    assert floor is not None
    # Check AccessPoint
    ap = session.query(AccessPoint).filter_by(mac_address="00:11:22:33:44:55").first()
    assert ap is not None
    assert ap.name == "TestAP"
    assert ap.clients == 8
    # Check ClientCount
    client_counts = session.query(ClientCount).filter_by(ap_id=ap.id).all()
    assert len(client_counts) == 2
    radio_counts = {cc.radio_id: cc.client_count for cc in client_counts}
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
    assert session.query(Building).count() == 0
    assert session.query(AccessPoint).count() == 0
    session.close()

def test_insert_apclientcount_data_existing_ap_update():
    # Should update existing AP, not duplicate
    engine = create_engine("sqlite:///:memory:")
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
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
    ap = session.query(AccessPoint).filter_by(mac_address="00:11:22:33:44:55").first()
    assert ap is not None
    assert ap.clients == 7
    assert ap.is_active is False
    session.close()

def test_insert_apclientcount_data_unexpected_radio():
    # Should skip unexpected radio keys
    engine = create_engine("sqlite:///:memory:")
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
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
    ap = session.query(AccessPoint).filter_by(mac_address="00:11:22:33:44:77").first()
    assert ap is not None
    # Only radio0 should be inserted
    client_counts = session.query(ClientCount).filter_by(ap_id=ap.id).all()
    assert len(client_counts) == 1
    assert client_counts[0].radio_id == 1
    assert client_counts[0].client_count == 2
    session.close()
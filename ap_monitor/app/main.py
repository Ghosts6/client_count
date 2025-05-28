import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, Query
from contextlib import asynccontextmanager
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.ext.declarative import declarative_base
import os

from ap_monitor.app.db import get_wireless_db, get_apclient_db, init_db, WirelessBase, APClientBase
from ap_monitor.app.models import (
    Campus, Building, ClientCount,
    ApBuilding, Floor, Room, AccessPoint, RadioType, ClientCountAP
)
from ap_monitor.app.dna_api import AuthManager, fetch_client_counts, fetch_ap_data, radio_id_map
from ap_monitor.app.utils import setup_logging, calculate_next_run_time
from ap_monitor.app.schemas import (
    CampusCreate, CampusResponse,
    BuildingCreate, BuildingResponse,
    ClientCountCreate, ClientCountResponse,
    ApBuildingCreate, ApBuildingResponse,
    FloorCreate, FloorResponse,
    RoomCreate, RoomResponse,
    AccessPointCreate, AccessPointResponse,
    RadioTypeCreate, RadioTypeResponse,
    ClientCountAPCreate, ClientCountAPResponse
)

def get_database_url():
    if os.getenv("TESTING", "false").lower() == "true":
        return "sqlite:///:memory:"
    return "postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}".format(
        DB_USER=os.getenv("DB_USER", "postgres"),
        DB_PASSWORD=os.getenv("DB_PASSWORD"),
        DB_HOST=os.getenv("DB_HOST", "localhost"),
        DB_PORT=os.getenv("DB_PORT", "3306"),
        DB_NAME=os.getenv("DB_NAME", "wireless_count")
    )

# Dynamically determine DATABASE_URL
DATABASE_URL = get_database_url()

# Set up logging first
logger = setup_logging()

# Log the database URL being used
logger.info(f"Using DATABASE_URL: {DATABASE_URL}")

def initialize_database():
    global engine, TestingSessionLocal
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    # Initialize both databases only in non-test mode
    if os.getenv("TESTING", "false").lower() != "true":
        # Create wireless_count tables
        WirelessBase.metadata.create_all(bind=engine)
        logger.info("Wireless count database tables created successfully")
        
        # Create apclientcount tables
        APClientBase.metadata.create_all(bind=apclient_engine)
        logger.info("AP client count database tables created successfully")
    # Ensure apclientcount tables exist in test mode
    elif os.getenv("TESTING", "false").lower() == "true":
        APClientBase.metadata.create_all(bind=apclient_engine)
        logger.info("Test AP client count database tables created successfully")

# Initialize database engine and session factory
engine = None
TestingSessionLocal = None

# Initialize apclientcount database only in non-test mode
if os.getenv("TESTING", "false").lower() != "true":
    APCLIENT_DB_URL = os.getenv("APCLIENT_DB_URL", "postgresql://postgres:@localhost:3306/apclientcount")
    apclient_engine = create_engine(APCLIENT_DB_URL)
    ApclientSessionLocal = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=apclient_engine))
else:
    # In test mode, use the same in-memory database for both
    from sqlalchemy import create_engine
    apclient_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    ApclientSessionLocal = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=apclient_engine))
    # Ensure apclientcount tables exist immediately
    APClientBase.metadata.create_all(bind=apclient_engine)

# Reinitialize the database engine and session factory
initialize_database()

# Initialize scheduler
scheduler = BackgroundScheduler()

# Create auth manager for DNA Center API
auth_manager = AuthManager()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handles app startup and shutdown using the FastAPI lifespan context."""
    try:
        # === STARTUP ===
        logger.info("App starting up...")

        # Initialize database
        logger.info("Initializing database...")
        init_db()
        initialize_database()  # Initialize both databases
        logger.info("Database initialized successfully")

        # Initialize radio table if empty
        with next(get_wireless_db()) as db:
            if db.query(RadioType).count() == 0:
                logger.info("Initializing radio data...")
                radios = [
                    RadioType(radioid=1, radioname="radio0"),
                    RadioType(radioid=2, radioname="radio1"),
                    RadioType(radioid=3, radioname="radio2")
                ]
                db.add_all(radios)
                db.commit()
                logger.info("Radio data initialized successfully")

        # Schedule tasks
        next_run = calculate_next_run_time()
        logger.info(f"First scheduled run at: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")

        scheduler.add_job(
            func=update_ap_data_task,
            trigger=DateTrigger(run_date=next_run),
            id="update_ap_data_task",
            name="Update AP Data Task",
            replace_existing=True,
        )
        scheduler.add_job(
            func=update_client_count_task,
            trigger=DateTrigger(run_date=next_run),
            id="update_client_count_task",
            name="Update Client Count Task",
            replace_existing=True,
        )

        scheduler.start()
        logger.info("Scheduler started successfully")

        yield

    finally:
        # === SHUTDOWN ===
        logger.info("App shutting down...")
        logger.info("Shutting down scheduler...")
        scheduler.shutdown()
        logger.info("Scheduler shut down successfully")

# Create FastAPI application with lifespan handler
app = FastAPI(
    title="AP Monitor",
    description="API for monitoring wireless access points and client counts",
    version="1.0.0",
    lifespan=lifespan,
)

def update_ap_data_task(db: Session = None, auth_manager_obj=None, fetch_ap_data_func=None):
    """Background task to update AP data in the database."""
    auth_manager_obj = auth_manager_obj or auth_manager
    fetch_ap_data_func = fetch_ap_data_func or fetch_ap_data
    close_db = False
    if db is None:
        db = next(get_apclient_db())  # Use apclient_db instead of wireless_db
        close_db = True
    try:
        logger.info("Running scheduled task: update_ap_data_task")
        logger.debug(f"Database session being used: {db}")
        now = datetime.now(timezone.utc)
        rounded_unix_timestamp = int(now.timestamp() * 1000)
        aps = fetch_ap_data_func(auth_manager_obj, rounded_unix_timestamp)
        logger.info(f"Fetched {len(aps)} APs from DNAC API")

        # Process AP data
        for ap in aps:
            ap_name = ap.get('name')
            location = ap.get('location', '')
            location_parts = location.split('/') if location else []
            
            # Handle location parsing based on real data format
            if not location_parts:
                logger.warning(f"Skipping device {ap_name} due to invalid location format: {location}")
                continue
                
            # Extract building and floor from location
            if len(location_parts) >= 5:
                building_name = location_parts[3]  # e.g., "Keele Campus"
                floor_name = location_parts[4]     # e.g., "Floor 5"
            elif len(location_parts) == 2:
                building_name = location_parts[0]
                floor_name = location_parts[1]
            else:
                logger.warning(f"Skipping device {ap_name} due to invalid location format: {location}")
                continue

            # Building
            building = db.query(ApBuilding).filter_by(building_name=building_name).first()
            if not building:
                building = ApBuilding(building_name=building_name)
                db.add(building)
                db.flush()

            # Floor
            floor = db.query(Floor).filter_by(floorname=floor_name, building_id=building.building_id).first()
            if not floor:
                floor = Floor(floorname=floor_name, building_id=building.building_id)
                db.add(floor)
                db.flush()

            # Access Point
            mac_address = ap.get('macAddress')
            ap_record = db.query(AccessPoint).filter_by(macaddress=mac_address).first()
            is_active = ap.get('reachabilityHealth') == "UP"
            
            if not ap_record:
                logger.debug(f"Creating new AccessPoint: {ap.get('name')} with MAC: {mac_address}")
                ap_record = AccessPoint(
                    apname=ap.get('name'),
                    macaddress=mac_address,
                    ipaddress=ap.get('ipAddress'),
                    modelname=ap.get('model'),
                    isactive=is_active,
                    floorid=floor.floorid,
                    building_id=building.building_id
                )
                db.add(ap_record)
                db.flush()
            else:
                logger.debug(f"Updating existing AccessPoint: {ap.get('name')}")
                ap_record.isactive = is_active
                ap_record.floorid = floor.floorid
                ap_record.building_id = building.building_id

            # Create client count records for each radio
            client_counts = ap.get('clientCount', {})
            for radio_name, count in client_counts.items():
                radio = db.query(RadioType).filter_by(radioname=radio_name).first()
                if not radio:
                    logger.warning(f"Unexpected radio key: {radio_name}")
                    continue

                # Check for existing record
                cc = db.query(ClientCountAP).filter_by(
                    apid=ap_record.apid,
                    radioid=radio.radioid,
                    timestamp=now
                ).first()

                if cc:
                    cc.clientcount = count
                else:
                    cc = ClientCountAP(
                        apid=ap_record.apid,
                        radioid=radio.radioid,
                        clientcount=count,
                        timestamp=now
                    )
                    db.add(cc)
        
        db.commit()
        logger.info("AP data updated successfully in apclientcount DB")

    except Exception as e:
        db.rollback()
        logger.error(f"Error updating AP data: {e}")
    finally:
        if close_db:
            db.close()
        next_run = calculate_next_run_time()
        scheduler.add_job(
            func=update_ap_data_task,
            trigger=DateTrigger(run_date=next_run),
            id="update_ap_data_task",
            name="Update AP Data Task",
            replace_existing=True,
        )
        logger.info(f"Next AP data update scheduled at {next_run.strftime('%Y-%m-%d %H:%M:%S')}")

def update_client_count_task(db: Session = None, auth_manager_obj=None, fetch_client_counts_func=None, fetch_ap_data_func=None):
    """Background task to update client count data in the database."""
    auth_manager_obj = auth_manager_obj or auth_manager
    fetch_client_counts_func = fetch_client_counts_func or fetch_client_counts
    fetch_ap_data_func = fetch_ap_data_func or fetch_ap_data
    close_db = False
    if db is None:
        db = next(get_apclient_db())  # Use apclient_db instead of wireless_db
        close_db = True
    try:
        logger.info("Running scheduled task: update_client_count_task")
        now = datetime.now(timezone.utc)
        rounded_unix_timestamp = int(now.timestamp() * 1000)
        
        # Fetch data from DNA Center API
        ap_data = fetch_ap_data_func(auth_manager_obj, rounded_unix_timestamp)
        site_data = fetch_client_counts_func(auth_manager_obj, rounded_unix_timestamp)
        
        # Process AP data for apclientcount DB
        for ap in ap_data:
            ap_name = ap.get('name')
            location = ap.get('location', '')
            location_parts = location.split('/') if location else []
            
            # Handle location parsing based on real data format
            if not location_parts:
                logger.warning(f"Skipping device {ap_name} due to invalid location format: {location}")
                continue
                
            # Extract building and floor from location
            if len(location_parts) >= 5:
                building_name = location_parts[3]  # e.g., "Keele Campus"
                floor_name = location_parts[4]     # e.g., "Floor 5"
            elif len(location_parts) == 2:
                building_name = location_parts[0]
                floor_name = location_parts[1]
            else:
                logger.warning(f"Skipping device {ap_name} due to invalid location format: {location}")
                continue

            # Building
            building = db.query(ApBuilding).filter_by(building_name=building_name).first()
            if not building:
                building = ApBuilding(building_name=building_name)
                db.add(building)
                db.flush()

            # Floor
            floor = db.query(Floor).filter_by(floorname=floor_name, building_id=building.building_id).first()
            if not floor:
                floor = Floor(floorname=floor_name, building_id=building.building_id)
                db.add(floor)
                db.flush()

            # Access Point
            mac_address = ap.get('macAddress')
            ap_record = db.query(AccessPoint).filter_by(macaddress=mac_address).first()
            is_active = ap.get('reachabilityHealth') == "UP"
            
            if not ap_record:
                logger.debug(f"Creating new AccessPoint: {ap.get('name')} with MAC: {mac_address}")
                ap_record = AccessPoint(
                    apname=ap.get('name'),
                    macaddress=mac_address,
                    ipaddress=ap.get('ipAddress'),
                    modelname=ap.get('model'),
                    isactive=is_active,
                    floorid=floor.floorid,
                    building_id=building.building_id
                )
                db.add(ap_record)
                db.flush()
            else:
                logger.debug(f"Updating existing AccessPoint: {ap.get('name')}")
                ap_record.isactive = is_active
                ap_record.floorid = floor.floorid
                ap_record.building_id = building.building_id

            # Create client count records for each radio
            client_counts = ap.get('clientCount', {})
            for radio_name, count in client_counts.items():
                radio = db.query(RadioType).filter_by(radioname=radio_name).first()
                if not radio:
                    logger.warning(f"Unexpected radio key: {radio_name}")
                    continue

                # Check for existing record
                cc = db.query(ClientCountAP).filter_by(
                    apid=ap_record.apid,
                    radioid=radio.radioid,
                    timestamp=now
                ).first()

                if cc:
                    cc.clientcount = count
                else:
                    cc = ClientCountAP(
                        apid=ap_record.apid,
                        radioid=radio.radioid,
                        clientcount=count,
                        timestamp=now
                    )
                    db.add(cc)
        
        db.commit()
        logger.info("Client count data updated successfully in apclientcount DB")

    except Exception as e:
        db.rollback()
        logger.error(f"Error updating client count data: {e}")
    finally:
        if close_db:
            db.close()
        next_run = calculate_next_run_time()
        scheduler.add_job(
            func=update_client_count_task,
            trigger=DateTrigger(run_date=next_run),
            id="update_client_count_task",
            name="Update Client Count Task",
            replace_existing=True,
        )
        logger.info(f"Next client count update scheduled at {next_run.strftime('%Y-%m-%d %H:%M:%S')}")

def insert_apclientcount_data(device_info_list, timestamp, session=None):
    from ap_monitor.app.models import ApBuilding, Floor, Room, AccessPoint, ClientCountAP, RadioType
    close_session = False
    if session is None:
        session = APclientSessionLocal()
        close_session = True
    try:
        # Ensure radio types exist
        radio_types = {
            "radio0": 1,
            "radio1": 2,
            "radio2": 3
        }
        for radio_name, radio_id in radio_types.items():
            radio = session.query(RadioType).filter_by(radioname=radio_name).first()
            if not radio:
                radio = RadioType(radioname=radio_name, radioid=radio_id)
                session.add(radio)
        session.flush()

        for device in device_info_list:
            ap_name = device['name']
            location = device.get('location', '')
            location_parts = location.split('/') if location else []
            
            # Handle location parsing based on real data format
            if not location_parts:
                logger.warning(f"Skipping device {ap_name} due to invalid location format: {location}")
                continue
                
            # Extract building and floor from location
            if len(location_parts) >= 5:
                building_name = location_parts[2]  # e.g., "Bethune Residence"
                floor_name = location_parts[3]     # e.g., "Floor 5"
            elif len(location_parts) == 2:
                building_name = location_parts[0]
                floor_name = location_parts[1]
            else:
                logger.warning(f"Skipping device {ap_name} due to invalid location format: {location}")
                continue

            # Building
            building = session.query(ApBuilding).filter_by(building_name=building_name).first()
            if not building:
                building = ApBuilding(building_name=building_name)
                session.add(building)
                session.flush()

            # Floor
            floor = session.query(Floor).filter_by(floorname=floor_name, building_id=building.building_id).first()
            if not floor:
                floor = Floor(floorname=floor_name, building_id=building.building_id)
                session.add(floor)
                session.flush()

            # Access Point
            mac_address = device['macAddress']
            ap = session.query(AccessPoint).filter_by(macaddress=mac_address).first()
            is_active = device['reachabilityHealth'] == "UP"
            
            if not ap:
                ap = AccessPoint(
                    apname=ap_name,
                    macaddress=mac_address,
                    ipaddress=device.get('ipAddress'),
                    modelname=device.get('model'),
                    isactive=is_active,
                    floorid=floor.floorid,
                    building_id=building.building_id
                )
                session.add(ap)
                session.flush()
            else:
                ap.isactive = is_active
                ap.floorid = floor.floorid
                ap.building_id = building.building_id

            # ClientCountAP: Create records for each radio type
            client_counts = device.get('clientCount', {})
            for radio_name, count in client_counts.items():
                radio_id = radio_types.get(radio_name)
                if radio_id is None:
                    logger.warning(f"Unexpected radio key: {radio_name}")
                    continue

                # Check for existing record
                cc = session.query(ClientCountAP).filter_by(
                    apid=ap.apid,
                    radioid=radio_id,
                    timestamp=timestamp
                ).first()

                if cc:
                    cc.clientcount = count
                else:
                    cc = ClientCountAP(
                        apid=ap.apid,
                        radioid=radio_id,
                        clientcount=count,
                        timestamp=timestamp
                    )
                    session.add(cc)

        session.commit()
        logger.info(f"Inserted/updated AP and client count data in apclientcount DB for {len(device_info_list)} devices.")
    except Exception as e:
        session.rollback()
        logger.error(f"Error inserting data into apclientcount DB: {e}")
        raise
    finally:
        if close_session:
            session.close()

@app.get("/aps", response_model=List[dict], tags=["Access Points"])
def get_aps(db: Session = Depends(get_wireless_db)):
    """Get all access points from the database."""
    try:
        logger.info("Fetching AP data from the database")
        aps = db.query(AccessPoint).all()
        logger.info(f"Retrieved {len(aps)} AP records")
        
        return [{
            "apid": ap.apid,
            "apname": ap.apname,
            "macaddress": str(ap.macaddress),
            "ipaddress": str(ap.ipaddress) if ap.ipaddress else None,
            "modelname": ap.modelname,
            "isactive": ap.isactive,
            "buildingid": ap.buildingid,
            "floorid": ap.floorid,
            "roomid": ap.roomid
        } for ap in aps]
    except SQLAlchemyError as e:
        logger.error(f"Database error in /aps: {e}")
        raise HTTPException(status_code=500, detail="Database error")
    except Exception as e:
        logger.error(f"Unexpected error in /aps: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/client-counts", response_model=List[dict], tags=["Client Counts"])
def get_client_counts(
    ap_id: Optional[int] = None,
    radio_id: Optional[int] = None,
    limit: int = 100,
    db: Session = Depends(get_apclient_db)
):
    """Get AP client count data from the apclientcount database."""
    try:
        query = db.query(ClientCountAP).join(AccessPoint, ClientCountAP.apid == AccessPoint.apid).join(RadioType, ClientCountAP.radioid == RadioType.radioid)
        if ap_id:
            query = query.filter(ClientCountAP.apid == ap_id)
        if radio_id:
            query = query.filter(ClientCountAP.radioid == radio_id)
        query = query.order_by(ClientCountAP.timestamp.desc()).limit(limit)
        results = query.all()
        return [{
            "clientcount": cc.clientcount,
            "apname": cc.accesspoint.apname if cc.accesspoint else None,
            "radioname": cc.radio.radioname if cc.radio else None,
            "apid": cc.apid,
            "radioid": cc.radioid,
            "timestamp": cc.timestamp.isoformat() if cc.timestamp else None
        } for cc in results]
    except SQLAlchemyError as e:
        logger.error(f"Database error in /client-counts: {e}")
        raise HTTPException(status_code=500, detail="Database error")
    except Exception as e:
        logger.error(f"Unexpected error in /client-counts: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.get("/buildings", response_model=List[dict], tags=["Buildings"])
def get_buildings(db: Session = Depends(get_wireless_db)):
    """Get list of buildings with their details."""
    try:
        logger.info("Fetching list of buildings")
        buildings = db.query(Building).all()
        return [{
            "building_id": b.building_id,
            "building_name": b.building_name,
            # Add more fields as needed
        } for b in buildings]
    except SQLAlchemyError as e:
        logger.error(f"Database error in /buildings: {e}")
        raise HTTPException(status_code=500, detail="Database error")
    except Exception as e:
        logger.error(f"Unexpected error in /buildings: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/floors/{building_id}", response_model=List[dict], tags=["Floors"])
def get_floors(building_id: int, db: Session = Depends(get_wireless_db)):
    """Get floors for a specific building."""
    try:
        floors = db.query(Floor).filter_by(buildingid=building_id).all()
        return [{
            "floorid": f.floorid,
            "floorname": f.floorname,
            "room_count": len(f.rooms),
            "ap_count": len(f.accesspoints)
        } for f in floors]
    except SQLAlchemyError as e:
        logger.error(f"Database error in /floors: {e}")
        raise HTTPException(status_code=500, detail="Database error")
    except Exception as e:
        logger.error(f"Unexpected error in /floors: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/rooms/{floor_id}", response_model=List[dict], tags=["Rooms"])
def get_rooms(floor_id: int, db: Session = Depends(get_wireless_db)):
    """Get rooms for a specific floor."""
    try:
        rooms = db.query(Room).filter_by(floorid=floor_id).all()
        return [{
            "roomid": r.roomid,
            "roomname": r.roomname,
            "ap_count": len(r.accesspoints)
        } for r in rooms]
    except SQLAlchemyError as e:
        logger.error(f"Database error in /rooms: {e}")
        raise HTTPException(status_code=500, detail="Database error")
    except Exception as e:
        logger.error(f"Unexpected error in /rooms: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/radio-types", response_model=List[dict], tags=["Radio Types"])
def get_radio_types(db: Session = Depends(get_wireless_db)):
    """Get all radio types."""
    try:
        radio_types = db.query(RadioType).all()
        return [{
            "radioid": rt.radioid,
            "radioname": rt.radioname
        } for rt in radio_types]
    except SQLAlchemyError as e:
        logger.error(f"Database error in /radio-types: {e}")
        raise HTTPException(status_code=500, detail="Database error")
    except Exception as e:
        logger.error(f"Unexpected error in /radio-types: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/wireless/campuses/", response_model=CampusResponse)
def create_campus(campus: CampusCreate, db: Session = Depends(get_wireless_db)):
    """Create a new campus."""
    db_campus = Campus(campus_name=campus.campus_name)
    db.add(db_campus)
    db.commit()
    db.refresh(db_campus)
    return db_campus

@app.get("/wireless/campuses/", response_model=List[CampusResponse])
def get_campuses(db: Session = Depends(get_wireless_db)):
    """Get all campuses."""
    return db.query(Campus).all()

@app.post("/wireless/buildings/", response_model=BuildingResponse)
def create_building(building: BuildingCreate, db: Session = Depends(get_wireless_db)):
    """Create a new building."""
    db_building = Building(**building.dict())
    db.add(db_building)
    db.commit()
    db.refresh(db_building)
    return db_building

@app.get("/wireless/buildings/", response_model=List[BuildingResponse])
def get_wireless_buildings(campus_id: Optional[int] = None, db: Session = Depends(get_wireless_db)):
    """Get all buildings, optionally filtered by campus."""
    query = db.query(Building)
    if campus_id:
        query = query.filter(Building.campus_id == campus_id)
    return query.all()

@app.post("/wireless/client-counts/", response_model=ClientCountResponse)
def create_client_count(count: ClientCountCreate, db: Session = Depends(get_wireless_db)):
    """Create a new client count."""
    db_count = ClientCount(**count.dict())
    db.add(db_count)
    db.commit()
    db.refresh(db_count)
    return db_count

@app.get("/wireless/client-counts/", response_model=List[ClientCountResponse])
def get_wireless_client_counts(
    building_id: Optional[int] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    db: Session = Depends(get_wireless_db)
):
    """Get client counts with optional filters."""
    query = db.query(ClientCount)
    if building_id:
        query = query.filter(ClientCount.building_id == building_id)
    if start_time:
        query = query.filter(ClientCount.time_inserted >= start_time)
    if end_time:
        query = query.filter(ClientCount.time_inserted <= end_time)
    return query.all()

@app.post("/ap/buildings/", response_model=ApBuildingResponse)
def create_ap_building(building: ApBuildingCreate, db: Session = Depends(get_apclient_db)):
    """Create a new AP building."""
    db_building = ApBuilding(**building.dict())
    db.add(db_building)
    db.commit()
    db.refresh(db_building)
    return db_building

@app.get("/ap/buildings/", response_model=List[ApBuildingResponse])
def get_ap_buildings(db: Session = Depends(get_apclient_db)):
    """Get all AP buildings."""
    return db.query(ApBuilding).all()

@app.post("/ap/floors/", response_model=FloorResponse)
def create_floor(floor: FloorCreate, db: Session = Depends(get_apclient_db)):
    """Create a new floor."""
    db_floor = Floor(**floor.dict())
    db.add(db_floor)
    db.commit()
    db.refresh(db_floor)
    return db_floor

@app.get("/ap/floors/", response_model=List[FloorResponse])
def get_ap_floors(building_id: Optional[int] = None, db: Session = Depends(get_apclient_db)):
    """Get all floors, optionally filtered by building."""
    query = db.query(Floor)
    if building_id:
        query = query.filter(Floor.buildingid == building_id)
    return query.all()

@app.post("/ap/rooms/", response_model=RoomResponse)
def create_room(room: RoomCreate, db: Session = Depends(get_apclient_db)):
    """Create a new room."""
    db_room = Room(**room.dict())
    db.add(db_room)
    db.commit()
    db.refresh(db_room)
    return db_room

@app.get("/ap/rooms/", response_model=List[RoomResponse])
def get_ap_rooms(floor_id: Optional[int] = None, db: Session = Depends(get_apclient_db)):
    """Get all rooms, optionally filtered by floor."""
    query = db.query(Room)
    if floor_id:
        query = query.filter(Room.floorid == floor_id)
    return query.all()

@app.post("/ap/access-points/", response_model=AccessPointResponse)
def create_access_point(ap: AccessPointCreate, db: Session = Depends(get_apclient_db)):
    """Create a new access point."""
    db_ap = AccessPoint(**ap.dict())
    db.add(db_ap)
    db.commit()
    db.refresh(db_ap)
    return db_ap

@app.get("/ap/access-points/", response_model=List[AccessPointResponse])
def get_ap_access_points(
    building_id: Optional[int] = None,
    floor_id: Optional[int] = None,
    room_id: Optional[int] = None,
    db: Session = Depends(get_apclient_db)
):
    """Get all access points with optional filters."""
    query = db.query(AccessPoint)
    if building_id:
        query = query.filter(AccessPoint.buildingid == building_id)
    if floor_id:
        query = query.filter(AccessPoint.floorid == floor_id)
    if room_id:
        query = query.filter(AccessPoint.roomid == room_id)
    return query.all()

@app.post("/ap/radio-types/", response_model=RadioTypeResponse)
def create_radio_type(radio: RadioTypeCreate, db: Session = Depends(get_apclient_db)):
    """Create a new radio type."""
    db_radio = RadioType(**radio.dict())
    db.add(db_radio)
    db.commit()
    db.refresh(db_radio)
    return db_radio

@app.get("/ap/radio-types/", response_model=List[RadioTypeResponse])
def get_ap_radio_types(db: Session = Depends(get_apclient_db)):
    """Get all radio types."""
    return db.query(RadioType).all()

@app.post("/ap/client-counts/", response_model=ClientCountAPResponse)
def create_client_count_ap(count: ClientCountAPCreate, db: Session = Depends(get_apclient_db)):
    """Create a new AP client count."""
    db_count = ClientCountAP(**count.dict())
    db.add(db_count)
    db.commit()
    db.refresh(db_count)
    return db_count

@app.get("/ap/client-counts/", response_model=List[ClientCountAPResponse])
def get_ap_client_counts(
    ap_id: Optional[int] = None,
    radio_id: Optional[int] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    db: Session = Depends(get_apclient_db)
):
    """Get AP client counts with optional filters."""
    query = db.query(ClientCountAP)
    if ap_id:
        query = query.filter(ClientCountAP.apid == ap_id)
    if radio_id:
        query = query.filter(ClientCountAP.radioid == radio_id)
    if start_time:
        query = query.filter(ClientCountAP.timestamp >= start_time)
    if end_time:
        query = query.filter(ClientCountAP.timestamp <= end_time)
    return query.all()

@app.post("/tasks/update-client-count/", response_model=dict)
def trigger_update_client_count():
    """Trigger the client count update task."""
    try:
        update_client_count_task()
        return {"message": "Client count update task started"}
    except Exception as e:
        logger.error(f"Error triggering client count update task: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tasks/update-ap-data/", response_model=dict)
def trigger_update_ap_data():
    """Trigger the AP data update task."""
    try:
        update_ap_data_task()
        return {"message": "AP data update task started"}
    except Exception as e:
        logger.error(f"Error triggering AP data update task: {e}")
        raise HTTPException(status_code=500, detail=str(e))
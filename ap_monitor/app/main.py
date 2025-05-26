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
from sqlalchemy.orm import sessionmaker
import os

from ap_monitor.app.db import get_db, init_db
from ap_monitor.app.models import AccessPoint, ClientCount, Building, Floor, Campus, ApBuilding, Room, RadioType, ClientCountAP
from ap_monitor.app.dna_api import AuthManager, fetch_client_counts, fetch_ap_data, radio_id_map
from ap_monitor.app.utils import setup_logging, calculate_next_run_time

from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import scoped_session

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

# Initialize database engine and session factory
engine = None
TestingSessionLocal = None

def initialize_database():
    global engine, TestingSessionLocal
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Reinitialize the database engine and session factory
initialize_database()

# Set up logging
logger = setup_logging()

# Log the database URL being used
logger.info(f"Using DATABASE_URL: {DATABASE_URL}")

# Initialize scheduler
scheduler = BackgroundScheduler()

# Create auth manager for DNA Center API
auth_manager = AuthManager()

APCLIENT_DB_URL = os.getenv("APCLIENT_DB_URL", "postgresql://postgres:@localhost:3306/apclientcount")
apclient_engine = create_engine(APCLIENT_DB_URL)
ApclientSessionLocal = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=apclient_engine))

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handles app startup and shutdown using the FastAPI lifespan context."""
    try:
        # === STARTUP ===
        logger.info("App starting up...")

        # Initialize database
        logger.info("Initializing database...")
        init_db()
        logger.info("Database initialized successfully")

        # Initialize radio table if empty
        with next(get_db()) as db:
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

def update_ap_data_task(db: Session = None):
    """Background task to update AP data in the database."""
    close_db = False
    if db is None:
        db = next(get_db())
        close_db = True
    try:
        logger.info("Running scheduled task: update_ap_data_task")
        logger.debug(f"Database session being used: {db}")
        now = datetime.now(timezone.utc)
        rounded_unix_timestamp = int(now.timestamp() * 1000)
        aps = fetch_ap_data(auth_manager, rounded_unix_timestamp)
        logger.info(f"Fetched {len(aps)} APs from DNAC API")
        
        for ap in aps:
            # Try to get location from multiple fields
            location = ap.get('location')
            if not location or len(location.split('/')) < 5:
                # Fallback to snmpLocation if available and not default
                snmp_location = ap.get('snmpLocation')
                if snmp_location and snmp_location.lower() != 'default location' and snmp_location.strip():
                    location = snmp_location
                else:
                    # Fallback to locationName if available and not null
                    location_name = ap.get('locationName')
                    if location_name and location_name.strip().lower() != 'null':
                        location = location_name
            location_parts = location.split('/') if location else []
            if len(location_parts) < 2:
                logger.warning(f"Skipping device {ap.get('name')} due to missing or invalid location fields. location: {location}")
                continue
            # Use last two parts as building and floor if possible
            building_name = location_parts[-2] if len(location_parts) >= 2 else 'Unknown'
            floor_name = location_parts[-1] if len(location_parts) >= 1 else 'Unknown'
            logger.debug(f"Processing AP: {ap.get('name')} in Building: {building_name}, Floor: {floor_name}")
            
            # Find or create building
            building = db.query(ApBuilding).filter_by(buildingname=building_name).first()
            if not building:
                logger.debug(f"Creating new building: {building_name}")
                building = ApBuilding(buildingname=building_name)
                db.add(building)
                db.flush()
            
            # Find or create floor
            floor = db.query(Floor).filter_by(floorname=floor_name, buildingid=building.buildingid).first()
            if not floor:
                logger.debug(f"Creating new floor: {floor_name} for Building: {building_name}")
                floor = Floor(floorname=floor_name, buildingid=building.buildingid)
                db.add(floor)
                db.flush()
            
            # Find or create access point
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
                    buildingid=building.buildingid
                )
                db.add(ap_record)
                db.flush()
            else:
                logger.debug(f"Updating existing AccessPoint: {ap.get('name')}")
                ap_record.isactive = is_active
                ap_record.floorid = floor.floorid
                ap_record.buildingid = building.buildingid
            
            # Create client count records for each radio
            for radio_name, count in ap.get('clientCount', {}).items():
                radio = db.query(RadioType).filter_by(radioname=radio_name).first()
                if not radio:
                    logger.warning(f"Unexpected radio key: {radio_name}")
                    continue
                
                client_count = ClientCountAP(
                    apid=ap_record.apid,
                    radioid=radio.radioid,
                    clientcount=count,
                    timestamp=now
                )
                db.add(client_count)
        
        db.commit()
        logger.info("AP data updated successfully")
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

def update_client_count_task(db: Session = None):
    """Background task to update client count data in the database."""
    close_db = False
    if db is None:
        db = next(get_db())
        close_db = True
    try:
        logger.info("Running scheduled task: update_client_count_task")
        now = datetime.now(timezone.utc)
        rounded_unix_timestamp = int(now.timestamp() * 1000)
        
        # Fetch data from DNA Center API
        ap_data = fetch_ap_data(auth_manager, rounded_unix_timestamp)
        site_data = fetch_client_counts(auth_manager, rounded_unix_timestamp)
        
        # Process site-level data
        for site in site_data:
            building_name = site.get("parentSiteName")
            floor_name = site.get("siteName")
            client_counts = site.get("clientCount", {})
            
            # Find or create building
            building = db.query(ApBuilding).filter_by(buildingname=building_name).first()
            if not building:
                building = ApBuilding(buildingname=building_name)
                db.add(building)
                db.flush()
            
            # Find or create floor
            floor = db.query(Floor).filter_by(floorname=floor_name, buildingid=building.buildingid).first()
            if not floor:
                floor = Floor(floorname=floor_name, buildingid=building.buildingid)
                db.add(floor)
                db.flush()
            
            # Process client counts for each radio
            for radio_name, count in client_counts.items():
                radio = db.query(RadioType).filter_by(radioname=radio_name).first()
                if not radio:
                    radio = RadioType(radioname=radio_name)
                    db.add(radio)
                    db.flush()
                
                # Create client count record
                client_count = ClientCountAP(
                    apid=None,  # Site-level data has no AP ID
                    radioid=radio.radioid,
                    clientcount=count,
                    timestamp=now
                )
                db.add(client_count)
        
        db.commit()
        logger.info("Client count data updated successfully")
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
        session = ApclientSessionLocal()
        close_session = True
    try:
        radioId_map = {r.radioname: r.radioid for r in session.query(RadioType).all()}
        for device in device_info_list:
            ap_name = device['name']
            location = device.get('location', '')
            location_parts = location.split('/')
            if len(location_parts) < 5:
                logger.warning(f"Skipping device {ap_name} due to invalid location format: {location}")
                continue
            building_name = location_parts[3]
            floor_name = location_parts[4]
            # Building
            building = session.query(ApBuilding).filter_by(buildingname=building_name).first()
            if not building:
                building = ApBuilding(buildingname=building_name)
                session.add(building)
                session.flush()
            # Floor
            floor = session.query(Floor).filter_by(floorname=floor_name, buildingid=building.buildingid).first()
            if not floor:
                floor = Floor(floorname=floor_name, buildingid=building.buildingid)
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
                    buildingid=building.buildingid
                )
                session.add(ap)
                session.flush()
            else:
                ap.isactive = is_active
            # ClientCountAP
            for radio, count in device.get('clientCount', {}).items():
                radio_id = radioId_map.get(radio)
                if radio_id is None:
                    logger.warning(f"Unexpected radio key: {radio}")
                    continue
                
                # Check for existing client count record
                existing_cc = session.query(ClientCountAP).filter_by(
                    apid=ap.apid,
                    radioid=radio_id
                ).first()
                
                if existing_cc:
                    # Update existing record
                    existing_cc.clientcount = count
                    existing_cc.timestamp = timestamp
                else:
                    # Create new record
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
    finally:
        if close_session:
            session.close()

@app.get("/aps", response_model=List[dict], tags=["Access Points"])
def get_aps(db: Session = Depends(get_db)):
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
    building: Optional[str] = None,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """Get client count data from the database."""
    try:
        query = db.query(ClientCountAP)

        if building:
            query = query.join(AccessPoint).join(ApBuilding).filter(ApBuilding.buildingname == building)

        query = query.order_by(ClientCountAP.timestamp.desc()).limit(limit)
        results = query.all()

        return [{
            "countid": cc.countid,
            "apid": cc.apid,
            "radioid": cc.radioid,
            "clientcount": cc.clientcount,
            "timestamp": cc.timestamp,
            "apname": cc.accesspoint.apname if cc.accesspoint else None,
            "radioname": cc.radio.radioname if cc.radio else None
        } for cc in results]
    except SQLAlchemyError as e:
        logger.error(f"Database error in /client-counts: {e}")
        raise HTTPException(status_code=500, detail="Database error")
    except Exception as e:
        logger.error(f"Unexpected error in /client-counts: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.get("/buildings", response_model=List[dict], tags=["Buildings"])
def get_buildings(db: Session = Depends(get_db)):
    """Get list of buildings with their details."""
    try:
        logger.info("Fetching list of buildings")
        buildings = db.query(ApBuilding).all()
        return [{
            "buildingid": b.buildingid,
            "buildingname": b.buildingname,
            "floor_count": len(b.floors),
            "ap_count": sum(len(floor.accesspoints) for floor in b.floors)
        } for b in buildings]
    except SQLAlchemyError as e:
        logger.error(f"Database error in /buildings: {e}")
        raise HTTPException(status_code=500, detail="Database error")
    except Exception as e:
        logger.error(f"Unexpected error in /buildings: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/floors/{building_id}", response_model=List[dict], tags=["Floors"])
def get_floors(building_id: int, db: Session = Depends(get_db)):
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
def get_rooms(floor_id: int, db: Session = Depends(get_db)):
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
def get_radio_types(db: Session = Depends(get_db)):
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
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple
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
from zoneinfo import ZoneInfo  # Python 3.9+

from ap_monitor.app.db import (
    get_wireless_db,
    get_apclient_db,
    get_wireless_db_session,
    get_apclient_db_session,
    init_db,
    WirelessBase,
    APClientBase
)
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
from .diagnostics import (
    analyze_zero_count_buildings,
    monitor_building_health,
    generate_diagnostic_report,
    is_diagnostics_enabled
)

TORONTO_TZ = ZoneInfo("America/Toronto")

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
scheduler = BackgroundScheduler(
    job_defaults={
        'coalesce': True,  # Only run once if multiple executions are missed
        'max_instances': 1,  # Only one instance of a job can run at a time
        'misfire_grace_time': 60  # Allow jobs to run up to 60 seconds late
    },
    timezone=TORONTO_TZ
)

# Create auth manager for DNA Center API
auth_manager = AuthManager()

def cleanup_job(job_id, scheduler_obj=None):
    """Clean up a job and its instances."""
    scheduler_obj = scheduler_obj or scheduler
    try:
        if scheduler_obj.get_job(job_id):
            scheduler_obj.remove_job(job_id)
            logger.info(f"Removed job {job_id}")
    except Exception as e:
        logger.error(f"Error cleaning up job {job_id}: {e}")

def reschedule_job(job_id, func, next_run, scheduler_obj=None):
    """Reschedule a job with proper cleanup."""
    scheduler_obj = scheduler_obj or scheduler
    try:
        cleanup_job(job_id, scheduler_obj)
        job_name = f"{getattr(func, '__name__', repr(func))} Task"
        scheduler_obj.add_job(
            func=func,
            trigger=DateTrigger(run_date=next_run),
            id=job_id,
            name=job_name,
            replace_existing=True,
        )
        logger.info(f"Next {job_id} scheduled at {next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    except Exception as e:
        logger.error(f"Error rescheduling job {job_id}: {e}")

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
        with get_wireless_db() as db:
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
        logger.info(f"First scheduled run at: {next_run.strftime('%Y-%m-%d %H:%M:%S %Z')} (Server time: {datetime.now(TORONTO_TZ).strftime('%Y-%m-%d %H:%M:%S %Z')})")

        # Clean up any existing jobs
        cleanup_job("update_ap_data_task")
        cleanup_job("update_client_count_task")

        # Add new jobs
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

    except Exception as e:
        logger.error(f"Error during startup: {e}")
        raise
    finally:
        # === SHUTDOWN ===
        logger.info("App shutting down...")
        try:
            if scheduler.running:
                logger.info("Shutting down scheduler...")
                scheduler.shutdown()
                logger.info("Scheduler shut down successfully")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")

# Create FastAPI application with lifespan handler
app = FastAPI(
    title="AP Monitor",
    description="API for monitoring wireless access points and client counts",
    version="1.0.0",
    lifespan=lifespan,
)

def parse_location(location: str) -> tuple:
    """
    Parse location string to extract building and floor names.
    Returns (building_name, floor_name) tuple.
    """
    invalid_set = {None, '', 'invalid', 'none', 'unknown'}
    if not location or not isinstance(location, str):
        logger.warning(f"Skipping device with empty or invalid location: {location}")
        return None, None

    # Reject locations with leading or trailing slashes
    if location.startswith('/') or location.endswith('/'):
        logger.warning(f"Skipping device with leading or trailing slash in location: {location}")
        return None, None

    # Remove leading/trailing slashes and split
    location = location.strip('/')
    parts = [p.strip() for p in location.split('/') if p.strip()]
    
    # Validate minimum required parts
    if len(parts) < 2:
        logger.warning(f"Skipping device with insufficient location parts: {location}")
        return None, None

    # Global/Keele Campus/<Building>/<Floor...>
    if len(parts) >= 2 and parts[0] == "Global" and parts[1] == "Keele Campus":
        if len(parts) < 4:
            logger.warning(f"Skipping device with invalid Global/Keele Campus location format: {location}")
            return None, None
        building = parts[2]
        floor = parts[3]
    # <Building>/<Floor...> (only if not Global/Keele Campus)
    elif len(parts) >= 2:
        building = parts[0]
        floor = parts[1]
    else:
        logger.warning(f"Skipping device with invalid location format: {location}")
        return None, None

    # Validate building and floor names
    if not building or str(building).strip().lower() in invalid_set:
        logger.warning(f"Skipping device with invalid building name: {building}")
        return None, None
    if not floor or str(floor).strip().lower() in invalid_set:
        logger.warning(f"Skipping device with invalid floor name: {floor}")
        return None, None

    # Additional validation for specific cases
    if building.lower() == 'invalid' or floor.lower() == 'invalid':
        logger.warning(f"Skipping device with explicitly invalid building/floor: {location}")
        return None, None

    # Validate that building and floor are not empty strings after stripping
    if not building.strip() or not floor.strip():
        logger.warning(f"Skipping device with empty building or floor after stripping: {location}")
        return None, None

    return building, floor

def update_ap_data_task(db: Session = None, auth_manager_obj=None, fetch_ap_data_func=None):
    """Background task to update AP data in the database."""
    auth_manager_obj = auth_manager_obj or auth_manager
    fetch_ap_data_func = fetch_ap_data_func or fetch_ap_data
    close_db = False
    if db is None:
        db = get_apclient_db_session()  # Use session function instead of context manager
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
            
            # Try different location fields in order of preference
            location = ap.get('location')
            if not location or len(location.split('/')) < 2:
                location = ap.get('snmpLocation')
            if not location or len(location.split('/')) < 2:
                location = ap.get('locationName')
            
            building_name, floor_name = parse_location(location)
            if not building_name or not floor_name:
                continue
            
            # Building
            building = db.query(ApBuilding).filter_by(buildingname=building_name).first()
            if not building:
                building = ApBuilding(buildingname=building_name)
                db.add(building)
                db.flush()
            
            # Floor
            floor = db.query(Floor).filter_by(floorname=floor_name, buildingid=building.buildingid).first()
            if not floor:
                floor = Floor(floorname=floor_name, buildingid=building.buildingid)
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
        raise  # Re-raise to trigger scheduler's error handling
    finally:
        if close_db:
            db.close()
        next_run = calculate_next_run_time()
        reschedule_job("update_ap_data_task", update_ap_data_task, next_run)

def update_client_count_task(db: Session = None, auth_manager_obj=None, fetch_client_counts_func=None, fetch_ap_data_func=None, wireless_db=None):
    """Update client count data from DNA Center API."""
    close_db = False
    close_wireless_db = False
    
    try:
        # Get database sessions if not provided
        if db is None:
            db = get_apclient_db_session()
            close_db = True
        if wireless_db is None:
            wireless_db = get_wireless_db_session()
            close_wireless_db = True
        
        # Get current time for timestamps
        now = datetime.now(timezone.utc)
        rounded_unix_timestamp = int(now.timestamp())
        
        # Use provided auth_manager or default
        auth_manager_obj = auth_manager_obj or auth_manager
        
        # Dual-path logic for fetching AP/client count data
        if fetch_client_counts_func:
            ap_data = fetch_client_counts_func(auth_manager_obj, rounded_unix_timestamp)
        elif fetch_ap_data_func:
            ap_data = fetch_ap_data_func(auth_manager_obj, rounded_unix_timestamp)
        else:
            ap_data = fetch_client_counts(auth_manager_obj, rounded_unix_timestamp)
        
        # Track building totals
        building_totals = {}
        
        # Get all buildings from wireless_count DB
        wireless_buildings = {b.building_name.lower(): b for b in wireless_db.query(Building).all()}
        
        # Process AP data for apclientcount DB
        for ap in ap_data:
            ap_name = ap.get('name')
            
            # Try different location fields in order of preference
            location = ap.get('location')
            if not location or len(location.split('/')) < 2:
                location = ap.get('snmpLocation')
            if not location or len(location.split('/')) < 2:
                location = ap.get('locationName')
            
            building_name, floor_name = parse_location(location)
            if not building_name or not floor_name:
                continue
            
            # Building - Use apclient_db (db) for ApBuilding model
            building = db.query(ApBuilding).filter_by(buildingname=building_name).first()
            if not building:
                building = ApBuilding(buildingname=building_name)
                db.add(building)
                db.flush()
            
            # Floor - Use apclient_db (db) for Floor model
            floor = db.query(Floor).filter_by(floorname=floor_name, buildingid=building.buildingid).first()
            if not floor:
                floor = Floor(floorname=floor_name, buildingid=building.buildingid)
                db.add(floor)
                db.flush()
            
            # Access Point - Use apclient_db (db) for AccessPoint model
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

            # Create client count records for each radio and track building totals
            client_counts = ap.get('clientCount', {})
            building_total = 0
            for radio_name, count in client_counts.items():
                # Use apclient_db (db) for RadioType model
                radio = db.query(RadioType).filter_by(radioname=radio_name).first()
                if not radio:
                    logger.warning(f"Unexpected radio key: {radio_name}")
                    continue

                # Check for existing record - Use apclient_db (db) for ClientCountAP model
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
                building_total += count
            
            # Add to building totals
            if building_name not in building_totals:
                building_totals[building_name] = 0
            building_totals[building_name] += building_total
        
        # Update wireless_count DB with building totals
        for building_name, total_clients in building_totals.items():
            # Find building in wireless_count DB using case-insensitive comparison
            building = wireless_buildings.get(building_name.lower())
            if building:
                # Create new client count record - Use wireless_db for ClientCount model
                client_count = ClientCount(
                    building_id=building.building_id,
                    client_count=total_clients,
                    time_inserted=now
                )
                wireless_db.add(client_count)
            else:
                logger.warning(f"Building {building_name} not found in wireless_count database")
        
        # Handle buildings with no APs or zero counts
        for building_name, building in wireless_buildings.items():
            if building_name.lower() not in {k.lower() for k in building_totals}:
                # Create zero count record for buildings with no APs - Use wireless_db for ClientCount model
                client_count = ClientCount(
                    building_id=building.building_id,
                    client_count=0,
                    time_inserted=now
                )
                wireless_db.add(client_count)
                logger.info(f"Created zero count record for building {building_name}")
        
        # Commit both databases
        db.commit()
        wireless_db.commit()
        logger.info("Client count data updated successfully in both databases")

    except Exception as e:
        if db:
            db.rollback()
        if wireless_db:
            wireless_db.rollback()
        logger.error(f"Error updating client count data: {str(e)}")
        raise  # Re-raise to trigger scheduler's error handling
    finally:
        if close_db and db:
            db.close()
        if close_wireless_db and wireless_db:
            wireless_db.close()
        next_run = calculate_next_run_time()
        reschedule_job("update_client_count_task", update_client_count_task, next_run)

def insert_apclientcount_data(device_info_list, timestamp, session=None):
    """Insert AP client count data into the database."""
    if session is None:
        session = next(get_apclient_db())
    
    try:
        for device_info in device_info_list:
            ap_name = device_info.get('name')
            
            # Try different location fields in order of preference
            location = device_info.get('location')
            if not location or len(location.split('/')) < 2:
                location = device_info.get('snmpLocation')
            if not location or len(location.split('/')) < 2:
                location = device_info.get('locationName')
            
            # Parse and validate location
            building_name, floor_name = parse_location(location)
            if building_name is None or floor_name is None:
                logger.warning(f"Skipping device with invalid location: {location}")
                continue
            
            # Additional validation before proceeding
            if not building_name.strip() or not floor_name.strip():
                logger.warning(f"Skipping device with empty building or floor after validation: {location}")
                continue
            
            # Get or create building
            building = session.query(ApBuilding).filter_by(buildingname=building_name).first()
            if not building:
                building = ApBuilding(buildingname=building_name)
                session.add(building)
                session.flush()
            
            # Get or create floor
            floor = session.query(Floor).filter_by(buildingid=building.buildingid, floorname=floor_name).first()
            if not floor:
                floor = Floor(buildingid=building.buildingid, floorname=floor_name)
                session.add(floor)
                session.flush()
            
            # Get or create room (optional)
            room_name = "Unknown Room"  # Default room name
            if location and len(location.split('/')) > 4:
                room_name = location.split('/')[4].strip()
            
            room = session.query(Room).filter_by(floorid=floor.floorid, roomname=room_name).first()
            if not room:
                room = Room(floorid=floor.floorid, roomname=room_name)
                session.add(room)
                session.flush()
            
            # Get or create access point
            mac_address = device_info["macAddress"]
            ap = session.query(AccessPoint).filter_by(macaddress=mac_address).first()
            
            if not ap:
                logger.debug(f"Creating new AccessPoint: {ap_name} with MAC: {mac_address}")
                ap = AccessPoint(
                    buildingid=building.buildingid,
                    floorid=floor.floorid,
                    roomid=room.roomid,
                    apname=ap_name,
                    macaddress=mac_address,
                    ipaddress=device_info["ipAddress"],
                    modelname=device_info["model"],
                    isactive=device_info["reachabilityHealth"] == "UP"
                )
                session.add(ap)
                session.flush()
            else:
                logger.debug(f"Updating existing AccessPoint: {ap_name}")
                ap.apname = ap_name
                ap.ipaddress = device_info["ipAddress"]
                ap.modelname = device_info["model"]
                ap.isactive = device_info["reachabilityHealth"] == "UP"
                ap.buildingid = building.buildingid
                ap.floorid = floor.floorid
                ap.roomid = room.roomid
                session.flush()
            
            # Update client counts
            client_counts = device_info.get("clientCount", {})
            for radio_name, count in client_counts.items():
                radio = session.query(RadioType).filter_by(radioname=radio_name).first()
                if not radio:
                    logger.warning(f"Skipping unexpected radio key: {radio_name}")
                    continue
                
                client_count = session.query(ClientCountAP).filter_by(
                    apid=ap.apid,
                    radioid=radio.radioid,
                    timestamp=timestamp
                ).first()
                
                if client_count:
                    client_count.clientcount = count
                else:
                    client_count = ClientCountAP(
                        apid=ap.apid,
                        radioid=radio.radioid,
                        clientcount=count,
                        timestamp=timestamp
                    )
                    session.add(client_count)
        
        session.commit()
        logger.info("AP data updated successfully in apclientcount DB")
            
    except Exception as e:
        logger.error(f"Error updating client count data: {str(e)}")
        session.rollback()
        raise

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

# Add health check endpoint
@app.get("/health")
def health_check():
    """Check the health of the application and scheduler."""
    try:
        scheduler_status = {
            "running": scheduler.running,
            "jobs": [
                {
                    "id": job.id,
                    "name": job.name,
                    "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                    "state": "running" if job.next_run_time and job.next_run_time > datetime.now(timezone.utc) else "idle"
                }
                for job in scheduler.get_jobs()
            ]
        }
        return {
            "status": "healthy",
            "scheduler": scheduler_status,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

def calculate_next_run_time():
    """Calculate the next run time rounded up to the next 5-minute mark (Toronto local time, offset-aware)."""
    now = datetime.now(TORONTO_TZ)
    minute = (now.minute // 5 + 1) * 5
    if minute >= 60:
        next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        return next_hour
    else:
        return now.replace(minute=minute, second=0, microsecond=0)

@app.get("/diagnostics/zero-counts")
async def get_zero_count_diagnostics():
    """
    Get detailed analysis of buildings with zero client counts.
    Only works when ENABLE_DIAGNOSTICS=true.
    """
    try:
        with get_wireless_db() as wireless_db, get_apclient_db() as apclient_db:
            report = analyze_zero_count_buildings(wireless_db, apclient_db, auth_manager)
            if "message" in report and report["message"] == "Diagnostics are not enabled":
                raise HTTPException(
                    status_code=403,
                    detail="Diagnostics are not enabled. Set ENABLE_DIAGNOSTICS=true to enable."
                )
            return report
    except Exception as e:
        logger.error(f"Error generating zero count diagnostics: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/diagnostics/health")
async def get_building_health():
    """
    Get health monitoring alerts for buildings.
    Only works when ENABLE_DIAGNOSTICS=true.
    """
    try:
        with get_wireless_db() as wireless_db, get_apclient_db() as apclient_db:
            alerts = monitor_building_health(wireless_db, apclient_db, auth_manager)
            if not is_diagnostics_enabled():
                raise HTTPException(
                    status_code=403,
                    detail="Diagnostics are not enabled. Set ENABLE_DIAGNOSTICS=true to enable."
                )
            return {"alerts": alerts}
    except Exception as e:
        logger.error(f"Error generating health alerts: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/diagnostics/report")
async def get_diagnostic_report():
    """
    Get a comprehensive diagnostic report including zero count analysis and health monitoring.
    Only works when ENABLE_DIAGNOSTICS=true.
    """
    try:
        with get_wireless_db() as wireless_db, get_apclient_db() as apclient_db:
            report = generate_diagnostic_report(wireless_db, apclient_db, auth_manager)
            if "message" in report and report["message"] == "Diagnostics are not enabled":
                raise HTTPException(
                    status_code=403,
                    detail="Diagnostics are not enabled. Set ENABLE_DIAGNOSTICS=true to enable."
                )
            return report
    except Exception as e:
        logger.error(f"Error generating diagnostic report: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
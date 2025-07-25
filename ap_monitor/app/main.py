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
import time
from urllib.error import HTTPError

from ap_monitor.app.db import (
    get_wireless_db,
    get_apclient_db,
    get_wireless_db_session,
    get_apclient_db_session,
    init_db,
    WirelessBase,
    APClientBase,
    get_apclient_db_dep,
    get_wireless_db_dep
)
from ap_monitor.app.models import (
    Campus, Building, ClientCount,
    ApBuilding, Floor, Room, AccessPoint, RadioType, ClientCountAP
)
from ap_monitor.app.dna_api import (
    AuthManager, fetch_client_counts, fetch_ap_data, radio_id_map,
    fetch_ap_client_data_with_fallback
)
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
    is_diagnostics_enabled,
    get_incomplete_diagnostics
)
from ap_monitor.app.mapping import parse_ap_name_for_location, normalize_building_name

# --- API Health Tracking ---
from collections import deque
import threading

API_ERROR_HISTORY = deque(maxlen=100)  # Track last 100 API errors
API_ERROR_LOCK = threading.Lock()

def log_api_error(error_type, message):
    with API_ERROR_LOCK:
        API_ERROR_HISTORY.append({
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'type': error_type,
            'message': str(message)
        })

def get_api_error_summary():
    with API_ERROR_LOCK:
        errors = list(API_ERROR_HISTORY)
    now = datetime.now(timezone.utc)
    one_hour_ago = now - timedelta(hours=1)
    recent_errors = [e for e in errors if datetime.fromisoformat(e['timestamp']) > one_hour_ago]
    return {
        'total_errors_tracked': len(errors),
        'errors_last_hour': len(recent_errors),
        'recent_errors': recent_errors[-10:]  # Show last 10 errors
    }

TORONTO_TZ = ZoneInfo("America/Toronto")

# Set up logging first
logger = setup_logging()

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

# Global maintenance window variable
MAINTENANCE_UNTIL = None

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

# Create FastAPI app with lifespan
app = FastAPI(
    title="AP Monitor API",
    description="API for monitoring wireless Access Points and client counts",
    version="1.0.0",
    lifespan=lifespan
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

def update_ap_data_task(db: Session = None, auth_manager_obj=None, fetch_ap_data_func=None, retries=0):
    """Background task to update AP data in the database, with retry on maintenance errors."""
    from ap_monitor.app.db import get_apclient_db_session
    global MAINTENANCE_UNTIL
    auth_manager_obj = auth_manager_obj or auth_manager
    fetch_ap_data_func = fetch_ap_data_func or fetch_ap_data
    close_db = False
    if db is None:
        db = get_apclient_db_session()
        close_db = True
    try:
        # Check for global maintenance window
        now = datetime.now(timezone.utc)
        if MAINTENANCE_UNTIL and now < MAINTENANCE_UNTIL:
            logger.warning(f"In maintenance window until {MAINTENANCE_UNTIL.isoformat()}, skipping update_ap_data_task.")
            next_run = MAINTENANCE_UNTIL
            reschedule_job("update_ap_data_task", update_ap_data_task, next_run)
            return
        logger.info(f"Running scheduled task: update_ap_data_task (retry {retries})")
        logger.debug(f"Database session being used: {db}")
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
        
    except HTTPError as e:
        if e.code in (404, 500):
            # Set global maintenance window for 1 hour
            MAINTENANCE_UNTIL = datetime.now(timezone.utc) + timedelta(hours=1)
            logger.error(f"Maintenance window or server error detected (HTTP {e.code}). Entering maintenance until {MAINTENANCE_UNTIL.isoformat()}.")
            log_api_error("HTTPError", f"Maintenance window or server error (HTTP {e.code}): {e}")
            next_run = MAINTENANCE_UNTIL
            reschedule_job("update_ap_data_task", update_ap_data_task, next_run)
            return
        if db:
            db.rollback()
        logger.error(f"Error updating AP data: {e}")
        log_api_error("HTTPError", e)
        # Do not re-raise, just log and continue to reschedule
    except Exception as e:
        if db:
            db.rollback()
        logger.error(f"Error updating AP data: {e}")
        log_api_error("Exception", e)
        raise  # Re-raise to trigger scheduler's error handling
    finally:
        if close_db and db:
            db.close()
        # Only reschedule if not in maintenance
        if not (MAINTENANCE_UNTIL and datetime.now(timezone.utc) < MAINTENANCE_UNTIL):
            next_run = calculate_next_run_time()
            reschedule_job("update_ap_data_task", update_ap_data_task, next_run)

def update_client_count_task(db: Session = None, auth_manager_obj=None, fetch_client_counts_func=None, fetch_ap_data_func=None, wireless_db=None, retries=0):
    """Update client count data from DNA Center API, with retry on maintenance errors."""
    global MAINTENANCE_UNTIL
    close_db = False
    close_wireless_db = False
    try:
        # Check for global maintenance window
        now = datetime.now(timezone.utc)
        if MAINTENANCE_UNTIL and now < MAINTENANCE_UNTIL:
            logger.warning(f"In maintenance window until {MAINTENANCE_UNTIL.isoformat()}, skipping update_client_count_task.")
            next_run = MAINTENANCE_UNTIL
            reschedule_job("update_client_count_task", update_client_count_task, next_run)
            return
        # Get database sessions if not provided
        if db is None:
            db = get_apclient_db_session()
            close_db = True
        if wireless_db is None:
            wireless_db = get_wireless_db_session()
            close_wireless_db = True
        rounded_unix_timestamp = int(now.timestamp())
        auth_manager_obj = auth_manager_obj or auth_manager
        # Use new fallback logic for fetching AP/client data
        ap_data_list = fetch_ap_client_data_with_fallback(auth_manager_obj)
        if isinstance(ap_data_list, dict):
            logger.error("fetch_ap_client_data_with_fallback returned a dict (likely API error or rate limit): %r", ap_data_list)
            log_api_error("APIError", ap_data_list)
            return
        if not isinstance(ap_data_list, list):
            logger.error("fetch_ap_client_data_with_fallback did not return a list! Got: %s", type(ap_data_list))
            log_api_error("APIError", f"Type: {type(ap_data_list)} Value: {ap_data_list}")
            return
        if not ap_data_list:
            logger.error("No AP/client data available from any endpoint. Skipping update.")
            log_api_error("APIError", "No AP/client data available from any endpoint.")
            return
        building_totals = {}
        wireless_buildings = {b.building_name.lower(): b for b in wireless_db.query(Building).all()}
        # --- Process data based on required fields ---
        incomplete_aps = []  # Track APs with missing non-critical fields
        for ap in ap_data_list:
            # Required fields for processing
            required_fields = ['macAddress', 'name', 'location', 'clientCount']
            missing_required = [f for f in required_fields if not ap.get(f)]
            if missing_required:
                logger.warning(f"Skipping AP {ap.get('name')} (MAC: {ap.get('macAddress')}) due to missing required fields: {missing_required}")
                continue
            # Check for missing non-critical fields
            non_critical_fields = ['model', 'status', 'ipAddress']
            missing_non_critical = [f for f in non_critical_fields if not ap.get(f)]
            ap_status = ap.get('status', 'ok')
            if missing_non_critical:
                ap_status = 'incomplete'
                logger.info(f"AP {ap.get('name')} (MAC: {ap.get('macAddress')}) is incomplete, missing: {missing_non_critical}")
                incomplete_aps.append({**ap, 'missing_fields': missing_non_critical})
            ap_name = ap.get('name')
            location = ap.get('location')
            building_name, floor_name = parse_location(location)
            if not building_name or not floor_name:
                logger.warning(f"Skipping AP {ap_name} due to invalid location: {location}")
                continue
            # --- Normalize building name to canonical DB name ---
            canonical_building_name = normalize_building_name(building_name)
            if not canonical_building_name:
                logger.warning(f"Skipping AP {ap_name} due to unmapped building name: {building_name}")
                continue
            building = db.query(ApBuilding).filter_by(buildingname=canonical_building_name).first()
            if not building:
                building = ApBuilding(buildingname=canonical_building_name)
                db.add(building)
                db.flush()
            floor = db.query(Floor).filter_by(floorname=floor_name, buildingid=building.buildingid).first()
            if not floor:
                floor = Floor(floorname=floor_name, buildingid=building.buildingid)
                db.add(floor)
                db.flush()
            mac_address = ap.get('macAddress')
            ap_record = db.query(AccessPoint).filter_by(macaddress=mac_address).first()
            is_active = ap.get('raw', {}).get('reachabilityStatus', ap.get('raw', {}).get('reachabilityHealth')) == "UP"
            if not ap_record:
                ap_record = AccessPoint(
                    apname=ap_name,
                    macaddress=mac_address,
                    ipaddress=ap.get('raw', {}).get('managementIpAddress', ap.get('raw', {}).get('ipAddress')),
                    modelname=ap.get('raw', {}).get('platformId', ap.get('raw', {}).get('model')),
                    isactive=is_active,
                    floorid=floor.floorid,
                    buildingid=building.buildingid
                )
                db.add(ap_record)
                db.flush()
            else:
                ap_record.isactive = is_active
                ap_record.floorid = floor.floorid
                ap_record.buildingid = building.buildingid
            count = ap.get('clientCount', 0)
            if isinstance(count, dict):
                count = sum(count.values())
            building_totals.setdefault(canonical_building_name, 0)
            building_totals[canonical_building_name] += count or 0
            # Insert/update ClientCountAP for radio0 (fallback)
            radio = db.query(RadioType).filter_by(radioname='radio0').first()
            if radio:
                cc = db.query(ClientCountAP).filter_by(
                    apid=ap_record.apid,
                    radioid=radio.radioid,
                    timestamp=now
                ).first()
                if cc:
                    cc.clientcount = count or 0
                else:
                    cc = ClientCountAP(
                        apid=ap_record.apid,
                        radioid=radio.radioid,
                        clientcount=count or 0,
                        timestamp=now
                    )
                    db.add(cc)
        # --- Update wireless_count DB with building totals ---
        for building_name, total_clients in building_totals.items():
            canonical_building_name = normalize_building_name(building_name)
            if not canonical_building_name:
                logger.warning(f"Skipping update for unmapped building name: {building_name}")
                continue
            building = wireless_buildings.get(canonical_building_name.lower())
            if building:
                client_count = ClientCount(
                    building_id=building.building_id,
                    client_count=total_clients,
                    time_inserted=now
                )
                wireless_db.add(client_count)
                logger.info(f"Updated client count for building {canonical_building_name}: {total_clients}")
            else:
                logger.warning(f"Building {canonical_building_name} not found in wireless_count database")
        for building_name, building in wireless_buildings.items():
            if building_name.lower() not in {k.lower() for k in building_totals}:
                client_count = ClientCount(
                    building_id=building.building_id,
                    client_count=0,
                    time_inserted=now
                )
                wireless_db.add(client_count)
                logger.info(f"Created zero count record for building {building_name}")
        db.commit()
        wireless_db.commit()
        logger.info("Client count data updated successfully in both databases")
        if incomplete_aps:
            logger.warning(f"{len(incomplete_aps)} APs were incomplete and may need further data recovery. See logs for details.")
    except HTTPError as e:
        if e.code in (404, 500):
            # Set global maintenance window for 1 hour
            MAINTENANCE_UNTIL = datetime.now(timezone.utc) + timedelta(hours=1)
            logger.error(f"Maintenance window or server error detected (HTTP {e.code}). Entering maintenance until {MAINTENANCE_UNTIL.isoformat()}.")
            log_api_error("HTTPError", f"Maintenance window or server error (HTTP {e.code}): {e}")
            next_run = MAINTENANCE_UNTIL
            reschedule_job("update_client_count_task", update_client_count_task, next_run)
            return
        if db:
            db.rollback()
        if wireless_db:
            wireless_db.rollback()
        logger.error(f"Error updating client count data: {str(e)}")
        log_api_error("HTTPError", e)
        # Do not re-raise, just log and continue to reschedule
    except Exception as e:
        if db:
            db.rollback()
        if wireless_db:
            wireless_db.rollback()
        logger.error(f"Error updating client count data: {str(e)}")
        log_api_error("Exception", e)
        raise
    finally:
        if close_db and db:
            db.close()
        if close_wireless_db and wireless_db:
            wireless_db.close()
        # Only reschedule if not in maintenance
        if not (MAINTENANCE_UNTIL and datetime.now(timezone.utc) < MAINTENANCE_UNTIL):
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
def get_aps(db: Session = Depends(get_wireless_db_dep)):
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

@app.get("/client-counts")
def get_client_counts(
    ap_id: Optional[int] = None,
    radio_id: Optional[int] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    db: Session = Depends(get_apclient_db_dep)
):
    """Get AP client count data with optional filters (AP client DB)."""
    try:
        query = db.query(ClientCountAP)
        if ap_id:
            query = query.filter(ClientCountAP.apid == ap_id)
        if radio_id:
            query = query.filter(ClientCountAP.radioid == radio_id)
        if start_time:
            query = query.filter(ClientCountAP.timestamp >= start_time)
        if end_time:
            query = query.filter(ClientCountAP.timestamp <= end_time)
        counts = query.all()
        return [
            {
                "count_id": c.countid,
                "apid": c.apid,
                "radioid": c.radioid,
                "client_count": c.clientcount,
                "timestamp": c.timestamp.isoformat() if c.timestamp else None
            }
            for c in counts
        ]
    except Exception as e:
        logger.error(f"Error in /client-counts: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/buildings", response_model=List[dict], tags=["Buildings"])
def get_buildings(db: Session = Depends(get_wireless_db_dep)):
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
def get_floors(building_id: int, db: Session = Depends(get_wireless_db_dep)):
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
def get_rooms(floor_id: int, db: Session = Depends(get_wireless_db_dep)):
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
def get_radio_types(db: Session = Depends(get_wireless_db_dep)):
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
def create_campus(campus: CampusCreate, db: Session = Depends(get_wireless_db_dep)):
    """Create a new campus."""
    db_campus = Campus(campus_name=campus.campus_name)
    db.add(db_campus)
    db.commit()
    db.refresh(db_campus)
    return db_campus

@app.get("/wireless/campuses/", response_model=List[CampusResponse])
def get_campuses(db: Session = Depends(get_wireless_db_dep)):
    """Get all campuses."""
    return db.query(Campus).all()

@app.post("/wireless/buildings/", response_model=BuildingResponse)
def create_building(building: BuildingCreate, db: Session = Depends(get_wireless_db_dep)):
    """Create a new building."""
    db_building = Building(**building.dict())
    db.add(db_building)
    db.commit()
    db.refresh(db_building)
    return db_building

@app.get("/wireless/buildings/", response_model=List[BuildingResponse])
def get_wireless_buildings(campus_id: Optional[int] = None, db: Session = Depends(get_wireless_db_dep)):
    """Get all buildings, optionally filtered by campus."""
    query = db.query(Building)
    if campus_id:
        query = query.filter(Building.campus_id == campus_id)
    return query.all()

@app.post("/wireless/client-counts/", response_model=ClientCountResponse)
def create_client_count(count: ClientCountCreate, db: Session = Depends(get_wireless_db_dep)):
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
    db: Session = Depends(get_wireless_db_dep)
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
def create_ap_building(building: ApBuildingCreate, db: Session = Depends(get_apclient_db_dep)):
    """Create a new AP building."""
    db_building = ApBuilding(**building.dict())
    db.add(db_building)
    db.commit()
    db.refresh(db_building)
    return db_building

@app.get("/ap/buildings/", response_model=List[ApBuildingResponse])
def get_ap_buildings(db: Session = Depends(get_apclient_db_dep)):
    """Get all AP buildings."""
    return db.query(ApBuilding).all()

@app.post("/ap/floors/", response_model=FloorResponse)
def create_floor(floor: FloorCreate, db: Session = Depends(get_apclient_db_dep)):
    """Create a new floor."""
    db_floor = Floor(**floor.dict())
    db.add(db_floor)
    db.commit()
    db.refresh(db_floor)
    return db_floor

@app.get("/ap/floors/", response_model=List[FloorResponse])
def get_ap_floors(building_id: Optional[int] = None, db: Session = Depends(get_apclient_db_dep)):
    """Get all floors, optionally filtered by building."""
    query = db.query(Floor)
    if building_id:
        query = query.filter(Floor.buildingid == building_id)
    return query.all()

@app.post("/ap/rooms/", response_model=RoomResponse)
def create_room(room: RoomCreate, db: Session = Depends(get_apclient_db_dep)):
    """Create a new room."""
    db_room = Room(**room.dict())
    db.add(db_room)
    db.commit()
    db.refresh(db_room)
    return db_room

@app.get("/ap/rooms/", response_model=List[RoomResponse])
def get_ap_rooms(floor_id: Optional[int] = None, db: Session = Depends(get_apclient_db_dep)):
    """Get all rooms, optionally filtered by floor."""
    query = db.query(Room)
    if floor_id:
        query = query.filter(Room.floorid == floor_id)
    return query.all()

@app.post("/ap/access-points/", response_model=AccessPointResponse)
def create_access_point(ap: AccessPointCreate, db: Session = Depends(get_apclient_db_dep)):
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
    db: Session = Depends(get_apclient_db_dep)
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
def create_radio_type(radio: RadioTypeCreate, db: Session = Depends(get_apclient_db_dep)):
    """Create a new radio type."""
    db_radio = RadioType(**radio.dict())
    db.add(db_radio)
    db.commit()
    db.refresh(db_radio)
    return db_radio

@app.get("/ap/radio-types/", response_model=List[RadioTypeResponse])
def get_ap_radio_types(db: Session = Depends(get_apclient_db_dep)):
    """Get all radio types."""
    return db.query(RadioType).all()

@app.post("/ap/client-counts/", response_model=ClientCountAPResponse)
def create_client_count_ap(count: ClientCountAPCreate, db: Session = Depends(get_apclient_db_dep)):
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
    db: Session = Depends(get_apclient_db_dep)
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
    """Calculate the next run time for scheduled tasks."""
    now = datetime.now(TORONTO_TZ)
    # Add 5 minutes to current time
    next_run = now + timedelta(minutes=5)
    next_run = next_run.replace(second=0, microsecond=0)
    return next_run

@app.get("/diagnostics/zero-counts")
async def get_zero_count_diagnostics():
    """Get diagnostics for buildings with zero client counts."""
    try:
        with get_wireless_db() as db:
            # Get current timestamp
            current_time = datetime.now(TORONTO_TZ)
            
            # Get all buildings
            buildings = db.query(Building).all()
            
            zero_count_buildings = []
            
            for building in buildings:
                # Get latest client count
                latest_count = db.query(ClientCount)\
                    .filter(ClientCount.building_id == building.building_id)\
                    .order_by(ClientCount.time_inserted.desc())\
                    .first()
                
                if not latest_count:
                    continue
                    
                # Get historical average (last 24 hours)
                one_day_ago = current_time - timedelta(days=1)
                historical_counts = db.query(ClientCount)\
                    .filter(
                        ClientCount.building_id == building.building_id,
                        ClientCount.time_inserted >= one_day_ago
                    ).all()
                
                if not historical_counts:
                    continue
                    
                historical_avg = sum(count.client_count for count in historical_counts) / len(historical_counts)
                
                # If current count is 0 but historical average is significant
                if latest_count.client_count == 0 and historical_avg > 5:
                    # Get AP status
                    ap_status = {
                        'total_aps': len(building.access_points),
                        'active_aps': sum(1 for ap in building.access_points if ap.status == 'active'),
                        'inactive_aps': sum(1 for ap in building.access_points if ap.status == 'inactive')
                    }
                    
                    # Get DNA Center status
                    dna_status = {
                        'total_aps_in_dna': len(building.access_points),
                        'aps_with_clients': sum(1 for ap in building.access_points if ap.client_count > 0)
                    }
                    
                    # Determine severity
                    if historical_avg > 50:
                        severity = 'high'
                    elif historical_avg > 20:
                        severity = 'medium'
                    else:
                        severity = 'low'
                    
                    zero_count_buildings.append({
                        'building_name': building.building_name,
                        'campus_name': building.campus.campus_name if building.campus else 'Unknown',
                        'current_count': latest_count.client_count,
                        'historical_average': round(historical_avg, 2),
                        'severity': severity,
                        'ap_status': ap_status,
                        'dna_center_status': dna_status,
                        'last_updated': latest_count.time_inserted.isoformat()
                    })
            
            return {
                'timestamp': current_time.isoformat(),
                'total_buildings_analyzed': len(buildings),
                'buildings_with_zero_counts': len(zero_count_buildings),
                'zero_count_buildings': zero_count_buildings
            }
            
    except Exception as e:
        logger.error(f"Error in get_zero_count_diagnostics: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error retrieving zero count diagnostics: {str(e)}"
        )

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

@app.get("/diagnostics/incomplete-devices")
async def get_incomplete_devices():
    """Get diagnostics for incomplete APs/devices (missing required fields)."""
    if not is_diagnostics_enabled():
        raise HTTPException(status_code=403, detail="Diagnostics are not enabled. Set ENABLE_DIAGNOSTICS=true to enable.")
    data = get_incomplete_diagnostics()
    return {"incomplete_devices": data, "count": len(data)}

@app.get("/diagnostics/api_health", tags=["Diagnostics"])
def get_api_health():
    """
    Get a summary of recent API error rates and details. Tracks the last 100 API errors in memory.
    Returns total errors tracked, errors in the last hour, and the 10 most recent errors.
    """
    return get_api_error_summary()

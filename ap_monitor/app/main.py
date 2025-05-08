import logging
from datetime import datetime, timedelta, UTC
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, Query
from contextlib import asynccontextmanager
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger

from app.db import get_db, init_db
from app.models import AccessPoint, ClientCount, Building, Floor, Room, Radio
from app.dna_api import AuthManager, fetch_client_counts, fetch_ap_data, radio_id_map
from app.utils import setup_logging, calculate_next_run_time

# Set up logging
logger = setup_logging()

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
        logger.info("Database initialized successfully")

        # Initialize radio table if empty
        with next(get_db()) as db:
            if db.query(Radio).count() == 0:
                logger.info("Initializing radio data...")
                radios = [
                    Radio(id=1, name="radio0", description="2.4 GHz"),
                    Radio(id=2, name="radio1", description="5 GHz"),
                    Radio(id=3, name="radio2", description="6 GHz")
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

def update_ap_data_task():
    """Background task to update AP data in the database."""
    with next(get_db()) as db:
        try:
            logger.info("Running scheduled task: update_ap_data_task")
            
            now = datetime.now()
            rounded_unix_timestamp = int(now.timestamp() * 1000)
            
            # Fetch AP data from DNA Center API with detailed information
            aps = fetch_ap_data(auth_manager, rounded_unix_timestamp)
            
            logger.info(f"Fetched {len(aps)} APs from DNAC API")
            
            # Process each AP
            for ap in aps:
                # Extract location details
                location = ap.get('location', '')
                location_parts = location.split('/')
                
                if len(location_parts) < 4:
                    logger.warning(f"Skipping device {ap.get('name')} due to invalid location format: {location}")
                    continue
                
                # Get building and floor names
                building_name = location_parts[2]
                floor_name = location_parts[3]
                room_name = location_parts[4] if len(location_parts) > 4 else None
                
                # Get or create building
                building = db.query(Building).filter_by(name=building_name).first()
                if not building:
                    building = Building(
                        name=building_name,
                        latitude=ap.get('latitude'),
                        longitude=ap.get('longitude')
                    )
                    db.add(building)
                    db.flush()
                
                # Get or create floor
                floor = db.query(Floor).filter_by(name=floor_name, building_id=building.id).first()
                if not floor:
                    floor = Floor(name=floor_name, building_id=building.id)
                    db.add(floor)
                    db.flush()
                
                # Get or create room if applicable
                room = None
                if room_name:
                    room = db.query(Room).filter_by(name=room_name, floor_id=floor.id).first()
                    if not room:
                        room = Room(name=room_name, floor_id=floor.id)
                        db.add(room)
                        db.flush()
                
                # Check if AP exists by MAC address
                mac_address = ap.get('macAddress', '')
                existing_ap = db.query(AccessPoint).filter_by(mac_address=mac_address).first() if mac_address else None
                
                is_active = True if ap.get('reachabilityHealth') == "UP" else False
                
                if existing_ap:
                    # Update existing AP
                    existing_ap.name = ap.get('name', 'Unknown')
                    existing_ap.ip_address = ap.get('ipAddress', '')
                    existing_ap.is_active = is_active
                    existing_ap.floor_id = floor.id
                    existing_ap.room_id = room.id if room else None
                    existing_ap.clients = ap.get('clientCount', {}).get('total', 0)
                    existing_ap.updated_at = now
                else:
                    # Create new AP
                    new_ap = AccessPoint(
                        name=ap.get('name', 'Unknown'),
                        mac_address=mac_address,
                        ip_address=ap.get('ipAddress', ''),
                        model_name=ap.get('model', 'Unknown'),
                        is_active=is_active,
                        floor_id=floor.id,
                        room_id=room.id if room else None,
                        clients=ap.get('clientCount', {}).get('total', 0)
                    )
                    db.add(new_ap)
            
            db.commit()
            logger.info("AP data updated successfully")
            
        except Exception as e:
            db.rollback()
            logger.error(f"Error updating AP data: {e}")
        finally:
            # Schedule the next run
            next_run = calculate_next_run_time()
            scheduler.add_job(
                func=update_ap_data_task,
                trigger=DateTrigger(run_date=next_run),
                id="update_ap_data_task",
                name="Update AP Data Task",
                replace_existing=True,
            )
            logger.info(f"Next AP data update scheduled at {next_run.strftime('%Y-%m-%d %H:%M:%S')}")

def update_client_count_task():
    """Background task to update client count data in the database."""
    with next(get_db()) as db:
        try:
            logger.info("Running scheduled task: update_client_count_task")
            
            now = datetime.now()
            rounded_unix_timestamp = int(now.timestamp() * 1000)
            
            # Fetch detailed AP data with client count information
            ap_data = fetch_ap_data(auth_manager, rounded_unix_timestamp)
            
            # Also fetch site-level client count data
            site_data = fetch_client_counts(auth_manager, rounded_unix_timestamp)
            
            # Process site-level client count data first (buildings)
            timestamp = now.strftime('%Y-%m-%d %H:%M:%S')
            
            for site in site_data:
                building_name = site.get('siteName')
                campus_name = site.get('parentSiteName')
                client_count_data = site.get('clientCount', {})
                total_clients = site.get('numberOfWirelessClients', 0)
                
                # Get or create building
                building = db.query(Building).filter_by(name=building_name).first()
                if not building:
                    building = Building(
                        name=building_name,
                        latitude=site.get('latitude'),
                        longitude=site.get('longitude')
                    )
                    db.add(building)
                    db.flush()
                
                # Process each AP in the site data
                # This will only handle APs that are directly associated with the site
                # and have client counts at the site level
                
                # For now, we'll just log the site-level client counts
                logger.info(f"Building {building_name}: {total_clients} clients")
            
            # Process AP-level client count data
            count = 0
            for device in ap_data:
                # Skip devices without client count information
                if 'clientCount' not in device:
                    continue
                
                mac_address = device.get('macAddress')
                if not mac_address:
                    logger.warning(f"Skipping device {device.get('name')} without MAC address")
                    continue
                
                # Find the AP in the database
                ap = db.query(AccessPoint).filter_by(mac_address=mac_address).first()
                if not ap:
                    logger.warning(f"AP with MAC {mac_address} not found in database, skipping client count update")
                    continue
                
                # Update overall client count on AP record
                client_count_dict = device.get('clientCount', {})
                total_clients = sum(client_count_dict.values()) if client_count_dict else 0
                ap.clients = total_clients
                
                # Insert client count records for each radio
                for radio_key, client_count in client_count_dict.items():
                    radio_id = radio_id_map.get(radio_key)
                    if radio_id:
                        client_count_record = ClientCount(
                            ap_id=ap.id,
                            radio_id=radio_id,
                            client_count=client_count,
                            timestamp=timestamp
                        )
                        db.add(client_count_record)
                        count += 1
            
            db.commit()
            logger.info(f"Successfully inserted {count} client count records")
            
        except Exception as e:
            db.rollback()
            logger.error(f"Error updating client count data: {e}")
        finally:
            # Schedule the next run
            next_run = calculate_next_run_time()
            scheduler.add_job(
                func=update_client_count_task,
                trigger=DateTrigger(run_date=next_run),
                id="update_client_count_task",
                name="Update Client Count Task",
                replace_existing=True,
            )
            logger.info(f"Next client count update scheduled at {next_run.strftime('%Y-%m-%d %H:%M:%S')}")

    """Manually trigger an update of client count data."""
    try:
        logger.info("Manual update of client count data requested")
        
        now = datetime.now()
        rounded_unix_timestamp = int(now.timestamp() * 1000)
        
        # Fetch client count data from DNA Center API
        site_data = fetch_client_counts(auth_manager, rounded_unix_timestamp)
        
        # Insert client count data
        timestamp = now.strftime('%Y-%m-%d %H:%M:%S')
        count = 0
        
        for site in site_data:
            building_name = site.get('siteName')
            campus_name = site.get('parentSiteName')
            client_count_data = site.get('clientCount', {})
            
            # Get or create building
            building = db.query(Building).filter_by(name=campus_name).first()
            if not building:
                building = Building(name=campus_name)
                db.add(building)
                db.flush()
            
            # Get or create floor
            floor = db.query(Floor).filter_by(name=building_name, building_id=building.id).first()
            if not floor:
                floor = Floor(name=building_name, building_id=building.id)
                db.add(floor)
                db.flush()
            
            # Find or create the access point
            ap_name = site.get('name', building_name)
            mac_address = site.get('macAddress', '')
            
            ap = None
            if mac_address:
                ap = db.query(AccessPoint).filter_by(mac_address=mac_address).first()
            
            if not ap:
                ap = AccessPoint(
                    name=ap_name,
                    mac_address=mac_address,
                    ip_address=site.get('ipAddress', ''),
                    model_name=site.get('type', 'Unknown'),
                    is_active=1 if site.get('healthScore', 0) > 0 else 0,
                    floor_id=floor.id,
                    clients=site.get('numberOfWirelessClients', 0)
                )
                db.add(ap)
                db.flush()
            
            # Insert client count for each radio
            for radio, radio_clients in client_count_data.items():
                radio_id = radio_id_map.get(radio)
                if radio_id:
                    client_count = ClientCount(
                        ap_id=ap.id,
                        radio_id=radio_id,
                        client_count=radio_clients,
                        timestamp=timestamp
                    )
                    db.add(client_count)
                    count += 1
            
            # If no radio-specific data, add total client count
            if not client_count_data and site.get('numberOfWirelessClients', 0) > 0:
                client_count = ClientCount(
                    ap_id=ap.id,
                    radio_id=1,  # Default to radio0
                    client_count=site.get('numberOfWirelessClients', 0),
                    timestamp=timestamp
                )
                db.add(client_count)
                count += 1
        
        db.commit()
        logger.info(f"Successfully inserted {count} client count records")
        
        return {"message": "Client count data updated successfully", "count": count}
    except SQLAlchemyError as e:
        db.rollback()
        logger.error(f"Database error in /update-client-counts: {e}")
        raise HTTPException(status_code=500, detail="Database error")
    except Exception as e:
        db.rollback()
        logger.error(f"Unexpected error in /update-client-counts: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.get("/aps", response_model=List[dict], tags=["Access Points"])
def get_aps(db: Session = Depends(get_db)):
    """Get all access points from the database."""
    try:
        logger.info("Fetching AP data from the database")
        aps = db.query(AccessPoint).all()
        logger.info(f"Retrieved {len(aps)} AP records")
        
        # Convert SQLAlchemy objects to dictionaries
        return [{
            "id": ap.id,
            "name": ap.name,
            "status": ap.status,
            "clients": ap.clients,
            "updated_at": ap.updated_at
        } for ap in aps]
    except SQLAlchemyError as e:
        logger.error(f"Database error in /aps: {e}")
        raise HTTPException(status_code=500, detail="Database error")
    except Exception as e:
        logger.error(f"Unexpected error in /aps: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/client-counts", response_model=List[dict], tags=["Client Counts"])
def get_client_counts(
    building: str = None,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """
    Get client count data from the database.
    
    Args:
        building: Optional filter by building name
        limit: Maximum number of records to return (default: 100)
    """
    try:
        logger.info(f"Fetching client count data (building={building}, limit={limit})")
        
        # Start with base query
        query = db.query(
            ClientCount.id,
            AccessPoint.name.label("ap_name"),
            Floor.name.label("floor_name"),
            Building.name.label("building_name"),
            ClientCount.radio_id,
            ClientCount.client_count,
            ClientCount.timestamp
        ).join(
            AccessPoint, ClientCount.ap_id == AccessPoint.id
        ).join(
            Floor, AccessPoint.floor_id == Floor.id
        ).join(
            Building, Floor.building_id == Building.id
        ).order_by(
            ClientCount.timestamp.desc()
        )
        
        # Apply building filter if provided
        if building:
            query = query.filter(Building.name == building)
        
        # Apply limit
        results = query.limit(limit).all()
        
        logger.info(f"Retrieved {len(results)} client count records")
        
        # Convert query results to dictionaries
        return [{
            "id": r.id,
            "ap_name": r.ap_name,
            "floor_name": r.floor_name,
            "building_name": r.building_name,
            "radio_id": r.radio_id,
            "client_count": r.client_count,
            "timestamp": r.timestamp
        } for r in results]
    except SQLAlchemyError as e:
        logger.error(f"Database error in /client-counts: {e}")
        raise HTTPException(status_code=500, detail="Database error")
    except Exception as e:
        logger.error(f"Unexpected error in /client-counts: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/buildings", response_model=List[str], tags=["Buildings"])
def get_buildings(db: Session = Depends(get_db)):
    """Get list of unique buildings from the client count data."""
    try:
        logger.info("Fetching list of buildings")
        buildings = db.query(Building.name).distinct().all()
        return [b[0] for b in buildings]
    except SQLAlchemyError as e:
        logger.error(f"Database error in /buildings: {e}")
        raise HTTPException(status_code=500, detail="Database error")
    except Exception as e:
        logger.error(f"Unexpected error in /buildings: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
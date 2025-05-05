import logging
from datetime import datetime, timedelta
from typing import List
from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger

from app.db import get_db, init_db
from app.models import AccessPoint, ClientCount, Building, Floor
from app.dna_api import AuthManager, fetch_client_counts, get_ap_data
from app.utils import setup_logging, calculate_next_run_time

# Set up logging
logger = setup_logging()

# Create FastAPI application
app = FastAPI(
    title="AP Monitor",
    description="API for monitoring wireless access points and client counts",
    version="1.0.0",
)

# Initialize scheduler
scheduler = BackgroundScheduler()

# Create auth manager for DNA Center API
auth_manager = AuthManager()

# Radio ID mapping
radio_id_map = {'radio0': 1, 'radio1': 2, 'radio2': 3}

@app.on_event("startup")
async def startup_event():
    """Initialize database and start schedulers on application startup."""
    try:
        # Initialize database
        logger.info("Initializing database...")
        init_db()
        logger.info("Database initialized successfully")
        
        # Calculate next run time for scheduled tasks
        next_run = calculate_next_run_time()
        logger.info(f"First scheduled run at: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Schedule AP data update task
        scheduler.add_job(
            func=update_ap_data_task,
            trigger=DateTrigger(run_date=next_run),
            id="update_ap_data_task",
            name="Update AP Data Task",
            replace_existing=True,
        )
        
        # Schedule client count data update task
        scheduler.add_job(
            func=update_client_count_task,
            trigger=DateTrigger(run_date=next_run),
            id="update_client_count_task",
            name="Update Client Count Task",
            replace_existing=True,
        )
        
        # Start the scheduler
        scheduler.start()
        logger.info("Scheduler started successfully")
        
    except Exception as e:
        logger.error(f"Error during startup: {e}")
        raise

@app.on_event("shutdown")
async def shutdown_event():
    """Stop schedulers on application shutdown."""
    logger.info("Shutting down scheduler...")
    scheduler.shutdown()
    logger.info("Scheduler shut down successfully")

def update_ap_data_task():
    """Background task to update AP data in the database."""
    with next(get_db()) as db:
        try:
            logger.info("Running scheduled task: update_ap_data_task")
            
            # Fetch AP data from DNA Center API
            aps = get_ap_data(auth_manager)
            
            # Clear existing AP data
            db.query(AccessPoint).delete()
            
            # Insert new AP data
            for ap in aps:
                db_ap = AccessPoint(
                    name=ap["name"],
                    mac_address=ap["macAddress"],
                    ip_address=ap["ipAddress"],
                    model_name=ap["model"],
                    is_active=1 if ap["reachabilityHealth"] == "UP" else 0,
                    clients=ap["clients"]
                )
                db.add(db_ap)
            
            db.commit()
            logger.info(f"Successfully updated {len(aps)} access points")
            
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
            
            # Fetch client count data from DNA Center API
            site_data = fetch_client_counts(auth_manager, rounded_unix_timestamp)
            
            # Insert client count data
            timestamp = now.strftime('%Y-%m-%d %H:%M:%S')
            
            for site in site_data:
                building_name = site.get('siteName')
                campus_name = site.get('parentSiteName')
                latitude = site.get('latitude')
                longitude = site.get('longitude')
                client_count = site.get('numberOfWirelessClients', 0)
                
                # Get or create building
                building = db.query(Building).filter_by(name=campus_name).first()
                if not building:
                    building = Building(name=campus_name)
                    db.add(building)
                    db.commit()
                    db.refresh(building)
                
                # Get or create floor
                floor = db.query(Floor).filter_by(name=building_name, building_id=building.id).first()
                if not floor:
                    floor = Floor(name=building_name, building_id=building.id)
                    db.add(floor)
                    db.commit()
                    db.refresh(floor)
                
                # Get or create access point
                ap = db.query(AccessPoint).filter_by(mac_address=site.get('macAddress')).first()
                if not ap:
                    ap = AccessPoint(
                        name=site.get('name'),
                        mac_address=site.get('macAddress'),
                        ip_address=site.get('ipAddress'),
                        model_name=site.get('model'),
                        is_active=1 if site.get('reachabilityHealth') == "UP" else 0,
                        floor_id=floor.id
                    )
                    db.add(ap)
                    db.commit()
                    db.refresh(ap)
                
                # Insert client count
                for radio, count in site.get('clientCount', {}).items():
                    radio_id = radio_id_map.get(radio)
                    if radio_id:
                        db_client_count = ClientCount(
                            ap_id=ap.id,
                            radio_id=radio_id,
                            client_count=count,
                            timestamp=timestamp
                        )
                        db.add(db_client_count)
            
            db.commit()
            logger.info(f"Successfully inserted {len(site_data)} client count records")
            
        except Exception as e:
            db.rollback()
            logger.error(f"Error updating client count data: {e}")
            
            
@app.get("/", tags=["Health"])
def read_root():
    """Health check endpoint."""
    return {"status": "healthy", "message": "AP Monitor API is running"}

@app.post("/update-aps", tags=["Access Points"])
def update_aps(db: Session = Depends(get_db)):
    """Manually trigger an update of AP data."""
    try:
        logger.info("Manual update of AP data requested")
        
        # Fetch AP data from DNA Center API
        aps = get_ap_data(auth_manager)
        
        # Clear existing AP data
        db.query(AccessPoint).delete()
        
        # Insert new AP data
        for ap in aps:
            db_ap = AccessPoint(
                name=ap["name"],
                status=ap["status"],
                clients=ap["clients"]
            )
            db.add(db_ap)
        
        db.commit()
        logger.info(f"Successfully updated {len(aps)} access points")
        
        return {"message": "AP data updated successfully", "count": len(aps)}
    except SQLAlchemyError as e:
        db.rollback()
        logger.error(f"Database error in /update-aps: {e}")
        raise HTTPException(status_code=500, detail="Database error")
    except Exception as e:
        db.rollback()
        logger.error(f"Unexpected error in /update-aps: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.post("/update-client-counts", tags=["Client Counts"])
def update_client_counts(db: Session = Depends(get_db)):
    """Manually trigger an update of client count data."""
    try:
        logger.info("Manual update of client count data requested")
        
        now = datetime.now()
        rounded_unix_timestamp = int(now.timestamp() * 1000)
        
        # Fetch client count data from DNA Center API
        site_data = fetch_client_counts(auth_manager, rounded_unix_timestamp)
        
        # Insert client count data
        timestamp = now.strftime('%Y-%m-%d %H:%M:%S')
        
        for site in site_data:
            db_client_count = ClientCount(
                building_name=site.get('siteName'),
                campus_name=site.get('parentSiteName'),
                client_count=site.get('numberOfWirelessClients', 0),
                timestamp=timestamp,
                latitude=site.get('latitude'),
                longitude=site.get('longitude')
            )
            db.add(db_client_count)
        
        db.commit()
        logger.info(f"Successfully inserted {len(site_data)} client count records")
        
        return {"message": "Client count data updated successfully", "count": len(site_data)}
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
        
        # Build query
        query = db.query(ClientCount).order_by(ClientCount.timestamp.desc())
        
        # Apply building filter if provided
        if building:
            query = query.filter(ClientCount.building_name == building)
        
        # Apply limit
        client_counts = query.limit(limit).all()
        
        logger.info(f"Retrieved {len(client_counts)} client count records")
        
        # Convert SQLAlchemy objects to dictionaries
        return [{
            "id": cc.id,
            "building_name": cc.building_name,
            "campus_name": cc.campus_name,
            "client_count": cc.client_count,
            "timestamp": cc.timestamp,
            "latitude": cc.latitude,
            "longitude": cc.longitude
        } for cc in client_counts]
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
        
        # Query distinct building names
        buildings = db.query(ClientCount.building_name).distinct().all()
        
        # Extract building names from result tuples
        building_names = [building[0] for building in buildings]
        
        logger.info(f"Retrieved {len(building_names)} buildings")
        
        return building_names
    except SQLAlchemyError as e:
        logger.error(f"Database error in /buildings: {e}")
        raise HTTPException(status_code=500, detail="Database error")
    except Exception as e:
        logger.error(f"Unexpected error in /buildings: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
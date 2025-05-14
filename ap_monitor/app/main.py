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

from app.db import get_db, init_db
from app.models import AccessPoint, ClientCount, Building, Floor, Room, Radio
from app.dna_api import AuthManager, fetch_client_counts, fetch_ap_data, radio_id_map
from app.utils import setup_logging, calculate_next_run_time

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
            logger.debug(f"Database session being used: {db}")


            now = datetime.now()
            rounded_unix_timestamp = int(now.timestamp() * 1000)

            # Fetch AP data from DNA Center API with detailed information
            aps = fetch_ap_data(auth_manager, rounded_unix_timestamp)

            logger.info(f"Fetched {len(aps)} APs from DNAC API")

            # Process each AP
            for ap in aps:
                location = ap.get('location', '')
                location_parts = location.split('/')

                if len(location_parts) < 4:
                    logger.warning(f"Skipping device {ap.get('name')} due to invalid location format: {location}")
                    continue

                building_name = location_parts[2]
                floor_name = location_parts[3]

                logger.debug(f"Processing AP: {ap.get('name')} in Building: {building_name}, Floor: {floor_name}")

                # Handle building
                building = db.query(Building).filter_by(name=building_name).first()
                if not building:
                    logger.debug(f"Creating new building: {building_name}")
                    building = Building(name=building_name, latitude=ap.get('latitude'), longitude=ap.get('longitude'))
                    db.add(building)
                    db.flush()

                # Handle floor
                floor = db.query(Floor).filter_by(number=floor_name, building_id=building.id).first()
                if not floor:
                    logger.debug(f"Creating new floor: {floor_name} for Building: {building_name}")
                    floor = Floor(number=floor_name, building_id=building.id)
                    db.add(floor)
                    db.flush()

                # Handle AP
                ap_record = db.query(AccessPoint).filter_by(mac_address=ap.get('macAddress')).first()
                if not ap_record:
                    logger.debug(f"Creating new AccessPoint: {ap.get('name')} with MAC: {ap.get('macAddress')}")
                    ap_record = AccessPoint(
                        name=ap.get('name'),
                        mac_address=ap.get('macAddress'),
                        ip_address=ap.get('ipAddress'),
                        model_name=ap.get('model'),
                        is_active=True,
                        floor_id=floor.id,
                        clients=sum(ap.get('clientCount', {}).values()),
                    )
                    db.add(ap_record)
                else:
                    logger.debug(f"Updating existing AccessPoint: {ap.get('name')} with new client count")
                    ap_record.clients = sum(ap.get('clientCount', {}).values())

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
                building_name = site.get("parentSiteName")
                floor_name = site.get("siteName")
                client_counts = site.get("clientCount", {})

                building = db.query(Building).filter_by(name=building_name).first()
                if not building:
                    building = Building(name=building_name, latitude=0.0, longitude=0.0)
                    db.add(building)
                    db.flush()

                floor = db.query(Floor).filter_by(number=floor_name, building_id=building.id).first()
                if not floor:
                    floor = Floor(number=floor_name, building_id=building.id)
                    db.add(floor)
                    db.flush()

                for radio_name, count in client_counts.items():
                    radio = db.query(Radio).filter_by(name=radio_name).first()
                    if not radio:
                        radio = Radio(name=radio_name, description="Unknown")
                        db.add(radio)
                        db.flush()

                    client_count = ClientCount(
                        ap_id=None,  # Site-level data has no AP ID
                        radio_id=radio.id,
                        client_count=count,
                        timestamp=timestamp
                    )
                    db.add(client_count)

            db.commit()
            logger.info("Client count data updated successfully")

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
        query = db.query(ClientCount)

        if building:
            query = query.join(AccessPoint).join(Floor).join(Building).filter(Building.name == building)

        query = query.limit(limit)
        results = query.all()

        return [
            {
                "ap_name": db.query(AccessPoint).filter(AccessPoint.id == cc.ap_id).first().name,
                "client_count": cc.client_count,
                "timestamp": cc.timestamp
            }
            for cc in results
        ]
    except SQLAlchemyError as e:
        logger.error(f"Database error in /client-counts: {e}")
        raise HTTPException(status_code=500, detail="Database error")
    except Exception as e:
        logger.error(f"Unexpected error in /client-counts: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

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
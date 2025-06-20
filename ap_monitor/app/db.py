print('DEBUG: ap_monitor.app.db module loaded')
import logging
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv
from contextlib import contextmanager

# Load .env file
load_dotenv()

# Configure logger
logger = logging.getLogger(__name__)

# Get database configuration from environment variables
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_NAME = os.getenv("DB_NAME", "wireless_count")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_PORT = os.getenv("DB_PORT", "3306")
APCLIENT_DB_URL = os.getenv("APCLIENT_DB_URL")

# Create database URLs
WIRELESS_DB_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# For testing, use SQLite in-memory database
if os.getenv("TESTING", "false").lower() == "true":
    WIRELESS_DB_URL = "sqlite:///:memory:"
    APCLIENT_DB_URL = "sqlite:///:memory:"

# Create base classes for declarative models
WirelessBase = declarative_base()
APClientBase = declarative_base()

# Initialize engines and session factories
wireless_engine = None
apclient_engine = None
WirelessSessionLocal = None
APClientSessionLocal = None

try:
    # Create SQLAlchemy engines with appropriate configuration for each database type
    if os.getenv("TESTING", "false").lower() == "true":
        # SQLite configuration for testing
        wireless_engine = create_engine(
            WIRELESS_DB_URL,
            connect_args={"check_same_thread": False}
        )
        apclient_engine = create_engine(
            APCLIENT_DB_URL,
            connect_args={"check_same_thread": False}
        )
    else:
        # PostgreSQL configuration for production
        wireless_engine = create_engine(
            WIRELESS_DB_URL,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            pool_recycle=3600
        )
        apclient_engine = create_engine(
            APCLIENT_DB_URL,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            pool_recycle=3600
        )
    
    # Create session factories
    WirelessSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=wireless_engine)
    APClientSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=apclient_engine)
    
    logger.info(f"Database connections set up successfully")
except Exception as e:
    logger.error(f"Error connecting to the databases: {e}")
    raise

@contextmanager
def get_wireless_db():
    """Dependency for getting wireless_count DB session in FastAPI endpoints."""
    db = WirelessSessionLocal()
    try:
        yield db
    finally:
        db.close()

@contextmanager
def get_apclient_db():
    """Dependency for getting apclientcount DB session in FastAPI endpoints."""
    db = APClientSessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_wireless_db_session():
    """Get a wireless database session without context management."""
    return WirelessSessionLocal()

def get_apclient_db_session():
    """Get an AP client database session without context management."""
    return APClientSessionLocal()

def get_apclient_db_dep():
    """FastAPI dependency for getting apclientcount DB session (generator, not context manager)."""
    db = APClientSessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_wireless_db_dep():
    """FastAPI dependency for getting wireless_count DB session (generator, not context manager)."""
    db = WirelessSessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    """Initialize databases by creating tables."""
    try:
        logger.info("Creating database tables...")
        # Import models here to avoid circular imports
        from ap_monitor.app.models import (
            Campus, Building, ClientCount,  # wireless_count models
            ApBuilding, Floor, Room, AccessPoint, RadioType, ClientCountAP  # apclientcount models
        )
        
        # Create tables for wireless_count
        WirelessBase.metadata.create_all(bind=wireless_engine)
        logger.info("Wireless count database tables created successfully")
        
        # Create tables for apclientcount
        APClientBase.metadata.create_all(bind=apclient_engine)
        logger.info("AP client count database tables created successfully")
    except Exception as e:
        logger.error(f"Error initializing databases: {e}")
        raise

# Make these available at module level for testing
__all__ = [
    'wireless_engine',
    'apclient_engine',
    'WirelessSessionLocal',
    'APClientSessionLocal',
    'WirelessBase',
    'APClientBase',
    'get_wireless_db',
    'get_apclient_db',
    'get_wireless_db_session',
    'get_apclient_db_session',
    'get_apclient_db_dep',
    'get_wireless_db_dep',
    'init_db'
]
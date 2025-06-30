import logging
import os
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import gzip

TORONTO_TZ = ZoneInfo("America/Toronto")

def setup_logging():
    """Configure logging for the application."""
    log_dir = "Logs"
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "ap-monitor.log")
    
    def namer(name):
        return name + ".gz"

    def rotator(source, dest):
        with open(source, "rb") as sf, gzip.open(dest, "wb") as df:
            df.writelines(sf)
        os.remove(source)

    handler = TimedRotatingFileHandler(log_file, when="D", interval=1, backupCount=14)
    handler.namer = namer
    handler.rotator = rotator
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            handler,
            logging.StreamHandler()
        ]
    )
    
    logger = logging.getLogger(__name__)
    logger.info("Logging configured successfully")
    
    return logger

def calculate_next_run_time():
    """
    Calculate the next run time as exactly 5 minutes from now.
    This ensures consistent 5-minute intervals between task runs.
    """
    now = datetime.now(TORONTO_TZ)
    next_run = now + timedelta(minutes=5)
    return next_run.replace(second=0, microsecond=0)

def load_env_file(file_path=".env"):
    """
    Load environment variables from .env file.
    
    Args:
        file_path: Path to the .env file
        
    Returns:
        Dict containing environment variables
    """
    env_vars = {}
    try:
        with open(file_path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    if "=" not in line:
                        raise ValueError(f"Invalid line in .env file: {line}")
                    key, value = line.split("=", 1)
                    env_vars[key.strip()] = value.strip()
        return env_vars
    except FileNotFoundError:
        raise FileNotFoundError(f"Error: .env file not found at {file_path}")
    except ValueError:
        # propagate invalid-format errors
        raise
    except Exception as e:
        raise Exception(f"Error parsing .env file: {e}")

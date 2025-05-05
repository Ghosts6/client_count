import logging
import os
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime, timedelta

def setup_logging():
    """Configure logging for the application."""
    log_dir = "Logs"
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "ap-monitor.log")
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            TimedRotatingFileHandler(log_file, when="D", interval=1, backupCount=30),
            logging.StreamHandler()
        ]
    )
    
    logger = logging.getLogger(__name__)
    logger.info("Logging configured successfully")
    
    return logger

def calculate_next_run_time():
    """
    Calculate the next 5-minute interval plus 1 minute for scheduled tasks.
    This matches the logic in the original code that sets specific intervals.
    """
    now = datetime.now()
    current_minute = now.minute
    
    # Calculate the next 5x + 1 minute interval
    next_minute = (current_minute // 5 + 1) * 5 + 1
    
    if next_minute >= 60:
        next_minute = 1  # Reset to 1 (not 0, to keep the +1 pattern)
        next_hour = (now.hour + 1) % 24
    else:
        next_hour = now.hour
        
    next_run = now.replace(hour=next_hour, minute=next_minute, second=0, microsecond=0)
    
    # If next_run is in the past (due to calculations), add 5 minutes
    if next_run <= now:
        next_run = next_run + timedelta(minutes=5)
        
    return next_run

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
                    key, value = line.split("=", 1)
                    env_vars[key.strip()] = value.strip()
        return env_vars
    except FileNotFoundError:
        raise FileNotFoundError(f"Error: .env file not found at {file_path}")
    except Exception as e:
        raise Exception(f"Error parsing .env file: {e}")
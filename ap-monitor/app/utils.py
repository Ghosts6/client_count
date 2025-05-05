import logging
import os
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime, timedelta

def setup_logging():
    """Configure logging for the application."""
    log_dir = "ap-monitor/Logs"
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "log.txt")
    
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
    """Calculate the next 5-minute interval for scheduled tasks."""
    now = datetime.now()
    current_minute = now.minute
    next_minute = (current_minute // 5 + 1) * 5
    
    if next_minute >= 60:
        next_minute = 0
        next_hour = (now.hour + 1) % 24
    else:
        next_hour = now.hour
        
    next_run = now.replace(hour=next_hour, minute=next_minute, second=0, microsecond=0)
    
    # If next_run is in the past (due to calculations), add 5 minutes
    if next_run <= now:
        next_run = next_run + timedelta(minutes=5)
        
    return next_run
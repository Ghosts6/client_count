
import os
import time
import logging
from logging.handlers import TimedRotatingFileHandler
import pytest
from unittest.mock import patch

# Log rotation test
@pytest.mark.parametrize("backup_count", [3])
def test_log_rotation_backup_count(tmp_path, backup_count):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_file = log_dir / "test.log"

    # Create a logger that rotates every second
    handler = TimedRotatingFileHandler(
        log_file, when="S", interval=1, backupCount=backup_count
    )
    logger = logging.getLogger("test_logger")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    # Generate enough logs to trigger rotation
    for i in range(backup_count + 2):
        logger.info(f"Log message {i}")
        time.sleep(1.1)  # Ensure rotation is triggered

    # Check that the number of backup files is correct
    log_files = sorted(log_dir.glob("test.log*"))
    assert len(log_files) == backup_count + 1  # +1 for the current log file

    # Cleanup
    handler.close()
    logger.removeHandler(handler)


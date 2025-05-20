import logging
import pytest
import io
from unittest.mock import patch, MagicMock, mock_open
from datetime import datetime
from ap_monitor.app.utils import setup_logging, calculate_next_run_time, load_env_file


@patch("ap_monitor.app.utils.os.makedirs")
@patch("ap_monitor.app.utils.TimedRotatingFileHandler")
@patch("ap_monitor.app.utils.logging.basicConfig")
@patch("ap_monitor.app.utils.logging.StreamHandler")
def test_setup_logging(mock_StreamHandler, mock_basicConfig, mock_TimedRotatingFileHandler, mock_makedirs):
    mock_stream_handler = MagicMock()
    mock_StreamHandler.return_value = mock_stream_handler

    logger = setup_logging()

    mock_makedirs.assert_called_once_with("Logs", exist_ok=True)
    mock_TimedRotatingFileHandler.assert_called_once_with(
        "Logs/ap-monitor.log", when="D", interval=1, backupCount=30
    )
    mock_basicConfig.assert_called_once_with(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[mock_TimedRotatingFileHandler.return_value, mock_stream_handler]
    )

    assert isinstance(logger, logging.Logger)

def test_calculate_next_run_time():
    next_run = calculate_next_run_time()
    assert isinstance(next_run, datetime)
    assert next_run.minute % 5 == 1
    assert next_run.second == 0
    assert next_run.microsecond == 0

@patch("builtins.open", new_callable=mock_open, read_data="VAR1=value1\nVAR2=value2\n")
def test_load_env_file(mock_file):
    result = load_env_file("dummy.env")
    assert result == {"VAR1": "value1", "VAR2": "value2"}
    mock_file.assert_called_once_with("dummy.env", "r")

@patch("builtins.open", new_callable=mock_open)
def test_load_env_file_file_not_found(mock_file):
    mock_file.side_effect = FileNotFoundError
    with pytest.raises(FileNotFoundError):
        load_env_file("missing.env")

def test_load_env_file_invalid_format():
    contents = "GOOD=1\nBADLINE\n"
    fake_file = io.StringIO(contents)

    with patch("builtins.open", return_value=fake_file):
        with pytest.raises(ValueError, match="Invalid line in .env file: BADLINE"):
            load_env_file("dummy.env")

import pytest
from unittest.mock import patch, MagicMock
from ap_monitor.app.db import (
    get_wireless_db,
    get_apclient_db,
    get_wireless_db_session,
    get_apclient_db_session,
    init_db
)
from sqlalchemy.exc import OperationalError

def test_get_wireless_db_yields_and_closes():
    mock_session = MagicMock()
    
    with patch("ap_monitor.app.db.WirelessSessionLocal", return_value=mock_session):
        with get_wireless_db() as db:
            assert db == mock_session
        mock_session.close.assert_called_once()

def test_get_apclient_db_yields_and_closes():
    mock_session = MagicMock()
    
    with patch("ap_monitor.app.db.APClientSessionLocal", return_value=mock_session):
        with get_apclient_db() as db:
            assert db == mock_session
        mock_session.close.assert_called_once()

def test_get_wireless_db_session():
    mock_session = MagicMock()
    
    with patch("ap_monitor.app.db.WirelessSessionLocal", return_value=mock_session):
        db = get_wireless_db_session()
        assert db == mock_session

def test_get_apclient_db_session():
    mock_session = MagicMock()
    
    with patch("ap_monitor.app.db.APClientSessionLocal", return_value=mock_session):
        db = get_apclient_db_session()
        assert db == mock_session

@patch("ap_monitor.app.db.WirelessBase.metadata.create_all")
@patch("ap_monitor.app.db.APClientBase.metadata.create_all")
@patch("ap_monitor.app.db.logger")
def test_init_db_success(mock_logger, mock_apclient_create_all, mock_wireless_create_all):
    fake_models = MagicMock()
    fake_models.AccessPoint = MagicMock()
    fake_models.ClientCount = MagicMock()
    
    with patch.dict("sys.modules", {"ap_monitor.app.models": fake_models}):
        init_db()

    mock_wireless_create_all.assert_called_once()
    mock_apclient_create_all.assert_called_once()
    mock_logger.info.assert_any_call("Creating database tables...")
    mock_logger.info.assert_any_call("Wireless count database tables created successfully")
    mock_logger.info.assert_any_call("AP client count database tables created successfully")

@patch("ap_monitor.app.db.WirelessBase.metadata.create_all", side_effect=OperationalError("DB error", None, None))
def test_init_db_failure(mock_create_all):
    with patch("ap_monitor.app.db.logger") as mock_logger:
        try:
            init_db()
        except OperationalError:
            pass
        mock_logger.error.assert_called_once()
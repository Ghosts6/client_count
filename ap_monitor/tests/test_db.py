import pytest
from unittest.mock import patch, MagicMock
from app.db import get_db, init_db
from sqlalchemy.exc import OperationalError

def test_get_db_yields_and_closes():
    mock_session = MagicMock()
    
    with patch("app.db.SessionLocal", return_value=mock_session):
        gen = get_db()
        db = next(gen)
        assert db == mock_session
        try:
            next(gen)
        except StopIteration:
            pass
        
        mock_session.close.assert_called_once()

@patch("app.db.Base.metadata.create_all")
@patch("app.db.logger")
@patch("app.db.engine")
def test_init_db_success(mock_engine, mock_logger, mock_create_all):
    fake_models = MagicMock()
    fake_models.AccessPoint = MagicMock()
    fake_models.ClientCount = MagicMock()
    
    with patch.dict("sys.modules", {"app.models": fake_models}):
        init_db()

    mock_create_all.assert_called_once_with(bind=mock_engine)
    mock_logger.info.assert_any_call("Creating database tables...")
    mock_logger.info.assert_any_call("Database tables created successfully")

@patch("app.db.Base.metadata.create_all", side_effect=OperationalError("DB error", None, None))
def test_init_db_failure(mock_create_all):
    with patch("app.db.logger") as mock_logger:
        try:
            init_db()
        except OperationalError:
            pass
        mock_logger.error.assert_called_once()
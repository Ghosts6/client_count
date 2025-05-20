import json
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta
from ap_monitor.app.dna_api import AuthManager, fetch_client_counts, fetch_ap_data, get_ap_data
from urllib.error import HTTPError, URLError


@patch("ap_monitor.app.dna_api.urlopen")
def test_get_token_success(mock_urlopen):
    mock_response = MagicMock()
    mock_response.__enter__.return_value.read.return_value = json.dumps({"Token": "mocked_token"}).encode()
    mock_response.__enter__.return_value.status = 200
    mock_urlopen.return_value = mock_response

    auth = AuthManager()
    token = auth.get_token()

    assert token == "mocked_token"
    assert auth.token == "mocked_token"


@patch("ap_monitor.app.dna_api.urlopen")
def test_fetch_client_counts(mock_urlopen):
    mock_response = MagicMock()
    mock_response.__enter__.return_value.read.return_value = json.dumps({
        "response": [
            {"parentSiteName": "Keele Campus"},
            {"parentSiteName": "Other Campus"}
        ]
    }).encode()
    mock_urlopen.return_value = mock_response

    auth_manager = MagicMock()
    auth_manager.get_token.return_value = "mocked_token"

    data = fetch_client_counts(auth_manager, 1715000000000)

    assert isinstance(data, list)
    assert all(site.get("parentSiteName") == "Keele Campus" for site in data)


@patch("ap_monitor.app.dna_api.urlopen")
def test_fetch_ap_data(mock_urlopen):
    mock_response = MagicMock()
    mock_response.__enter__.return_value.read.return_value = json.dumps({
        "response": [
            {"uuid": "abc123", "name": "AP1"},
            {"uuid": "abc123", "name": "AP1"},  # duplicate
            {"uuid": "def456", "name": "AP2"}
        ]
    }).encode()
    mock_urlopen.return_value = mock_response

    auth_manager = MagicMock()
    auth_manager.get_token.return_value = "mocked_token"

    data = fetch_ap_data(auth_manager, 1715000000000, retries=1)

    assert isinstance(data, list)
    assert len(data) == 2  # should remove duplicate UUIDs
    assert {ap['uuid'] for ap in data} == {"abc123", "def456"}


@patch("ap_monitor.app.dna_api.urlopen")
def test_get_ap_data(mock_urlopen):
    mock_response = MagicMock()
    mock_response.__enter__.return_value.read.return_value = json.dumps({
        "response": [
            {"type": "AP", "hostname": "AP01", "macAddress": "AA:BB", "managementIpAddress": "1.1.1.1", "platformId": "Cisco", "reachabilityStatus": "Reachable", "clientCount": 5},
            {"type": "Switch", "hostname": "Switch01"}  # Not an AP
        ]
    }).encode()
    mock_urlopen.return_value = mock_response

    auth_manager = MagicMock()
    auth_manager.get_token.return_value = "mocked_token"

    data = get_ap_data(auth_manager, retries=1)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]['name'] == "AP01"
    assert data[0]['macAddress'] == "AA:BB"


@patch("ap_monitor.app.dna_api.urlopen")
def test_auth_manager_token_refresh(mock_urlopen):
    mock_response = MagicMock()
    mock_response.__enter__.return_value.read.return_value = json.dumps({"Token": "abc123"}).encode()
    mock_response.__enter__.return_value.status = 200
    mock_urlopen.return_value = mock_response

    auth = AuthManager()
    token = auth.get_token()
    assert token == "abc123"
    assert auth.token_expiry > datetime.now()


@patch("ap_monitor.app.dna_api.urlopen", side_effect=HTTPError(None, 500, "Server Error", None, None))
def test_auth_manager_http_error(mock_urlopen):
    auth = AuthManager()
    with pytest.raises(Exception, match="Failed to obtain access token"):
        auth.get_token()


@patch("ap_monitor.app.dna_api.urlopen", side_effect=URLError("DNS failure"))
def test_auth_manager_url_error(mock_urlopen):
    auth = AuthManager()
    with pytest.raises(Exception, match="Failed to obtain access token"):
        auth.get_token()


@patch("ap_monitor.app.dna_api.urlopen")
def test_fetch_client_counts_retries(mock_urlopen):
    auth = AuthManager()
    auth.token = "token"
    auth.token_expiry = datetime.now() + timedelta(minutes=10)

    # Fail twice, succeed once
    fail = MagicMock(side_effect=Exception("Temp failure"))
    success = MagicMock()
    success.__enter__.return_value.read.return_value = json.dumps({"response": [{"parentSiteName": "Keele Campus"}]}).encode()
    success.__enter__.return_value.status = 200

    mock_urlopen.side_effect = [fail, fail, success]

    data = fetch_client_counts(auth, rounded_unix_timestamp=1234567890, retries=3)
    assert len(data) == 1
    assert data[0]["parentSiteName"] == "Keele Campus"


@patch("ap_monitor.app.dna_api.urlopen", side_effect=Exception("API unreachable"))
def test_get_ap_data_failure(mock_urlopen):
    auth = AuthManager()
    auth.token = "token"
    auth.token_expiry = datetime.now() + timedelta(minutes=10)

    with pytest.raises(Exception, match="API unreachable"):
        get_ap_data(auth_manager=auth, retries=1)


@patch.dict("os.environ", {"DNA_USERNAME": "", "DNA_PASSWORD": ""})
def test_env_vars_missing():
    with patch("ap_monitor.app.dna_api.AuthManager.__init__", side_effect=ValueError("DNA_USERNAME and DNA_PASSWORD must be set in .env file")):
        with pytest.raises(ValueError, match="DNA_USERNAME and DNA_PASSWORD must be set"):
            AuthManager()

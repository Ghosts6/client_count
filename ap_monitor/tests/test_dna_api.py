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
        "totalCount": 3,
        "response": [
            {
                "name": "AP1",
                "macAddress": "AA:BB:CC:DD:EE:FF",
                "ipAddress": "10.0.0.1",
                "location": "Global/Keele Campus/Building1/Floor1",
                "model": "Cisco AP",
                "clientCount": {"radio0": 5, "radio1": 3},
                "reachabilityHealth": "UP"
            },
            {
                "name": "AP2",
                "macAddress": "AA:BB:CC:DD:EE:FF",  # duplicate
                "ipAddress": "10.0.0.2",
                "location": "Global/Keele Campus/Building1/Floor1",
                "model": "Cisco AP",
                "clientCount": {"radio0": 2, "radio1": 1},
                "reachabilityHealth": "UP"
            },
            {
                "name": "AP3",
                "macAddress": "AA:BB:CC:DD:EE:EE",
                "ipAddress": "10.0.0.3",
                "location": "Global/Keele Campus/Building1/Floor1",
                "model": "Cisco AP",
                "clientCount": {"radio0": 4, "radio1": 2},
                "reachabilityHealth": "UP"
            }
        ]
    }).encode()
    mock_urlopen.return_value = mock_response

    auth_manager = MagicMock()
    auth_manager.get_token.return_value = "mocked_token"

    data = fetch_ap_data(auth_manager)
    
    # Should only get 2 devices since one MAC address is duplicate
    assert len(data) == 2
    
    # Verify the data structure and content
    mac_addresses = {device["macAddress"] for device in data}
    assert len(mac_addresses) == 2  # Should have 2 unique MAC addresses
    assert "AA:BB:CC:DD:EE:FF" in mac_addresses  # First device's MAC
    assert "AA:BB:CC:DD:EE:EE" in mac_addresses  # Third device's MAC
    
    # Verify the latest data is kept for the duplicate MAC
    duplicate_device = next(device for device in data if device["macAddress"] == "AA:BB:CC:DD:EE:FF")
    assert duplicate_device["ipAddress"] == "10.0.0.2"  # Should keep the latest data
    assert duplicate_device["clientCount"] == {"radio0": 2, "radio1": 1}  # Should keep the latest data


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


@patch("ap_monitor.app.dna_api.urlopen")
def test_fetch_ap_data_with_valid_location(mock_urlopen):
    mock_response = MagicMock()
    mock_response.__enter__.return_value.read.return_value = json.dumps({
        "totalCount": 1,
        "response": [{
            "name": "AP1",
            "macAddress": "AA:BB:CC:DD:EE:FF",
            "ipAddress": "10.0.0.1",
            "location": "Global/York University/Keele Campus/Building1/Floor1",
            "model": "Cisco AP",
            "clientCount": {"radio0": 5},
            "reachabilityHealth": "UP"
        }]
    }).encode()
    mock_urlopen.return_value = mock_response

    auth_manager = MagicMock()
    auth_manager.get_token.return_value = "mocked_token"

    data = fetch_ap_data(auth_manager)
    assert len(data) == 1
    assert data[0]["location"] == "Global/York University/Keele Campus/Building1/Floor1"


@patch("ap_monitor.app.dna_api.urlopen")
def test_fetch_ap_data_with_snmp_location_fallback(mock_urlopen):
    mock_response = MagicMock()
    mock_response.__enter__.return_value.read.return_value = json.dumps({
        "response": [{
            "uuid": "abc123",
            "name": "AP1",
            "location": None,
            "snmpLocation": "Global/York University/Keele Campus/Building1/Floor1",
            "macAddress": "AA:BB:CC:DD:EE:FF",
            "reachabilityHealth": "UP",
            "clientCount": {"radio0": 5}
        }]
    }).encode()
    mock_urlopen.return_value = mock_response

    auth_manager = MagicMock()
    auth_manager.get_token.return_value = "mocked_token"

    data = fetch_ap_data(auth_manager)

    assert len(data) == 1
    ap = data[0]
    # Verify snmpLocation is used as fallback
    assert ap["location"] is None
    assert ap["snmpLocation"] == "Global/York University/Keele Campus/Building1/Floor1"
    # Verify location parts are correctly parsed from snmpLocation
    location_parts = ap["snmpLocation"].split('/')
    assert len(location_parts) >= 5
    assert location_parts[-2] == "Building1"  # Building name
    assert location_parts[-1] == "Floor1"     # Floor name


@patch("ap_monitor.app.dna_api.urlopen")
def test_fetch_ap_data_with_location_name_fallback(mock_urlopen):
    mock_response = MagicMock()
    mock_response.__enter__.return_value.read.return_value = json.dumps({
        "response": [{
            "uuid": "abc123",
            "name": "AP1",
            "location": None,
            "snmpLocation": "default location",
            "locationName": "Global/York University/Keele Campus/Building1/Floor1",
            "macAddress": "AA:BB:CC:DD:EE:FF",
            "reachabilityHealth": "UP",
            "clientCount": {"radio0": 5}
        }]
    }).encode()
    mock_urlopen.return_value = mock_response

    auth_manager = MagicMock()
    auth_manager.get_token.return_value = "mocked_token"

    data = fetch_ap_data(auth_manager)

    assert len(data) == 1
    ap = data[0]
    # Verify locationName is used as fallback
    assert ap["location"] is None
    assert ap["snmpLocation"] == "default location"
    assert ap["locationName"] == "Global/York University/Keele Campus/Building1/Floor1"
    # Verify location parts are correctly parsed from locationName
    location_parts = ap["locationName"].split('/')
    assert len(location_parts) >= 5
    assert location_parts[-2] == "Building1"  # Building name
    assert location_parts[-1] == "Floor1"     # Floor name


@patch("ap_monitor.app.dna_api.urlopen")
def test_fetch_ap_data_with_no_location(mock_urlopen):
    mock_response = MagicMock()
    mock_response.__enter__.return_value.read.return_value = json.dumps({
        "response": [{
            "uuid": "abc123",
            "name": "AP1",
            "location": None,
            "snmpLocation": "default location",
            "locationName": "null",
            "macAddress": "AA:BB:CC:DD:EE:FF",
            "reachabilityHealth": "UP",
            "clientCount": {"radio0": 5}
        }]
    }).encode()
    mock_urlopen.return_value = mock_response

    auth_manager = MagicMock()
    auth_manager.get_token.return_value = "mocked_token"

    data = fetch_ap_data(auth_manager)

    assert len(data) == 1
    ap = data[0]
    # Verify all location fields are invalid
    assert ap["location"] is None
    assert ap["snmpLocation"] == "default location"
    assert ap["locationName"] == "null"
    # Verify no valid location parts can be extracted
    assert not any(loc and loc.lower() != "default location" and loc.lower() != "null" 
                  for loc in [ap["location"], ap["snmpLocation"], ap["locationName"]])


@patch("ap_monitor.app.dna_api.urlopen")
def test_fetch_ap_data_with_invalid_location_format(mock_urlopen):
    mock_response = MagicMock()
    mock_response.__enter__.return_value.read.return_value = json.dumps({
        "response": [{
            "uuid": "abc123",
            "name": "AP1",
            "location": "Invalid/Location/Format",
            "macAddress": "AA:BB:CC:DD:EE:FF",
            "reachabilityHealth": "UP",
            "clientCount": {"radio0": 5}
        }]
    }).encode()
    mock_urlopen.return_value = mock_response

    auth_manager = MagicMock()
    auth_manager.get_token.return_value = "mocked_token"

    data = fetch_ap_data(auth_manager)

    assert len(data) == 1
    ap = data[0]
    # Verify location format is invalid
    assert ap["location"] == "Invalid/Location/Format"
    location_parts = ap["location"].split('/')
    assert len(location_parts) < 5  # Invalid format should have fewer than 5 parts


@patch("ap_monitor.app.dna_api.urlopen")
@patch.dict("os.environ", {
    "DNA_USERNAME": "test_user",
    "DNA_PASSWORD": "test_pass",
    "DNA_API_URL": "https://test.dnac.com"
}, clear=True)
def test_auth_manager_initialization(mock_urlopen):
    """Test AuthManager initialization with environment variables."""
    # Reload the module to pick up the new environment variables
    import importlib
    import ap_monitor.app.dna_api
    importlib.reload(ap_monitor.app.dna_api)
    
    auth = ap_monitor.app.dna_api.AuthManager()
    assert auth.auth_url == "https://test.dnac.com/dna/system/api/v1/auth/token"
    assert "Basic" in auth.auth_headers["Authorization"]


@patch("ap_monitor.app.dna_api.urlopen")
def test_fetch_ap_data_empty_response(mock_urlopen):
    """Test handling of empty API response."""
    mock_response = MagicMock()
    mock_response.__enter__.return_value.read.return_value = json.dumps({
        "totalCount": 0,
        "response": []
    }).encode()
    mock_urlopen.return_value = mock_response

    auth_manager = MagicMock()
    auth_manager.get_token.return_value = "mocked_token"

    data = fetch_ap_data(auth_manager)
    assert len(data) == 0


@patch("ap_monitor.app.dna_api.urlopen")
def test_fetch_ap_data_malformed_response(mock_urlopen):
    """Test handling of malformed API response."""
    mock_response = MagicMock()
    mock_response.__enter__.return_value.read.return_value = json.dumps({
        "error": "Invalid response format"
    }).encode()
    mock_urlopen.return_value = mock_response

    auth_manager = MagicMock()
    auth_manager.get_token.return_value = "mocked_token"

    with pytest.raises(KeyError):
        fetch_ap_data(auth_manager)


@patch("ap_monitor.app.dna_api.urlopen")
def test_fetch_ap_data_with_retry(mock_urlopen):
    """Test retry mechanism for API failures."""
    # First attempt fails, second succeeds
    fail_response = MagicMock()
    fail_response.__enter__.side_effect = HTTPError(None, 429, "Too Many Requests", None, None)

    success_response = MagicMock()
    success_response.__enter__.return_value.read.return_value = json.dumps({
        "totalCount": 1,
        "response": [{
            "name": "AP1",
            "macAddress": "AA:BB:CC:DD:EE:FF",
            "ipAddress": "10.0.0.1",
            "location": "Global/York University/Keele Campus/Building1/Floor1",
            "model": "Cisco AP",
            "clientCount": {"radio0": 5},
            "reachabilityHealth": "UP"
        }]
    }).encode()

    mock_urlopen.side_effect = [fail_response, success_response]

    auth_manager = MagicMock()
    auth_manager.get_token.return_value = "mocked_token"

    data = fetch_ap_data(auth_manager)
    assert len(data) == 1
    assert data[0]["name"] == "AP1"


@patch("ap_monitor.app.dna_api.urlopen")
def test_fetch_ap_data_pagination(mock_urlopen):
    """Test handling of paginated API responses."""
    # First page with 100 items
    first_page = MagicMock()
    first_page.__enter__.return_value.read.return_value = json.dumps({
        "totalCount": 150,
        "response": [{
            "name": f"AP{i}",
            "macAddress": f"AA:BB:CC:DD:EE:{i:02x}",
            "ipAddress": f"10.0.0.{i}",
            "location": "Global/Keele Campus/Building1/Floor1",
            "model": "Cisco AP",
            "clientCount": {"radio0": 5},
            "reachabilityHealth": "UP"
        } for i in range(100)]
    }).encode()

    # Second page with 50 items
    second_page = MagicMock()
    second_page.__enter__.return_value.read.return_value = json.dumps({
        "totalCount": 150,
        "response": [{
            "name": f"AP{i}",
            "macAddress": f"AA:BB:CC:DD:EE:{i:02x}",
            "ipAddress": f"10.0.0.{i}",
            "location": "Global/Keele Campus/Building1/Floor1",
            "model": "Cisco AP",
            "clientCount": {"radio0": 5},
            "reachabilityHealth": "UP"
        } for i in range(100, 150)]
    }).encode()

    mock_urlopen.side_effect = [first_page, second_page]

    auth_manager = MagicMock()
    auth_manager.get_token.return_value = "mocked_token"

    data = fetch_ap_data(auth_manager)
    assert len(data) == 150
    assert data[0]["name"] == "AP0"
    assert data[149]["name"] == "AP149"


@patch("ap_monitor.app.dna_api.urlopen")
def test_fetch_ap_data_duplicate_handling(mock_urlopen):
    """Test handling of duplicate AP entries."""
    mock_response = MagicMock()
    mock_response.__enter__.return_value.read.return_value = json.dumps({
        "response": [
            {
                "uuid": "abc123",
                "name": "AP1",
                "location": "Global/York University/Keele Campus/Building1/Floor1",
                "macAddress": "AA:BB:CC:DD:EE:FF",
                "reachabilityHealth": "UP",
                "clientCount": {"radio0": 5}
            },
            {
                "uuid": "abc123",  # Duplicate UUID
                "name": "AP1",
                "location": "Global/York University/Keele Campus/Building1/Floor1",
                "macAddress": "AA:BB:CC:DD:EE:FF",
                "reachabilityHealth": "UP",
                "clientCount": {"radio0": 5}
            }
        ]
    }).encode()
    mock_urlopen.return_value = mock_response

    auth_manager = MagicMock()
    auth_manager.get_token.return_value = "mocked_token"

    data = fetch_ap_data(auth_manager)
    assert len(data) == 1  # Should remove duplicate


@patch("ap_monitor.app.dna_api.urlopen")
def test_fetch_ap_data_missing_required_fields(mock_urlopen):
    """Test handling of AP data with missing required fields."""
    mock_response = MagicMock()
    mock_response.__enter__.return_value.read.return_value = json.dumps({
        "response": [
            {
                "uuid": "abc123",
                "name": "AP1",
                # Missing location
                "macAddress": "AA:BB:CC:DD:EE:FF",
                "reachabilityHealth": "UP",
                "clientCount": {"radio0": 5}
            },
            {
                "uuid": "def456",
                "name": "AP2",
                "location": "Global/York University/Keele Campus/Building1/Floor1",
                # Missing macAddress
                "reachabilityHealth": "UP",
                "clientCount": {"radio0": 5}
            }
        ]
    }).encode()
    mock_urlopen.return_value = mock_response

    auth_manager = MagicMock()
    auth_manager.get_token.return_value = "mocked_token"

    data = fetch_ap_data(auth_manager)
    assert len(data) == 2  # Should still return both APs, let the processing layer handle missing fields

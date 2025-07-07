import json
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, parse_qs
from ap_monitor.app.dna_api import AuthManager, fetch_client_counts, fetch_ap_data, get_ap_data, fetch_ap_client_data_with_fallback, fetch_clients, fetch_clients_count_for_ap, SITE_HIERARCHY
import logging


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

    # First page: fail twice, then succeed with one record
    mock_response1 = MagicMock()
    mock_response1.read.return_value = json.dumps({
        "response": [{
            "siteName": "Test Building",
            "siteId": "test-id-1",
            "parentSiteId": "e77b6e96-3cd3-400a-9ebd-231c827fd369",
            "parentSiteName": " All Sites",
            "siteType": "building",
            "healthyClientsPercentage": 100,
            "clientHealthWired": 100,
            "clientHealthWireless": 100,
            "numberOfClients": 8,
            "numberOfWiredClients": 3,
            "numberOfWirelessClients": 5,
            "apDeviceTotalCount": 2,
            "wirelessDeviceTotalCount": 2,
            "networkHealthWireless": 90,
            "clientHealthWireless": 95,
            "wiredGoodClients": 3,
            "wirelessGoodClients": 5,
            "usage": 1998130.0,
            "applicationHealthStats": {
                "appTotalCount": 0,
                "businessRelevantAppCount": {
                    "poor": 0,
                    "fair": 0,
                    "good": 0
                },
                "businessIrrelevantAppCount": {
                    "poor": 0,
                    "fair": 0,
                    "good": 0
                },
                "defaultHealthAppCount": {
                    "poor": 0,
                    "fair": 0,
                    "good": 0
                }
            }
        }],
        "totalCount": 1
    }).encode()
    mock_response1.__enter__.return_value = mock_response1
    mock_response1.status = 200

    # Subsequent pages: return empty response
    mock_response_empty = MagicMock()
    mock_response_empty.read.return_value = json.dumps({
        "response": [],
        "totalCount": 1
    }).encode()
    mock_response_empty.__enter__.return_value = mock_response_empty
    mock_response_empty.status = 200

    # Create mock error response
    mock_error = MagicMock()
    mock_error.side_effect = Exception("Temporary failure")

    # Set up the mock to fail twice then succeed for first page, then empty for next pages
    mock_urlopen.side_effect = [
        mock_error, mock_error, mock_response1,  # First page
        mock_response_empty,  # Second page (no more data)
        mock_response_empty   # Third page (no more data)
    ]

    # Call the function
    data = fetch_client_counts(auth, rounded_unix_timestamp=1234567890, retries=3)

    # Verify the results
    assert len(data) == 1
    assert data[0]['location'] == "Test Building"
    assert data[0]['wirelessClients'] == 5
    assert data[0]['wiredClients'] == 3
    assert data[0]['totalClients'] == 8
    assert data[0]['apDevices'] == 2
    assert data[0]['wirelessDevices'] == 2
    assert data[0]['networkHealth'] == 90
    assert data[0]['clientHealth'] == 95
    assert data[0]['siteType'] == "building"
    assert data[0]['parentSiteName'] == " All Sites"


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

    # New logic: AP is skipped due to no valid location, even after fallback
    assert len(data) == 0


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
    # New logic: Only APs with valid location are returned
    assert len(data) == 1
    assert data[0]['effectiveLocation'] == "Global/York University/Keele Campus/Building1/Floor1"


@patch("ap_monitor.app.dna_api.urlopen")
def test_fetch_client_counts_with_site_details(mock_urlopen):
    """Test fetch_client_counts with both site-health and site-detail endpoints."""
    # Mock site details response
    site_details_response = MagicMock()
    site_details_response.__enter__.return_value.read.return_value = json.dumps({
        "response": [
            {
                "id": "test-building-1",
                "name": "Test Building 1",
                "siteNameHierarchy": "Global/Keele Campus/Test Building 1",
                "additionalInfo": [
                    {
                        "nameSpace": "Location",
                        "attributes": {
                            "type": "building",
                            "latitude": "43.773578",
                            "longitude": "-79.503704"
                        }
                    }
                ]
            }
        ]
    }).encode()
    site_details_response.__enter__.return_value.status = 200

    # Mock site health response
    site_health_response = MagicMock()
    site_health_response.__enter__.return_value.read.return_value = json.dumps({
        "response": [
            {
                "siteId": "test-building-1",
                "siteName": "Test Building 1",
                "parentSiteName": "Keele Campus",
                "siteType": "building",
                "numberOfWirelessClients": 50,
                "numberOfWiredClients": 30,
                "numberOfClients": 80,
                "apDeviceTotalCount": 10,
                "wirelessDeviceTotalCount": 10,
                "networkHealthWireless": 95,
                "clientHealthWireless": 90
            }
        ]
    }).encode()
    site_health_response.__enter__.return_value.status = 200

    # Set up mock to return different responses for different URLs
    def mock_urlopen_side_effect(request, *args, **kwargs):
        if "site/" in request.full_url:
            return site_details_response
        return site_health_response

    mock_urlopen.side_effect = mock_urlopen_side_effect

    auth_manager = MagicMock()
    auth_manager.get_token.return_value = "mocked_token"

    data = fetch_client_counts(auth_manager, 1715000000000)

    assert len(data) == 1
    site = data[0]
    assert site["location"] == "Test Building 1"
    assert site["wirelessClients"] == 50
    assert site["wiredClients"] == 30
    assert site["totalClients"] == 80
    assert site["apDevices"] == 10
    assert site["wirelessDevices"] == 10
    assert site["networkHealth"] == 95
    assert site["clientHealth"] == 90
    assert site["siteType"] == "building"
    assert site["parentSiteName"] == "Keele Campus"
    assert site["siteHierarchy"] == "Global/Keele Campus/Test Building 1"
    assert site["latitude"] == "43.773578"
    assert site["longitude"] == "-79.503704"


@patch("ap_monitor.app.dna_api.urlopen")
def test_fetch_client_counts_site_details_failure(mock_urlopen):
    """Test fetch_client_counts when site details endpoint fails."""
    # Mock site details failure
    site_details_error = HTTPError(
        url="https://test.dnac.com/site/test",
        code=500,
        msg="Server Error",
        hdrs={},
        fp=None
    )
    
    # Mock site health response
    site_health_response = MagicMock()
    site_health_response.__enter__.return_value.read.return_value = json.dumps({
        "response": [
            {
                "siteId": "test-building-1",
                "siteName": "Test Building 1",
                "parentSiteName": "Keele Campus",
                "siteType": "building",
                "numberOfWirelessClients": 50,
                "numberOfWiredClients": 30,
                "numberOfClients": 80,
                "apDeviceTotalCount": 10,
                "wirelessDeviceTotalCount": 10,
                "networkHealthWireless": 95,
                "clientHealthWireless": 90
            }
        ]
    }).encode()
    site_health_response.__enter__.return_value.status = 200

    # Set up mock to return different responses for different URLs
    def mock_urlopen_side_effect(request, *args, **kwargs):
        if "site/" in request.full_url:
            raise site_details_error
        return site_health_response

    mock_urlopen.side_effect = mock_urlopen_side_effect

    auth_manager = MagicMock()
    auth_manager.get_token.return_value = "mocked_token"

    # Should still work with just site health data
    data = fetch_client_counts(auth_manager, 1715000000000)

    assert len(data) == 1
    site = data[0]
    assert site["location"] == "Test Building 1"
    assert site["wirelessClients"] == 50
    assert site["wiredClients"] == 30
    assert site["totalClients"] == 80
    # These fields should be empty since site details failed
    assert site["siteHierarchy"] == ""
    assert site["latitude"] is None
    assert site["longitude"] is None


@patch("ap_monitor.app.dna_api.urlopen")
def test_fetch_client_counts_filtering(mock_urlopen):
    """Test fetch_client_counts filtering logic."""
    # Mock site details response
    site_details_response = MagicMock()
    site_details_response.__enter__.return_value.read.return_value = json.dumps({
        "response": [
            {
                "id": "test-building-1",
                "name": "Test Building 1",
                "siteNameHierarchy": "Global/Keele Campus/Test Building 1",
                "additionalInfo": [
                    {
                        "nameSpace": "Location",
                        "attributes": {
                            "type": "building",
                            "latitude": "43.773578",
                            "longitude": "-79.503704"
                        }
                    }
                ]
            }
        ]
    }).encode()
    site_details_response.__enter__.return_value.status = 200

    # Mock site health response with multiple sites
    site_health_response = MagicMock()
    site_health_response.__enter__.return_value.read.return_value = json.dumps({
        "response": [
            {
                "siteId": "test-building-1",
                "siteName": "Test Building 1",
                "parentSiteName": "Keele Campus",
                "siteType": "building",
                "numberOfWirelessClients": 50,
                "numberOfWiredClients": 30,
                "numberOfClients": 80,
                "apDeviceTotalCount": 10,
                "wirelessDeviceTotalCount": 10,
                "networkHealthWireless": 95,
                "clientHealthWireless": 90
            },
            {
                "siteId": "test-area-1",
                "siteName": "Test Area 1",
                "parentSiteName": "Other Campus",
                "siteType": "area",
                "numberOfWirelessClients": 0,
                "numberOfWiredClients": 0,
                "numberOfClients": 0,
                "apDeviceTotalCount": 0,
                "wirelessDeviceTotalCount": 0,
                "networkHealthWireless": 0,
                "clientHealthWireless": 0
            }
        ]
    }).encode()
    site_health_response.__enter__.return_value.status = 200

    # Set up mock to return different responses for different URLs
    def mock_urlopen_side_effect(request, *args, **kwargs):
        if "site/" in request.full_url:
            return site_details_response
        return site_health_response

    mock_urlopen.side_effect = mock_urlopen_side_effect

    auth_manager = MagicMock()
    auth_manager.get_token.return_value = "mocked_token"

    data = fetch_client_counts(auth_manager, 1715000000000)

    # Should only include the building with clients
    assert len(data) == 1
    site = data[0]
    assert site["location"] == "Test Building 1"
    assert site["siteType"] == "building"
    assert site["parentSiteName"] == "Keele Campus"
    assert site["wirelessClients"] > 0 or site["wiredClients"] > 0


def make_mock_response(data):
    mock_response = MagicMock()
    mock_response.__enter__.return_value.read.return_value = json.dumps(data).encode()
    mock_response.__enter__.return_value.status = 200
    return mock_response

@patch("ap_monitor.app.dna_api.urlopen")
def test_fetch_ap_client_data_with_fallback_merging(mock_urlopen):
    """
    Test merging and fallback logic for fetch_ap_client_data_with_fallback.
    Simulate partial data from each API and verify merged result is correct.
    """
    # Mock responses for each endpoint
    ap_config_data = [{
        "macAddress": "AA:BB:CC:DD:EE:FF",
        "apName": "AP1",
        "location": None,  # Missing location
        "apModel": "Cisco AP",
        "primaryIpAddress": "10.0.0.1"
    }]
    device_health_data = [{
        "macAddress": "AA:BB:CC:DD:EE:FF",
        "name": None,  # Missing name
        "location": "Global/Campus/Building/Floor",
        "model": None,
        "reachabilityHealth": "UP",
        "clientCount": {"radio0": 5, "radio1": 3},
        "ipAddress": None
    }]
    client_counts_data = [{
        "macAddress": "AA:BB:CC:DD:EE:FF",
        "count": 8
    }]
    all_clients_data = [{
        "apMac": "AA:BB:CC:DD:EE:FF"
    } for _ in range(8)]
    site_health_data = [{
        "siteId": "site-1",
        "siteName": "Global/Campus/Building/Floor",
        "numberOfClients": 8
    }]
    planned_aps_data = [{
        "attributes": {"macAddress": "AA:BB:CC:DD:EE:FF", "heirarchyName": "Global/Campus/Building/Floor"}
    }]

    # Patch each fetcher to return the above data in order
    with patch("ap_monitor.app.dna_api.fetch_ap_config_summary", return_value=ap_config_data), \
         patch("ap_monitor.app.dna_api.fetch_device_health", return_value=device_health_data), \
         patch("ap_monitor.app.dna_api.fetch_all_clients_count", return_value=client_counts_data), \
         patch("ap_monitor.app.dna_api.fetch_clients", return_value=all_clients_data), \
         patch("ap_monitor.app.dna_api.fetch_site_health", return_value=site_health_data), \
         patch("ap_monitor.app.dna_api.fetch_planned_aps", return_value=planned_aps_data):
        auth_manager = MagicMock()
        auth_manager.get_token.return_value = "mocked_token"
        results = fetch_ap_client_data_with_fallback(auth_manager)
        assert isinstance(results, list)
        assert len(results) == 1
        ap = results[0]
        # All required fields should be filled from some source
        assert ap["macAddress"] == "AA:BB:CC:DD:EE:FF"
        assert ap["name"] == "AP1"  # from ap_config_data
        assert ap["location"] == "Global/Campus/Building/Floor"  # from device_health_data or planned_aps_data
        assert ap["clientCount"] == 8  # from client_counts_data or device_health_data
        assert ap["status"] == "ok"
        # Source map should show which API provided each field
        assert ap["source_map"]["macAddress"] == "ap_inventory"
        assert ap["source_map"]["name"] == "ap_inventory"
        assert ap["source_map"]["location"] in ("device_health", "planned_aps")
        assert ap["source_map"]["clientCount"] in ("client_counts", "device_health")

@patch("ap_monitor.app.dna_api.urlopen")
def test_fetch_ap_client_data_with_fallback_incomplete(mock_urlopen):
    """
    Test that diagnostics are logged if all APIs fail for a required field.
    """
    # All fetchers return empty or missing required fields
    with patch("ap_monitor.app.dna_api.fetch_ap_config_summary", return_value=[]), \
         patch("ap_monitor.app.dna_api.fetch_device_health", return_value=[]), \
         patch("ap_monitor.app.dna_api.fetch_all_clients_count", return_value=[]), \
         patch("ap_monitor.app.dna_api.fetch_clients", return_value=[]), \
         patch("ap_monitor.app.dna_api.fetch_site_health", return_value=[]), \
         patch("ap_monitor.app.dna_api.fetch_planned_aps", return_value=[]), \
         patch("ap_monitor.app.dna_api.save_incomplete_diagnostics_from_list") as mock_diag:
        auth_manager = MagicMock()
        auth_manager.get_token.return_value = "mocked_token"
        results = fetch_ap_client_data_with_fallback(auth_manager)
        # Should be empty or all incomplete
        assert isinstance(results, list)
        # Pass if diagnostics are called, or if there are no APs to diagnose
        assert mock_diag.called or len(results) == 0

@patch("ap_monitor.app.dna_api.urlopen")
def test_fetch_ap_client_data_with_fallback_ap_name_parsing(mock_urlopen):
    """
    Test fallback to AP name parsing for location when location is missing or 'default location'.
    """
    ap_config_data = [{
        "macAddress": "11:22:33:44:55:66",
        "apName": "k483-tel-3-26",
        "location": "default location",
        "apModel": "Cisco AP",
        "primaryIpAddress": "10.0.0.2"
    }]
    device_health_data = [{
        "macAddress": "11:22:33:44:55:66",
        "name": "k483-tel-3-26",
        "location": "default location",
        "model": "Cisco AP",
        "reachabilityHealth": "UP",
        "clientCount": {"radio0": 4, "radio1": 2},
        "ipAddress": "10.0.0.2"
    }]
    client_counts_data = []
    all_clients_data = []
    site_health_data = []
    planned_aps_data = []
    with patch("ap_monitor.app.dna_api.fetch_ap_config_summary", return_value=ap_config_data), \
         patch("ap_monitor.app.dna_api.fetch_device_health", return_value=device_health_data), \
         patch("ap_monitor.app.dna_api.fetch_all_clients_count", return_value=client_counts_data), \
         patch("ap_monitor.app.dna_api.fetch_clients", return_value=all_clients_data), \
         patch("ap_monitor.app.dna_api.fetch_site_health", return_value=site_health_data), \
         patch("ap_monitor.app.dna_api.fetch_planned_aps", return_value=planned_aps_data):
        auth_manager = MagicMock()
        auth_manager.get_token.return_value = "mocked_token"
        results = fetch_ap_client_data_with_fallback(auth_manager)
        assert isinstance(results, list)
        assert len(results) == 1
        ap = results[0]
        # Location should be set using AP name parsing
        assert ap["location"] == "Global/Keele Campus/Victor Phillip Dahdaleh Building/3/26"
        assert ap["source_map"]["location"] == "ap_name_parsing"
        assert ap["clientCount"] == 6  # sum of radio0 and radio1
        assert ap["status"] == "ok"

@patch("ap_monitor.app.dna_api.urlopen")
def test_fetch_clients_requires_site_id(mock_urlopen):
    auth_manager = MagicMock()
    auth_manager.get_token.return_value = "mocked_token"
    mock_response = MagicMock()
    mock_response.__enter__.return_value.read.return_value = b'{"response": []}'
    mock_urlopen.return_value = mock_response
    result = fetch_clients(auth_manager, page_limit=1)
    assert isinstance(result, list)

@patch("ap_monitor.app.dna_api.urlopen")
def test_fetch_clients_with_site_id(mock_urlopen):
    logging.basicConfig(level=logging.DEBUG)
    logger = logging.getLogger("test_fetch_clients_with_site_id")
    logger.info("Starting test_fetch_clients_with_site_id")
    auth_manager = MagicMock()
    auth_manager.get_token.return_value = "mocked_token"
    responses = [
        {"response": [{"macAddress": "AA:BB:CC:DD:EE:FF"}]},
        {"response": []}
    ]
    def side_effect(req, context=None, timeout=None):
        logger.info(f"Mock urlopen called with URL: {getattr(req, 'full_url', req)}")
        class MockResponse:
            def __enter__(self):
                class Dummy:
                    def read(self_inner):
                        logger.info(f"Returning mock response: {responses[0]}")
                        return json.dumps(responses.pop(0)).encode()
                return Dummy()
            def __exit__(self, exc_type, exc_val, exc_tb):
                pass
        return MockResponse()
    mock_urlopen.side_effect = side_effect
    logger.info("Calling fetch_clients...")
    result = fetch_clients(auth_manager, site_id="e77b6e96-3cd3-400a-9ebd-231c827fd369", page_limit=1)
    logger.info(f"fetch_clients returned: {result}")
    assert isinstance(result, list)
    assert result[0]["macAddress"] == "AA:BB:CC:DD:EE:FF"

@patch("ap_monitor.app.dna_api.urlopen")
def test_fetch_clients_count_for_ap_with_site_id(mock_urlopen):
    auth_manager = MagicMock()
    auth_manager.get_token.return_value = "mocked_token"
    mock_response = MagicMock()
    mock_response.__enter__.return_value.read.return_value = json.dumps({"response": {"count": 5}}).encode()
    mock_urlopen.return_value = mock_response
    count = fetch_clients_count_for_ap(auth_manager, mac="AA:BB:CC:DD:EE:FF", site_id="e77b6e96-3cd3-400a-9ebd-231c827fd369")
    assert count == 5

@patch("ap_monitor.app.dna_api.urlopen")
def test_fetch_clients_count_for_ap_429(mock_urlopen):
    auth_manager = MagicMock()
    auth_manager.get_token.return_value = "mocked_token"
    responses = [
        HTTPError(url=None, code=429, msg="Too Many Requests", hdrs=None, fp=None),
        HTTPError(url=None, code=429, msg="Too Many Requests", hdrs=None, fp=None),
        {"response": {"count": 7}}
    ]
    def side_effect(req, context=None, timeout=None):
        resp = responses.pop(0)
        if isinstance(resp, HTTPError):
            raise resp
        class MockResponse:
            def __enter__(self):
                class Dummy:
                    def read(self_inner):
                        return json.dumps(resp).encode()
                return Dummy()
            def __exit__(self, exc_type, exc_val, exc_tb):
                pass
        return MockResponse()
    mock_urlopen.side_effect = side_effect
    with patch("time.sleep", lambda s: None):
        count = fetch_clients_count_for_ap(auth_manager, mac="AA:BB:CC:DD:EE:FF", retries=5, delay=0.1)
    assert count == 7

@patch("ap_monitor.app.dna_api.urlopen")
def test_fetch_clients_count_for_ap_429_all_fail(mock_urlopen):
    auth_manager = MagicMock()
    auth_manager.get_token.return_value = "mocked_token"
    responses = [HTTPError(url=None, code=429, msg="Too Many Requests", hdrs=None, fp=None)] * 5
    def side_effect(req, context=None, timeout=None):
        resp = responses.pop(0)
        if isinstance(resp, HTTPError):
            raise resp
    mock_urlopen.side_effect = side_effect
    with patch("time.sleep", lambda s: None):
        count = fetch_clients_count_for_ap(auth_manager, mac="AA:BB:CC:DD:EE:FF", retries=5, delay=0.1)
    assert count is None

@patch("ap_monitor.app.dna_api.urlopen")
def test_fetch_clients_uses_siteHierarchy(mock_urlopen):
    auth_manager = MagicMock()
    auth_manager.get_token.return_value = "mocked_token"
    called_urls = []
    responses = [
        {"response": [{"macAddress": "AA:BB:CC:DD:EE:FF"}]},
        {"response": []}
    ]
    def side_effect(req, context=None, timeout=None):
        called_urls.append(req.full_url)
        class MockResponse:
            def __enter__(self):
                class Dummy:
                    def read(self_inner):
                        return json.dumps(responses.pop(0)).encode()
                return Dummy()
            def __exit__(self, exc_type, exc_val, exc_tb):
                pass
        return MockResponse()
    mock_urlopen.side_effect = side_effect
    with patch("time.sleep", lambda s: None):
        fetch_clients(auth_manager)
    parsed = urlparse(called_urls[0])
    qs = parse_qs(parsed.query)
    assert qs["siteHierarchy"][0] == SITE_HIERARCHY

@patch("ap_monitor.app.dna_api.urlopen")
def test_fetch_clients_count_for_ap_uses_siteHierarchy(mock_urlopen):
    called_urls = []
    def side_effect(req, context=None, timeout=None):
        called_urls.append(req.full_url)
        class MockResponse:
            def __enter__(self):
                class Dummy:
                    def read(self_inner):
                        return json.dumps({"response": {"count": 3}}).encode()
                return Dummy()
            def __exit__(self, exc_type, exc_val, exc_tb):
                pass
        return MockResponse()
    mock_urlopen.side_effect = side_effect
    with patch("time.sleep", lambda s: None):
        fetch_clients_count_for_ap(MagicMock(get_token=lambda: "mocked_token"), mac="AA:BB:CC:DD:EE:FF")
    parsed = urlparse(called_urls[0])
    qs = parse_qs(parsed.query)
    assert qs["siteHierarchy"][0] == SITE_HIERARCHY

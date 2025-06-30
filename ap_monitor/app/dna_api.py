import base64
import json
import ssl
import os
import time
from datetime import datetime, timedelta
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from dotenv import load_dotenv
from ap_monitor.app.db import APClientSessionLocal
from ap_monitor.app.utils import setup_logging
from ap_monitor.app.diagnostics import save_incomplete_diagnostics_from_list

# Load environment variables
load_dotenv()

# Configure logger
logger = setup_logging()

# Create SSL context that doesn't verify certificates
ssl_context = ssl._create_unverified_context()

# DNA Center API configuration
BASE_URL = os.getenv("DNA_API_URL", "https://dnac11.netops.yorku.ca")
AUTH_URL = BASE_URL + "/dna/system/api/v1/auth/token"
SITE_HEALTH_URL = BASE_URL + "/dna/intent/api/v1/site-health"
DEVICE_HEALTH_URL = BASE_URL + "/dna/intent/api/v1/device-health"
NETWORK_DEVICE_URL = BASE_URL + "/dna/intent/api/v1/network-device"
SITE_MEMBERSHIP_URL = BASE_URL + "/dna/intent/api/v1/membership/{siteId}"
KEELE_CAMPUS_SITE_ID = 'e77b6e96-3cd3-400a-9ebd-231c827fd369'

# Mapping of radio keys to radio IDs
radio_id_map = {'radio0': 1, 'radio1': 2, 'radio2': 3}

# Authentication credentials
username = os.getenv("DNA_USERNAME")
password = os.getenv("DNA_PASSWORD")

if not username or not password:
    logger.error("DNA_USERNAME or DNA_PASSWORD not set in .env file")
    raise ValueError("DNA_USERNAME and DNA_PASSWORD must be set in .env file")

# Create basic auth credentials
credentials = f"{username}:{password}"
encoded_credentials = base64.b64encode(credentials.encode()).decode()

AUTH_HEADERS = {
    'Authorization': 'Basic ' + encoded_credentials,
    'Content-Type': 'application/json'
}

class AuthManager:
    """Manages authentication token for DNA Center API."""
    
    def __init__(self, auth_url=AUTH_URL, auth_headers=AUTH_HEADERS):
        self.auth_url = auth_url
        self.auth_headers = auth_headers
        self.token = None
        self.token_expiry = None
        self.last_refresh_time = None
        self.min_refresh_interval = 30  # Minimum seconds between token refreshes
        logger.info(f"Initializing AuthManager with URL: {auth_url}")
        logger.info(f"Auth headers (excluding credentials): {dict(filter(lambda x: x[0] != 'Authorization', auth_headers.items()))}")
    
    def get_token(self, force_refresh=False):
        """Get a valid authentication token, refreshing if necessary."""
        current_time = datetime.now()
        
        # Check if we need to wait before refreshing
        if self.last_refresh_time:
            time_since_last_refresh = (current_time - self.last_refresh_time).total_seconds()
            if time_since_last_refresh < self.min_refresh_interval:
                wait_time = self.min_refresh_interval - time_since_last_refresh
                logger.info(f"Waiting {wait_time:.1f} seconds before refreshing token...")
                time.sleep(wait_time)
        
        if not self.token or not self.token_expiry or current_time >= self.token_expiry - timedelta(minutes=5) or force_refresh:
            logger.info("Refreshing authentication token")
            req = Request(self.auth_url, headers=self.auth_headers, method='POST')
            try:
                with urlopen(req, context=ssl_context) as response:
                    if response.status == 200:
                        response_data = json.load(response)
                        self.token = response_data.get("Token")
                        if not self.token:
                            logger.error("No token in response data")
                            logger.error(f"Response data: {response_data}")
                            raise Exception("No token in response data")
                        self.token_expiry = current_time + timedelta(minutes=55)
                        self.last_refresh_time = current_time
                        logger.info("Authentication token successfully refreshed")
                        logger.debug(f"Token expiry set to: {self.token_expiry}")
                    else:
                        logger.error(f"Failed to obtain access token. Status: {response.status}")
                        raise Exception(f"Failed to obtain access token: {response.status}")
            except HTTPError as e:
                logger.error(f"HTTP Error while obtaining access token: {e.code} - {e.reason}")
                raise Exception(f"Failed to obtain access token: {e.reason}")
            except URLError as e:
                logger.error(f"URL Error while obtaining access token: {e.reason}")
                raise Exception(f"Failed to obtain access token: {e.reason}")
            except Exception as e:
                logger.error(f"Unexpected error while obtaining access token: {str(e)}")
                raise
        return self.token

def fetch_client_counts(auth_manager, rounded_unix_timestamp, retries=3):
    """
    Fetch wireless client count data from DNA Center API using both site-health and site-detail endpoints.
    
    Args:
        auth_manager: AuthManager instance for token management
        rounded_unix_timestamp: Timestamp for the API query
        retries: Number of retries for failed requests
        
    Returns:
        List of site data with client counts
    """
    token = auth_manager.get_token()
    auth_headers = {'x-auth-token': token}
    data = []
    
    # First get the site details to get building hierarchy
    site_detail_url = f"{BASE_URL}/dna/intent/api/v1/site/{KEELE_CAMPUS_SITE_ID}"
    building_map = {}
    
    try:
        logger.info("Fetching site details for building hierarchy")
        req = Request(site_detail_url, headers=auth_headers)
        with urlopen(req, context=ssl_context, timeout=60) as response:
            site_details = json.load(response)
            
            # Process site details to create building map
            for site in site_details.get('response', []):
                if site.get('additionalInfo'):
                    for info in site['additionalInfo']:
                        if info.get('nameSpace') == 'Location':
                            attrs = info.get('attributes', {})
                            if attrs.get('type') == 'building':
                                building_map[site['id']] = {
                                    'name': site['name'],
                                    'hierarchy': site.get('siteNameHierarchy', ''),
                                    'latitude': attrs.get('latitude'),
                                    'longitude': attrs.get('longitude')
                                }
    except Exception as e:
        logger.error(f"Error fetching site details: {e}")
        # Continue with site health data even if site details fail
    
    # Now get the site health data
    site_health_url = f"{BASE_URL}/dna/intent/api/v1/site-health"
    processed_sites = set()  # Track processed site IDs to avoid duplicates
    
    # First request to get total count
    params = {
        "siteId": KEELE_CAMPUS_SITE_ID,
        "limit": 50,
        "offset": 1
    }
    query_string = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{site_health_url}?{query_string}"
    
    req = Request(url, headers=auth_headers)
    attempt = 0
    
    while attempt < retries:
        try:
            logger.info(f"Starting API request with offset 1")
            with urlopen(req, context=ssl_context, timeout=60) as response:
                response_data = json.load(response)
                logger.info(f"API request completed successfully")
                
                if 'response' not in response_data:
                    logger.error(f"Missing 'response' in API response: {response_data}")
                    raise KeyError("Missing 'response' in API response")
                
                # Process the site data
                for site in response_data.get('response', []):
                    site_id = site.get('siteId')
                    site_name = site.get('siteName', '')
                    
                    if not site_name or site_id in processed_sites:
                        continue
                    
                    processed_sites.add(site_id)
                    
                    # Get client counts
                    wireless_clients = site.get('numberOfWirelessClients', 0) or 0
                    wired_clients = site.get('numberOfWiredClients', 0) or 0
                    total_clients = site.get('numberOfClients', 0) or 0
                    
                    # Get device counts
                    ap_devices = site.get('apDeviceTotalCount', 0) or 0
                    wireless_devices = site.get('wirelessDeviceTotalCount', 0) or 0
                    
                    # Get health metrics
                    network_health = site.get('networkHealthWireless', 0) or 0
                    client_health = site.get('clientHealthWireless', 0) or 0
                    
                    # Get site hierarchy info
                    site_type = site.get('siteType', '')
                    parent_site = site.get('parentSiteName', '')
                    
                    # Get additional building info if available
                    building_info = building_map.get(site_id, {})
                    
                    # Create a site record with all available data
                    processed_site = {
                        'location': site_name,
                        'clientCount': wireless_clients,
                        'timestamp': rounded_unix_timestamp,
                        'wiredClients': wired_clients,
                        'wirelessClients': wireless_clients,
                        'totalClients': total_clients,
                        'apDevices': ap_devices,
                        'wirelessDevices': wireless_devices,
                        'networkHealth': network_health,
                        'clientHealth': client_health,
                        'siteType': site_type,
                        'parentSiteName': parent_site,
                        'siteHierarchy': building_info.get('hierarchy', ''),
                        'latitude': building_info.get('latitude'),
                        'longitude': building_info.get('longitude')
                    }
                    data.append(processed_site)
            break
        except HTTPError as e:
            if e.code == 429:  # Too Many Requests
                attempt += 1
                if attempt >= retries:
                    logger.error(f"Failed after {retries} attempts due to rate limiting")
                    raise
                
                delay = 60 * (2 ** (attempt - 1))
                logger.warning(f"Rate limit hit. Waiting {delay} seconds before retry... (Attempt {attempt}/{retries})")
                time.sleep(delay)
                continue
            else:
                logger.error(f"HTTP Error: {e}")
                raise
        except Exception as e:
            attempt += 1
            logger.warning(f"API request error (attempt {attempt}): {e}")
            if attempt >= retries:
                logger.error(f"Failed after {retries} attempts: {e}")
            time.sleep(2 ** attempt)
    
    # Filter data to include only relevant buildings
    filtered_data = []
    for site in data:
        location = str(site.get('location', '')).lower()
        parent_site = str(site.get('parentSiteName', '')).lower()
        site_type = str(site.get('siteType', '')).lower()
        
        # Include sites that:
        # 1. Are buildings (site_type == 'building')
        # 2. Have 'keele', 'york', or 'campus' in their name
        # 3. Are part of the main campus (parent site contains 'all sites')
        # 4. Have actual client counts (wireless or wired)
        if ((site_type == 'building' or 
             any(keyword in location for keyword in ['keele', 'york', 'campus']) or
             'all sites' in parent_site) and
            (site.get('wirelessClients', 0) > 0 or site.get('wiredClients', 0) > 0)):
            filtered_data.append(site)
    
    logger.info(f"Retrieved {len(filtered_data)} buildings with client count data")
    if len(filtered_data) == 0:
        logger.warning("No buildings found with client count data. Raw data sample:")
        for site in data[:3]:
            logger.warning(f"Sample site data: {json.dumps(site, indent=2)}")
    
    return filtered_data

def test_api_connection():
    """Test the API connection and return detailed information about the response."""
    auth_manager = AuthManager()
    try:
        token = auth_manager.get_token()
        if not token:
            return {"status": "error", "message": "Failed to obtain authentication token"}
        
        # Test the device health endpoint
        auth_headers = {
            'x-auth-token': token,
            'Content-Type': 'application/json'
        }
        
        params = {
            "deviceRole": "AP",
            "siteId": KEELE_CAMPUS_SITE_ID,
            "limit": 1,
            "offset": 1
        }
        
        query_string = urlencode(params)
        test_url = f"{DEVICE_HEALTH_URL}?{query_string}"
        req = Request(test_url, headers=auth_headers)
        
        with urlopen(req, context=ssl_context, timeout=60) as response:
            response_data = response.read().decode('utf-8')
            device_info = json.loads(response_data)
            
            return {
                "status": "success",
                "response": device_info,
                "headers": dict(response.getheaders()),
                "status_code": response.status
            }
            
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "type": type(e).__name__
        }

def fetch_ap_data(auth_manager, timestamp=None, clients_data=None):
    """
    Fetch AP data from DNA Center API with rate limit handling and fallback to client data for location.
    """
    logger.info("Starting AP data fetch")
    
    all_devices = []
    offset = 1
    limit = 25  # Reduced from 50 to avoid rate limits
    total_count = None
    retry_count = 0
    max_retries = 3
    base_delay = 30  # Start with 30 seconds delay
    
    while True:
        # Build request parameters
        params = {
            "deviceRole": "AP",
            "siteId": KEELE_CAMPUS_SITE_ID,
            "limit": limit,
            "offset": offset
        }
        
        query_string = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{BASE_URL}/dna/intent/api/v1/device-health?{query_string}"
        
        try:
            # Get fresh token for each request
            token = auth_manager.get_token()
            auth_headers = {
                'x-auth-token': token,
                'Content-Type': 'application/json'
            }
            
            req = Request(url, headers=auth_headers)
            with urlopen(req, context=ssl_context) as response:
                response_data = response.read().decode('utf-8')
                data = json.loads(response_data)
                
                if 'response' not in data:
                    raise KeyError("Missing 'response' in API response")
                
                if total_count is None:
                    total_count = data.get('totalCount', 0)
                    logger.info(f"Total devices available: {total_count}")
                
                devices = data.get('response', [])
                if not devices:
                    break
                
                all_devices.extend(devices)
                
                if len(all_devices) >= total_count:
                    break
                
                offset += limit
                # Add delay between requests to avoid rate limits
                time.sleep(5)  # 5 seconds between requests
                retry_count = 0  # Reset retry count on successful request
                
        except HTTPError as e:
            if e.code == 429:  # Too Many Requests
                retry_count += 1
                if retry_count > max_retries:
                    logger.error(f"Failed after {max_retries} retries due to rate limiting")
                    raise
                
                delay = base_delay * (2 ** (retry_count - 1))  # Exponential backoff
                logger.warning(f"Rate limit hit. Waiting {delay} seconds before retry... (Attempt {retry_count}/{max_retries})")
                time.sleep(delay)
                continue
            else:
                logger.error(f"HTTP Error: {e}")
                raise
        except Exception as e:
            logger.error(f"Error fetching AP data: {e}")
            raise
    
    logger.info(f"Retrieved {len(all_devices)} devices")
    
    # Build a lookup for AP MAC -> fallback location from clients_data
    mac_to_fallback_location = {}
    if clients_data:
        for client in clients_data:
            ap_mac = client.get('apMac') or client.get('connectedNetworkDeviceMac')
            site_hierarchy = client.get('siteHierarchy')
            if ap_mac and site_hierarchy and ap_mac not in mac_to_fallback_location:
                mac_to_fallback_location[ap_mac.upper()] = site_hierarchy
    
    processed_devices = []
    seen_macs = {}  # Track unique MAC addresses with their latest data
    
    for device in all_devices:
        try:
            # Get location with fallback
            original_location = device.get("location")
            snmp_location = device.get("snmpLocation")
            location_name = device.get("locationName")
            mac_address = device.get("macAddress", "Unknown")
            mac_upper = mac_address.upper() if mac_address else None
            
            # Determine effective location
            effective_location = original_location
            if not effective_location or len(effective_location.split('/')) < 2:
                if snmp_location and snmp_location.lower() != 'default location' and snmp_location.strip():
                    effective_location = snmp_location
                elif location_name and location_name.strip().lower() != 'null':
                    effective_location = location_name
            # Fallback: use client data if still missing/invalid
            if (not effective_location or len(effective_location.split('/')) < 2) and mac_upper in mac_to_fallback_location:
                effective_location = mac_to_fallback_location[mac_upper]
                logger.info(f"Used fallback location from client data for AP {device.get('name', 'Unknown')} ({mac_address}): {effective_location}")
            # If still missing, skip
            if not effective_location or len(effective_location.split('/')) < 2:
                logger.warning(f"Skipping AP {device.get('name', 'Unknown')} ({mac_address}) due to invalid location (even after fallback): {effective_location}")
                continue
            # Create processed device
            processed_device = {
                "name": device.get("name", "Unknown"),
                "macAddress": mac_address,
                "ipAddress": device.get("ipAddress", "Unknown"),
                "location": original_location,  # Keep original location
                "effectiveLocation": effective_location,  # Add effective location
                "model": device.get("model", "Unknown"),
                "clientCount": device.get("clientCount", {}),
                "reachabilityHealth": device.get("reachabilityHealth", "UNKNOWN"),
                "snmpLocation": snmp_location,
                "locationName": location_name
            }
            # Always deduplicate by MAC address, keeping the latest data
            seen_macs[mac_address] = processed_device
        except Exception as e:
            logger.error(f"Error processing device {device.get('name', 'Unknown')}: {e}")
            continue
    # Convert the dictionary values to a list
    processed_devices = list(seen_macs.values())
    return processed_devices

def get_ap_data(auth_manager=None, retries=3):
    """
    Fetch basic access point data from DNA Center API.
    
    Args:
        auth_manager: Optional AuthManager instance
        retries: Number of retries for failed requests
        
    Returns:
        List of AP data with basic information
    """
    if auth_manager is None:
        auth_manager = AuthManager()
    
    token = auth_manager.get_token()
    auth_headers = {'x-auth-token': token}
    
    req = Request(NETWORK_DEVICE_URL, headers=auth_headers)
    attempt = 0
    
    while attempt < retries:
        try:
            logger.info("Fetching network device data from DNA Center API")
            with urlopen(req, context=ssl_context, timeout=60) as response:
                response_data = json.load(response)
                devices = response_data.get('response', [])
                
                # Filter for access points
                ap_data = []
                for device in devices:
                    if "AP" in device.get("type", ""):
                        ap_data.append({
                            "name": device.get("hostname", "Unknown"),
                            "macAddress": device.get("macAddress", ""),
                            "ipAddress": device.get("managementIpAddress", ""),
                            "model": device.get("platformId", "Unknown"),
                            "reachabilityHealth": device.get("reachabilityStatus", "Unknown"),
                            "clients": device.get("clientCount", 0)
                        })
                
                logger.info(f"Successfully fetched {len(ap_data)} access points")
                return ap_data
                
        except Exception as e:
            attempt += 1
            logger.warning(f"Error fetching AP data (attempt {attempt}): {e}")
            if attempt >= retries:
                logger.error(f"Failed to fetch AP data after {retries} attempts: {e}")
                raise
            time.sleep(2 ** attempt)  # Exponential backoff

def insert_apclientcount_data(device_info_list, timestamp, session=None):
    """Insert AP and client count data into the database."""
    from ap_monitor.app.models import ApBuilding, Floor, Room, AccessPoint, ClientCountAP, RadioType
    close_session = False
    if session is None:
        session = APClientSessionLocal()
        close_session = True
    try:
        radioId_map = {r.radioname: r.radioid for r in session.query(RadioType).all()}
        for device in device_info_list:
            ap_name = device['name']
            location = device.get('location', '')
            
            # Location parsing logic - handle multiple formats
            location_parts = [p.strip() for p in location.split('/') if p.strip()] if location else []
            building_name = None
            floor_name = None
            room_name = None
            # Robust parsing for all real-world formats
            if len(location_parts) >= 4:
                building_name = location_parts[2]
                floor_name = location_parts[3]
                if len(location_parts) > 4:
                    room_name = location_parts[4]
            elif len(location_parts) == 3:
                building_name = location_parts[1]
                floor_name = location_parts[2]
            elif len(location_parts) == 2:
                building_name = location_parts[0]
                floor_name = location_parts[1]
            else:
                logger.warning(f"Skipping device {ap_name} due to invalid location format: {location}")
                continue

            # Building
            building = session.query(ApBuilding).filter_by(building_name=building_name).first()
            if not building:
                building = ApBuilding(building_name=building_name)
                session.add(building)
                session.flush()
            
            # Floor
            floor = session.query(Floor).filter_by(floorname=floor_name, building_id=building.building_id).first()
            if not floor:
                floor = Floor(floorname=floor_name, building_id=building.building_id)
                session.add(floor)
                session.flush()
            
            # Room (optional)
            room = None
            if room_name:
                room = session.query(Room).filter_by(roomname=room_name, floorid=floor.floorid).first()
                if not room:
                    room = Room(roomname=room_name, floorid=floor.floorid)
                    session.add(room)
                    session.flush()
            
            # Rest of the function remains the same...
            # Access Point
            mac_address = device['macAddress']
            ap = session.query(AccessPoint).filter_by(macaddress=mac_address).first()
            is_active = device['reachabilityHealth'] == "UP"
            if not ap:
                ap = AccessPoint(
                    apname=ap_name,
                    macaddress=mac_address,
                    ipaddress=device.get('ipAddress'),
                    modelname=device.get('model'),
                    isactive=is_active,
                    floorid=floor.floorid,
                    building_id=building.building_id,
                    roomid=room.roomid if room else None
                )
                session.add(ap)
                session.flush()
            else:
                ap.isactive = is_active
            
            # ClientCountAP
            for radio, count in device.get('clientCount', {}).items():
                radio_id = radioId_map.get(radio)
                if radio_id is None:
                    logger.warning(f"Unexpected radio key: {radio}")
                    continue
                cc = session.query(ClientCountAP).filter_by(apid=ap.apid, radioid=radio_id, timestamp=timestamp).first()
                if cc:
                    cc.clientcount = count
                else:
                    cc = ClientCountAP(
                        apid=ap.apid,
                        radioid=radio_id,
                        clientcount=count,
                        timestamp=timestamp
                    )
                    session.add(cc)
        session.commit()
        logger.info(f"Inserted/updated AP and client count data in apclientcount DB for {len(device_info_list)} devices.")
    except Exception as e:
        session.rollback()
        logger.error(f"Error inserting data into apclientcount DB: {e}")
        raise
    finally:
        if close_session:
            session.close()

def fetch_clients(auth_manager, retries=3):
    """Fetch all client devices from the DNA Center API."""
    token = auth_manager.get_token()
    auth_headers = {'x-auth-token': token}
    url = f"{BASE_URL}/dna/data/api/v1/clients"
    attempt = 0
    while attempt < retries:
        try:
            req = Request(url, headers=auth_headers)
            with urlopen(req, context=ssl_context, timeout=60) as response:
                data = json.load(response)
                return data.get('response', [])
        except Exception as e:
            attempt += 1
            logger.warning(f"Error fetching clients (attempt {attempt}): {e}")
            if attempt >= retries:
                logger.error(f"Failed to fetch clients after {retries} attempts: {e}")
                return []
            time.sleep(2 ** attempt)


def fetch_clients_count_by_site(auth_manager, site_id, retries=3):
    """Fetch client count for a specific site from the DNA Center API."""
    token = auth_manager.get_token()
    auth_headers = {'x-auth-token': token}
    url = f"{BASE_URL}/dna/data/api/v1/clients/count?siteId={site_id}"
    attempt = 0
    while attempt < retries:
        try:
            req = Request(url, headers=auth_headers)
            with urlopen(req, context=ssl_context, timeout=60) as response:
                data = json.load(response)
                return data.get('response', {})
        except Exception as e:
            attempt += 1
            logger.warning(f"Error fetching client count for site {site_id} (attempt {attempt}): {e}")
            if attempt >= retries:
                logger.error(f"Failed to fetch client count for site {site_id} after {retries} attempts: {e}")
                return {}
            time.sleep(2 ** attempt)


def fetch_site_health_summaries(auth_manager, retries=3):
    """Fetch site health summaries from the DNA Center API."""
    token = auth_manager.get_token()
    auth_headers = {'x-auth-token': token}
    url = f"{BASE_URL}/dna/data/api/v1/siteHealthSummaries"
    attempt = 0
    while attempt < retries:
        try:
            req = Request(url, headers=auth_headers)
            with urlopen(req, context=ssl_context, timeout=60) as response:
                data = json.load(response)
                return data.get('response', [])
        except Exception as e:
            attempt += 1
            logger.warning(f"Error fetching site health summaries (attempt {attempt}): {e}")
            if attempt >= retries:
                logger.error(f"Failed to fetch site health summaries after {retries} attempts: {e}")
                return []
            time.sleep(2 ** attempt)


def fetch_network_devices(auth_manager, retries=3):
    """Fetch network devices (APs) from the DNA Center API."""
    token = auth_manager.get_token()
    auth_headers = {'x-auth-token': token}
    url = f"{BASE_URL}/dna/data/api/v1/networkDevices?role=ACCESS"
    attempt = 0
    while attempt < retries:
        try:
            req = Request(url, headers=auth_headers)
            with urlopen(req, context=ssl_context, timeout=60) as response:
                data = json.load(response)
                return data.get('response', [])
        except Exception as e:
            attempt += 1
            logger.warning(f"Error fetching network devices (attempt {attempt}): {e}")
            if attempt >= retries:
                logger.error(f"Failed to fetch network devices after {retries} attempts: {e}")
                return []
            time.sleep(2 ** attempt)


def fetch_ap_client_data_with_fallback(auth_manager, site_id=None, retries=3):
    """
    Fetch AP/client data using prioritized, extensible multi-API fallback and merging.
    For each AP/device, merge data from all APIs as needed, filling missing fields from fallback APIs in order.
    Returns a list of dicts, each with a 'status' field and a 'source_map' showing which API provided each field.
    Aggressively attempts to fill missing required fields, logs incomplete devices, and ensures Grafana-required fields are present.
    """
    # Define required and non-critical fields
    required_fields = ['macAddress', 'name', 'location', 'clientCount']
    non_critical_fields = ['model', 'status', 'ipAddress']
    all_fields = required_fields + non_critical_fields
    # List of API fetch functions in order of preference
    api_fetchers = [
        ('networkDevices', fetch_network_devices),
        ('clients', fetch_clients),
        ('siteHealthSummaries', fetch_site_health_summaries),
        ('clients/count', lambda am, r: [fetch_clients_count_by_site(am, site_id, r)] if site_id else []),
    ]
    # Helper: extract fields from each API's record
    def extract_fields(api_name, record):
        if api_name == 'networkDevices':
            return {
                'macAddress': record.get('macAddress'),
                'name': record.get('hostname') or record.get('name'),
                'location': record.get('location') or record.get('snmpLocation') or record.get('locationName'),
                'clientCount': record.get('clientCount', record.get('clients', None)),
                'model': record.get('model') or record.get('platformId'),
                'status': record.get('reachabilityStatus', record.get('reachabilityHealth')),
                'ipAddress': record.get('managementIpAddress', record.get('ipAddress')),
                'raw': record
            }
        elif api_name == 'clients':
            return {
                'macAddress': record.get('connectedNetworkDeviceMacAddress'),
                'name': record.get('connectedNetworkDeviceName'),
                'location': record.get('siteHierarchy'),
                'clientCount': 1,  # Each client record represents one client
                'model': None,
                'status': None,
                'ipAddress': None,
                'raw': record
            }
        elif api_name == 'siteHealthSummaries':
            return {
                'macAddress': None,
                'name': record.get('siteName'),
                'location': record.get('siteName'),
                'clientCount': record.get('numberOfClients', 0),
                'model': None,
                'status': None,
                'ipAddress': None,
                'raw': record
            }
        elif api_name == 'clients/count':
            return {
                'macAddress': None,
                'name': 'Unknown',
                'location': 'Unknown',
                'clientCount': record.get('count', 0),
                'model': None,
                'status': None,
                'ipAddress': None,
                'raw': record
            }
        return {}
    # Step 1: Gather all data from all APIs
    all_records = {}
    for api_name, fetcher in api_fetchers:
        try:
            api_data = fetcher(auth_manager, retries)
            if not api_data:
                continue
            for rec in api_data:
                fields = extract_fields(api_name, rec)
                mac = fields['macAddress']
                # Use MAC as primary key if available, else use name/location
                key = mac or (fields['name'], fields['location'])
                if not key:
                    continue
                if key not in all_records:
                    all_records[key] = {'source_map': {}, 'fields': {}, 'raws': {}, 'api_counts': {}}
                for f in all_fields:
                    val = fields.get(f)
                    if val is not None:
                        # For clientCount, sum if from clients API
                        if f == 'clientCount' and api_name == 'clients':
                            prev = all_records[key]['fields'].get(f, 0)
                            all_records[key]['fields'][f] = prev + 1
                        else:
                            all_records[key]['fields'][f] = val
                        all_records[key]['source_map'][f] = api_name
                        all_records[key]['raws'][api_name] = rec
                        all_records[key]['api_counts'][api_name] = all_records[key]['api_counts'].get(api_name, 0) + 1
        except Exception as e:
            logger.warning(f"Error fetching from {api_name}: {e}")
            continue
    # Step 2: Aggressively fill missing required fields from all APIs
    diagnostics_incomplete = []
    results = []
    for key, data in all_records.items():
        fields = data['fields']
        source_map = data['source_map']
        # Aggressively try to fill missing required fields from any available API
        missing_required = [f for f in required_fields if not fields.get(f)]
        if missing_required:
            # Try to fill from any other API's raw data
            for f in missing_required:
                for api_name, raw in data['raws'].items():
                    val = extract_fields(api_name, raw).get(f)
                    if val:
                        fields[f] = val
                        source_map[f] = api_name
                        missing_required = [ff for ff in required_fields if not fields.get(ff)]
                        if not missing_required:
                            break
                if not missing_required:
                    break
        # After aggressive fill, check again
        missing_required = [f for f in required_fields if not fields.get(f)]
        if not missing_required:
            status = 'ok' if all(source_map.get(f) == 'networkDevices' for f in required_fields if f in source_map) else 'fallback'
        else:
            status = 'incomplete'
            diagnostics_incomplete.append({
                'key': key,
                'missing_fields': missing_required,
                'fields': {f: fields.get(f) for f in all_fields},
                'source_map': source_map,
                'raws': data['raws']
            })
        merged = {f: fields.get(f) for f in all_fields}
        merged['status'] = status
        merged['source_map'] = source_map
        merged['raws'] = data['raws']
        merged['missing_required'] = missing_required
        results.append(merged)
    # Step 3: If no results, try siteHealthSummaries or clients/count as last resort
    if not results:
        site_health = fetch_site_health_summaries(auth_manager, retries)
        for site in site_health:
            results.append({
                'macAddress': None,
                'name': site.get('siteName'),
                'location': site.get('siteName'),
                'clientCount': site.get('numberOfClients', 0),
                'model': None,
                'status': 'siteHealth',
                'ipAddress': None,
                'source_map': {'clientCount': 'siteHealthSummaries'},
                'raws': {'siteHealthSummaries': site},
                'missing_required': ['macAddress']
            })
    # Log diagnostics for incomplete APs/devices
    if diagnostics_incomplete:
        logger.warning(f"{len(diagnostics_incomplete)} APs/devices are incomplete and may need further data recovery. See diagnostics_incomplete for details.")
        save_incomplete_diagnostics_from_list(diagnostics_incomplete)
    return results

def update_ap_data_task_with_fallback(auth_manager):
    """Update AP data using AP and client data, with fallback location logic."""
    logger.info("Starting AP data update task with fallback location support")
    # Fetch client data for fallback
    clients_data = fetch_clients(auth_manager)
    logger.info(f"Fetched {len(clients_data)} client records for fallback location lookup")
    # Fetch AP data, passing client data for fallback
    ap_data = fetch_ap_data(auth_manager, clients_data=clients_data)
    logger.info(f"Fetched {len(ap_data)} AP records after applying fallback location logic")
    # ... continue with processing ap_data as before ...
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
from .mapping import parse_ap_name_for_location

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

# Add at the top, after loading env
SITE_HIERARCHY = os.getenv("DNA_SITE_HIERARCHY", "Global/Keele Campus")

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

def fetch_clients(auth_manager, retries=3, page_limit=100, max_clients=None, delay=1.0, site_id=None, site_hierarchy=None):
    """
    Fetch all client devices from the DNA Center API using pagination and required filter.
    Always uses siteHierarchy (not siteId) for filtering, as required by DNAC instance.
    Args:
        auth_manager: AuthManager instance
        retries: Number of retries per request
        page_limit: Number of clients per page (default 100)
        max_clients: Optional max number of clients to fetch (None = all)
        delay: Delay in seconds between requests
        site_hierarchy: Site hierarchy string (default: Global/Keele Campus)
    Returns:
        List of all client records
    """
    if site_id:
        logger.warning("siteId is ignored for /clients endpoint; using siteHierarchy instead.")
    if not site_hierarchy:
        site_hierarchy = SITE_HIERARCHY
    token = auth_manager.get_token()
    auth_headers = {'x-auth-token': token}
    offset = 1  # 1-based offset per API doc
    all_clients = []
    total_fetched = 0
    filter_param = {'siteHierarchy': site_hierarchy}
    while True:
        params = {**filter_param, 'limit': page_limit, 'offset': offset}
        url = f"{BASE_URL}/dna/data/api/v1/clients?{urlencode(params)}"
        attempt = 0
        while attempt < retries:
            try:
                logger.info(f"Fetching clients: offset={offset}, limit={page_limit}, filter={filter_param}")
                req = Request(url, headers=auth_headers)
                with urlopen(req, context=ssl_context, timeout=60) as response:
                    data = json.load(response)
                    clients = data.get('response', [])
                    if not clients:
                        logger.info(f"No more clients returned at offset {offset}.")
                        return all_clients
                    all_clients.extend(clients)
                    total_fetched += len(clients)
                    logger.info(f"Fetched {len(clients)} clients (total so far: {total_fetched})")
                    if max_clients and total_fetched >= max_clients:
                        logger.info(f"Reached max_clients={max_clients}, stopping fetch.")
                        return all_clients[:max_clients]
                    offset += page_limit  # increment by page_limit, 1-based
                    time.sleep(delay)
                    break  # Success, break retry loop
            except Exception as e:
                attempt += 1
                logger.warning(f"Error fetching clients (attempt {attempt}) at offset {offset}: {e} (URL: {url})")
                if attempt >= retries:
                    logger.error(f"Failed to fetch clients after {retries} attempts at offset {offset}: {e}")
                    return all_clients
                time.sleep(2 ** attempt)

# Global throttle for /clients/count (100 requests/minute)
import threading
_last_clients_count_time = [0.0]
_clients_count_lock = threading.Lock()
def throttle_clients_count():
    import time
    with _clients_count_lock:
        now = time.time()
        elapsed = now - _last_clients_count_time[0]
        min_interval = 60.0 / 100.0  # 100 req/min
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        _last_clients_count_time[0] = time.time()

def fetch_clients_count_for_ap(auth_manager, mac=None, name=None, site_id=None, site_hierarchy=None, retries=3, delay=1.0, backoff_factor=2.0):
    """
    Fetch client count for a specific AP using /clients/count with macAddress or connectedNetworkDeviceName, always including siteHierarchy.
    Implements delay and exponential backoff for 429 errors.
    Throttles globally to stay under 100 requests/minute.
    Returns the count if found, else None. Logs the raw response for debugging.
    """
    if site_id:
        logger.warning("siteId is ignored for /clients/count endpoint; using siteHierarchy instead.")
    if not site_hierarchy:
        site_hierarchy = SITE_HIERARCHY
    token = auth_manager.get_token()
    auth_headers = {'x-auth-token': token}
    params = {'siteHierarchy': site_hierarchy}
    if mac:
        params['macAddress'] = mac
    if name:
        params['connectedNetworkDeviceName'] = name
    url = f"{BASE_URL}/dna/data/api/v1/clients/count?{urlencode(params)}"
    attempt = 0
    current_delay = delay
    while attempt < retries:
        throttle_clients_count()
        try:
            req = Request(url, headers=auth_headers)
            with urlopen(req, context=ssl_context, timeout=30) as response:
                data = json.load(response)
                logger.debug(f"/clients/count response for AP {mac or name}: {data}")
                if isinstance(data, dict) and 'response' in data and 'count' in data['response']:
                    return data['response']['count']
                elif isinstance(data, dict) and 'count' in data:
                    return data['count']
                else:
                    logger.warning(f"Unexpected /clients/count response for AP {mac or name}: {data}")
                    return None
        except HTTPError as e:
            if hasattr(e, 'code') and e.code == 429:
                logger.warning(f"429 Too Many Requests for AP {mac or name}, backing off for {current_delay}s")
                time.sleep(current_delay)
                current_delay *= backoff_factor
            else:
                logger.warning(f"HTTP error for AP {mac or name}: {e}")
                break
        except Exception as e:
            logger.warning(f"Error fetching /clients/count for AP {mac or name}: {e}")
            break
        attempt += 1
    logger.error(f"Failed to fetch /clients/count for AP {mac or name} after {retries} attempts")
    return None

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
    Uses all relevant endpoints as documented in doc/debug/api/selectedApi.txt.
    """
    # --- Step 1: Fetch data from all relevant endpoints ---
    # 1. AP inventory/configuration
    ap_inventory = []
    try:
        ap_inventory = fetch_ap_config_summary(auth_manager, retries)
    except Exception as e:
        logger.warning(f"Error fetching AP inventory: {e}")

    # 2. Device health (per-AP)
    ap_health = []
    try:
        ap_health = fetch_device_health(auth_manager, retries)
    except Exception as e:
        logger.warning(f"Error fetching device health: {e}")

    # 3. Aggregate client counts (per AP, per site, per building)
    client_counts = []
    try:
        client_counts = fetch_all_clients_count(auth_manager, retries)
    except Exception as e:
        logger.warning(f"Error fetching client counts: {e}")

    # 4. All clients (for aggregation by AP if needed)
    all_clients = []
    try:
        # Use paginated fetch_clients to avoid rate limits
        all_clients = fetch_clients(auth_manager, retries=retries, page_limit=100, delay=1.0)
    except Exception as e:
        logger.warning(f"Error fetching all clients: {e}")

    # 5. Site health (site-level fallback)
    site_health = []
    try:
        site_health = fetch_site_health(auth_manager, retries)
    except Exception as e:
        logger.warning(f"Error fetching site health: {e}")

    # 6. Planned APs for building/floor (for mapping)
    planned_aps = []
    try:
        planned_aps = fetch_planned_aps(auth_manager, retries)
    except Exception as e:
        logger.warning(f"Error fetching planned APs: {e}")

    # --- Step 2: Build lookup tables for merging ---
    ap_by_mac = {ap.get('macAddress', '').upper(): ap for ap in ap_inventory if ap.get('macAddress')}
    health_by_mac = {ap.get('macAddress', '').upper(): ap for ap in ap_health if ap.get('macAddress')}
    planned_by_mac = {ap.get('attributes', {}).get('macAddress', '').upper(): ap for ap in planned_aps if ap.get('attributes', {}).get('macAddress')}
    # Aggregate client counts by AP MAC or site/location as available
    client_count_by_ap = {}
    for cc in client_counts:
        ap_mac = cc.get('macAddress', '') or cc.get('apMac', '')
        if ap_mac:
            client_count_by_ap[ap_mac.upper()] = cc.get('count', cc.get('clientCount', 0))
    # Aggregate all clients by AP MAC (using paginated fetch_clients)
    clients_by_ap = {}
    for client in all_clients:
        ap_mac = client.get('apMac') or client.get('connectedNetworkDeviceMac')
        if ap_mac:
            ap_mac = ap_mac.upper()
            clients_by_ap.setdefault(ap_mac, []).append(client)
    # Site health by siteId
    site_health_by_id = {s.get('siteId'): s for s in site_health if s.get('siteId')}

    # --- Step 3: Merge and fill required fields for each AP ---
    required_fields = ['macAddress', 'name', 'location', 'clientCount']
    non_critical_fields = ['model', 'status', 'ipAddress']
    all_fields = required_fields + non_critical_fields
    results = []
    diagnostics_incomplete = []
    all_mac_addresses = set(ap_by_mac.keys()) | set(health_by_mac.keys()) | set(planned_by_mac.keys()) | set(client_count_by_ap.keys()) | set(clients_by_ap.keys())
    skipped_debug = []
    complete_count = 0
    for mac in all_mac_addresses:
        merged = {f: None for f in all_fields}
        source_map = {}
        debug_info = {'mac': mac, 'tried': {}, 'raw': {}}
        # 1. Try AP inventory/config
        ap_inv = ap_by_mac.get(mac)
        if ap_inv:
            merged['macAddress'] = ap_inv.get('macAddress')
            merged['name'] = ap_inv.get('apName') or ap_inv.get('name')
            merged['location'] = ap_inv.get('location')
            merged['model'] = ap_inv.get('apModel') or ap_inv.get('model')
            merged['ipAddress'] = ap_inv.get('primaryIpAddress') or ap_inv.get('ipAddress')
            source_map.update({k: 'ap_inventory' for k in merged if merged[k]})
        # 2. Device health (primary for clientCount)
        ap_h = health_by_mac.get(mac)
        if ap_h:
            merged['macAddress'] = merged['macAddress'] or ap_h.get('macAddress')
            merged['name'] = merged['name'] or ap_h.get('name')
            merged['location'] = merged['location'] or ap_h.get('location')
            merged['model'] = merged['model'] or ap_h.get('model')
            merged['status'] = ap_h.get('reachabilityHealth') or ap_h.get('status')
            # Always prefer device_health for clientCount if present
            merged['clientCount'] = sum(ap_h.get('clientCount', {}).values()) if isinstance(ap_h.get('clientCount'), dict) else ap_h.get('clientCount')
            merged['ipAddress'] = merged['ipAddress'] or ap_h.get('ipAddress')
            source_map.update({k: 'device_health' for k in merged if merged[k] and k not in source_map})
        # 3. Planned APs (for mapping)
        planned = planned_by_mac.get(mac)
        if planned:
            merged['location'] = merged['location'] or planned.get('attributes', {}).get('heirarchyName')
            source_map['location'] = 'planned_aps'
        # 4. Client counts (aggregate)
        if not merged['clientCount'] and mac in client_count_by_ap:
            merged['clientCount'] = client_count_by_ap[mac]
            source_map['clientCount'] = 'client_counts'
        # 5. All clients (aggregate by AP, using paginated fetch_clients)
        if not merged['clientCount'] and mac in clients_by_ap:
            merged['clientCount'] = len(clients_by_ap[mac])
            source_map['clientCount'] = 'all_clients'
        # 6. Fallback: site health (by location)
        if not merged['clientCount'] and merged['location']:
            for s in site_health:
                if s.get('siteName') == merged['location']:
                    merged['clientCount'] = s.get('numberOfClients')
                    source_map['clientCount'] = 'site_health'
                    break
        # 7. Fallback: /clients/count for this AP (last resort)
        if not merged['clientCount']:
            count = fetch_clients_count_for_ap(auth_manager, mac=mac, name=merged.get('name'), site_id=site_id)
            debug_info['tried']['clients_count'] = True
            debug_info['raw']['clients_count'] = count
            if count is not None:
                merged['clientCount'] = count
                source_map['clientCount'] = 'clients_count_api'
        # --- NEW: Fallback for default location using AP name ---
        # If location is missing/invalid or 'default location', try to parse from AP name
        location_invalid = (not merged['location'] or merged['location'].strip().lower() in ['default location', '', 'null', 'none', 'unknown'])
        if location_invalid and merged['name']:
            building, floor, ap_number = parse_ap_name_for_location(merged['name'])
            if building and floor and ap_number:
                merged['location'] = f"Global/Keele Campus/{building}/{floor}/{ap_number}"
                source_map['location'] = 'ap_name_parsing'
        # --- Check for missing required fields ---
        missing_required = [f for f in required_fields if not merged.get(f)]
        status = 'ok' if not missing_required else 'incomplete'
        if status == 'incomplete':
            diagnostics_incomplete.append({
                'mac': mac,
                'missing_fields': missing_required,
                'fields': merged.copy(),
                'source_map': source_map.copy(),
                'debug': debug_info.copy()
            })
            if len(skipped_debug) < 10:
                skipped_debug.append(debug_info)
        else:
            complete_count += 1
        merged['status'] = status
        merged['source_map'] = source_map
        results.append(merged)
    # --- Step 4: Log diagnostics for incomplete/skipped APs/devices ---
    if diagnostics_incomplete:
        logger.warning(f"{len(diagnostics_incomplete)} APs/devices are incomplete and may need further data recovery. See diagnostics_incomplete for details.")
        save_incomplete_diagnostics_from_list(diagnostics_incomplete)
        logger.debug(f"Sample skipped APs debug info: {json.dumps(skipped_debug, indent=2)}")
    logger.info(f"AP data fetch summary: {complete_count} complete, {len(diagnostics_incomplete)} incomplete/skipped.")
    return results

# --- Helper functions for each endpoint, using doc/debug/api/selectedApi.txt as reference ---
def fetch_ap_config_summary(auth_manager, retries=3, key=None):
    """
    Fetch AP inventory/configuration from /wireless/accesspoint-configuration/summary
    If 'key' (Ethernet MAC address) is provided, radioDTOs will be present in the response per API doc.
    Otherwise, radioDTOs will not be included.
    Handles both dict and list responses.
    """
    token = auth_manager.get_token()
    auth_headers = {'x-auth-token': token}
    url = f"{BASE_URL}/dna/intent/api/v1/wireless/accesspoint-configuration/summary?limit=500"
    if key:
        url += f"&key={key}"
    attempt = 0
    while attempt < retries:
        try:
            req = Request(url, headers=auth_headers)
            with urlopen(req, context=ssl_context, timeout=60) as response:
                data = json.load(response)
                if isinstance(data, dict):
                    return data.get('response', [])
                elif isinstance(data, list):
                    return data
                else:
                    logger.warning(f"Unexpected AP config summary response type: {type(data)}")
                    return []
        except Exception as e:
            attempt += 1
            logger.warning(f"Error fetching AP config summary (attempt {attempt}): {e}")
            if attempt >= retries:
                logger.error(f"Failed to fetch AP config summary after {retries} attempts: {e}")
                return []
            time.sleep(2 ** attempt)

def fetch_device_health(auth_manager, retries=3):
    """Fetch device health from /device-health. Handles both dict and list responses."""
    token = auth_manager.get_token()
    auth_headers = {'x-auth-token': token}
    url = f"{BASE_URL}/dna/intent/api/v1/device-health?deviceRole=AP&limit=500"
    attempt = 0
    while attempt < retries:
        try:
            req = Request(url, headers=auth_headers)
            with urlopen(req, context=ssl_context, timeout=60) as response:
                data = json.load(response)
                if isinstance(data, dict):
                    return data.get('response', [])
                elif isinstance(data, list):
                    return data
                else:
                    logger.warning(f"Unexpected device health response type: {type(data)}")
                    return []
        except Exception as e:
            attempt += 1
            logger.warning(f"Error fetching device health (attempt {attempt}): {e}")
            if attempt >= retries:
                logger.error(f"Failed to fetch device health after {retries} attempts: {e}")
                return []
            time.sleep(2 ** attempt)

def fetch_all_clients_count(auth_manager, retries=3):
    """Fetch aggregate client counts from /clients/count. Handles both dict and list responses."""
    token = auth_manager.get_token()
    auth_headers = {'x-auth-token': token}
    url = f"{BASE_URL}/dna/data/api/v1/clients/count"
    attempt = 0
    while attempt < retries:
        try:
            req = Request(url, headers=auth_headers)
            with urlopen(req, context=ssl_context, timeout=60) as response:
                data = json.load(response)
                if isinstance(data, dict):
                    return [data.get('response', data)] if data else []
                elif isinstance(data, list):
                    return data
                else:
                    logger.warning(f"Unexpected clients count response type: {type(data)}")
                    return []
        except Exception as e:
            attempt += 1
            logger.warning(f"Error fetching clients count (attempt {attempt}): {e}")
            if attempt >= retries:
                logger.error(f"Failed to fetch clients count after {retries} attempts: {e}")
                return []
            time.sleep(2 ** attempt)

def fetch_site_health(auth_manager, retries=3):
    """Fetch site health from /site-health. Handles both dict and list responses."""
    token = auth_manager.get_token()
    auth_headers = {'x-auth-token': token}
    url = f"{BASE_URL}/dna/intent/api/v1/site-health?limit=50"
    attempt = 0
    while attempt < retries:
        try:
            req = Request(url, headers=auth_headers)
            with urlopen(req, context=ssl_context, timeout=60) as response:
                data = json.load(response)
                if isinstance(data, dict):
                    return data.get('response', [])
                elif isinstance(data, list):
                    return data
                else:
                    logger.warning(f"Unexpected site health response type: {type(data)}")
                    return []
        except Exception as e:
            attempt += 1
            logger.warning(f"Error fetching site health (attempt {attempt}): {e}")
            if attempt >= retries:
                logger.error(f"Failed to fetch site health after {retries} attempts: {e}")
                return []
            time.sleep(2 ** attempt)

def fetch_planned_aps(auth_manager, retries=3):
    """Fetch planned APs for all buildings/floors (requires building/floor IDs). Handles both dict and list responses."""
    # This is a placeholder; in practice, you may need to loop over all known building/floor IDs and aggregate the results.
    return []

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
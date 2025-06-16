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

def fetch_ap_data(auth_manager, timestamp=None):
    """
    Fetch AP data from DNA Center API with rate limit handling
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
    
    # Process the devices
    processed_devices = []
    seen_macs = {}  # Track unique MAC addresses with their latest data
    
    for device in all_devices:
        try:
            # Get location with fallback
            original_location = device.get("location")
            snmp_location = device.get("snmpLocation")
            location_name = device.get("locationName")
            
            # Determine effective location
            effective_location = original_location
            if not effective_location or len(effective_location.split('/')) < 5:
                if snmp_location and snmp_location.lower() != 'default location' and snmp_location.strip():
                    effective_location = snmp_location
                elif location_name and location_name.strip().lower() != 'null':
                    effective_location = location_name
            
            mac_address = device.get("macAddress", "Unknown")
            
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
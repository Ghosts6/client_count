import logging
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

# Load environment variables
load_dotenv()

# Configure logger
logger = logging.getLogger(__name__)

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
    
    def get_token(self):
        """Get a valid authentication token, refreshing if necessary."""
        current_time = datetime.now()
        if not self.token or not self.token_expiry or current_time >= self.token_expiry - timedelta(minutes=5):
            logger.info("Refreshing authentication token")
            req = Request(self.auth_url, headers=self.auth_headers, method='POST')
            try:
                with urlopen(req, context=ssl_context) as response:
                    if response.status == 200:
                        response_data = json.load(response)
                        self.token = response_data.get("Token")
                        self.token_expiry = current_time + timedelta(minutes=55)
                        logger.info("Authentication token successfully refreshed")
                    else:
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
    Fetch wireless client count data from DNA Center API.
    
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
    
    for i in range(3):  # Fetch data in batches with different offsets
        params = f'?siteType=building&offset={i*50+1}&limit=50&timestamp={rounded_unix_timestamp}'
        req = Request(SITE_HEALTH_URL + params, headers=auth_headers)
        attempt = 0
        
        while attempt < retries:
            try:
                logger.info(f"Starting API request {i + 1} with offset {i * 50 + 1}")
                with urlopen(req, context=ssl_context, timeout=60) as response:
                    response_data = json.load(response)
                    logger.info(f"API request {i + 1} completed successfully")
                    data.extend(response_data.get('response', []))
                break
            except Exception as e:
                attempt += 1
                logger.warning(f"API request error (attempt {attempt}): {e}")
                if attempt >= retries:
                    logger.error(f"Failed after {retries} attempts: {e}")
                time.sleep(2 ** attempt)  # Exponential backoff
    
    # Filter for Keele Campus buildings
    return [site for site in data if site.get('parentSiteName') == 'Keele Campus']

def fetch_ap_data(auth_manager, rounded_unix_timestamp, retries=3):
    """
    Fetch access point data from DNA Center API with detailed information.
    
    Args:
        auth_manager: AuthManager instance for token management
        rounded_unix_timestamp: Timestamp for the API query
        retries: Number of retries for failed requests
        
    Returns:
        List of unique AP data with client counts
    """
    token = auth_manager.get_token()
    auth_headers = {'x-auth-token': token}
    data = []
    
    # Allow time for API to be ready
    time.sleep(5)  
    
    i = 0
    length = 250  # Initial value to enter the loop
    
    while length == 250:  # Continue until we get less than 250 devices (pagination)
        params = {
            "deviceRole": "AP", 
            "siteId": KEELE_CAMPUS_SITE_ID, 
            "limit": 250, 
            "offset": (250 * i) + 1,
            "startTime": rounded_unix_timestamp - 300000,  # 5 minutes before
            "endTime": rounded_unix_timestamp
        }
        
        query_string = urlencode(params)
        req = Request(f"{DEVICE_HEALTH_URL}?{query_string}", headers=auth_headers)
        
        attempt = 0
        while attempt < retries:
            try:
                with urlopen(req, context=ssl_context, timeout=60) as response:
                    device_info = json.load(response)
                    break  # Exit retry loop if successful
            except (HTTPError, URLError) as e:
                attempt += 1
                logger.warning(f"{type(e).__name__} (attempt {attempt}): {e}")
                if attempt < retries:
                    time.sleep(2 ** attempt)  # Exponential backoff
                else:
                    logger.error(f"Failed due to {type(e).__name__} after {retries} attempts: {e}")
                    raise
            except Exception as e:
                attempt += 1
                logger.warning(f"General error (attempt {attempt}): {e}")
                if attempt < retries:
                    time.sleep(2 ** attempt)
                else:
                    logger.error(f"Failed due to general error after {retries} attempts: {e}")
                    raise
        
        response_length = len(device_info["response"])
        length = response_length  # Update length for loop condition
        data.extend(device_info["response"])
        
        i += 1
        if response_length == 250:  # If we got the maximum number, there might be more
            time.sleep(2)  # Avoid API rate limits
    
    # Remove duplicates by uuid
    unique_devices = list({device["uuid"]: device for device in data}.values())
    return unique_devices

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
        session = ApclientSessionLocal()
        close_session = True
    try:
        radioId_map = {r.radioname: r.radioid for r in session.query(RadioType).all()}
        for device in device_info_list:
            ap_name = device['name']
            location = device.get('location', '')
            location_parts = location.split('/')
            if len(location_parts) < 5:
                logger.warning(f"Skipping device {ap_name} due to invalid location format: {location}")
                continue
            building_name = location_parts[3]
            floor_name = location_parts[4]
            # Building
            building = session.query(ApBuilding).filter_by(buildingname=building_name).first()
            if not building:
                building = ApBuilding(buildingname=building_name)
                session.add(building)
                session.flush()
            # Floor
            floor = session.query(Floor).filter_by(floorname=floor_name, buildingid=building.buildingid).first()
            if not floor:
                floor = Floor(floorname=floor_name, buildingid=building.buildingid)
                session.add(floor)
                session.flush()
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
                    buildingid=building.buildingid
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
    finally:
        if close_session:
            session.close()
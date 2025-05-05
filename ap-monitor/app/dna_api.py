import logging
import base64
import json
import ssl
import os
import time
from datetime import datetime, timedelta
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
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
NETWORK_DEVICE_URL = BASE_URL + "/dna/intent/api/v1/network-device"

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

def get_ap_data(auth_manager=None, retries=3):
    """
    Fetch access point data from DNA Center API.
    
    Args:
        auth_manager: Optional AuthManager instance
        retries: Number of retries for failed requests
        
    Returns:
        List of AP data
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
                            "status": device.get("reachabilityStatus", "Unknown"),
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
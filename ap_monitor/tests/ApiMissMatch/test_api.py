#!/usr/bin/env python3
import json
import logging
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from ap_monitor.app.dna_api import AuthManager, test_api_connection, fetch_ap_data
from urllib.request import Request, urlopen
import ssl
import time

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Create SSL context
ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE

def test_pagination(auth_manager, limit=100):
    """Test API with pagination"""
    results = []
    offset = 1
    total_count = None
    
    while True:
        logger.info(f"\nFetching page with offset {offset}...")
        params = {
            "deviceRole": "AP",
            "siteId": "e77b6e96-3cd3-400a-9ebd-231c827fd369",
            "limit": limit,
            "offset": offset
        }
        
        query_string = "&".join(f"{k}={v}" for k, v in params.items())
        test_url = f"https://dnac11.netops.yorku.ca/dna/intent/api/v1/device-health?{query_string}"
        
        logger.info(f"Testing URL: {test_url}")
        
        try:
            token = auth_manager.get_token()
            auth_headers = {
                'x-auth-token': token,
                'Content-Type': 'application/json'
            }
            
            req = Request(test_url, headers=auth_headers)
            with urlopen(req, context=ssl_context) as response:
                response_data = response.read().decode('utf-8')
                data = json.loads(response_data)
                
                if total_count is None:
                    total_count = data.get('totalCount', 0)
                    logger.info(f"Total devices available: {total_count}")
                
                devices = data.get('response', [])
                if not devices:
                    break
                    
                results.extend(devices)
                logger.info(f"Retrieved {len(devices)} devices in this page")
                
                if len(results) >= total_count:
                    break
                    
                offset += limit
                
                # Add a small delay to avoid rate limiting
                time.sleep(1)
                
        except Exception as e:
            logger.error(f"Error fetching page {offset}: {e}")
            break
    
    return {
        "total_retrieved": len(results),
        "expected_total": total_count,
        "sample_device": results[0] if results else None
    }

def main():
    # Load environment variables
    load_dotenv()
    
    # Test basic API connection
    logger.info("Testing basic API connection...")
    api_test = test_api_connection()
    logger.info(f"API Test Result: {json.dumps(api_test, indent=2)}")
    
    # Save API test result
    with open('api_test_result.json', 'w') as f:
        json.dump(api_test, f, indent=2)
    logger.info("Saved API test result to api_test_result.json")
    
    # Test pagination
    logger.info("\nTesting pagination...")
    auth_manager = AuthManager()
    pagination_results = test_pagination(auth_manager)
    
    # Save pagination results
    with open('pagination_results.json', 'w') as f:
        json.dump(pagination_results, f, indent=2)
    logger.info("Saved pagination results to pagination_results.json")
    
    # Test fetching AP data
    logger.info("\nTesting AP data fetch...")
    try:
        ap_data = fetch_ap_data(auth_manager, int(datetime.now().timestamp() * 1000))
        
        # Save AP data
        with open('ap_data_result.json', 'w') as f:
            json.dump(ap_data, f, indent=2)
        logger.info(f"Saved AP data to ap_data_result.json")
        logger.info(f"Found {len(ap_data)} access points")
        
        if ap_data:
            logger.info("\nSample AP data:")
            logger.info(json.dumps(ap_data[0], indent=2))
            
    except Exception as e:
        logger.error(f"Error fetching AP data: {e}")

if __name__ == "__main__":
    main() 
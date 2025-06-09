import logging
import os
from datetime import datetime, timezone, timedelta
from sqlalchemy import func, and_
from .models import Building, Campus, ClientCount, ApBuilding, AccessPoint, ClientCountAP
from .dna_api import fetch_ap_data, AuthManager

# Configure diagnostics logger
diagnostics_logger = logging.getLogger('diagnostics')
diagnostics_logger.setLevel(logging.INFO)

# Create diagnostics log directory if it doesn't exist
log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'Logs', 'diagnostics')
os.makedirs(log_dir, exist_ok=True)

# Configure file handler for diagnostics
diagnostics_file = os.path.join(log_dir, 'diagnostics.log')
file_handler = logging.FileHandler(diagnostics_file)
file_handler.setLevel(logging.INFO)

# Create formatter and add it to the handler
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)

# Add the handler to the logger
diagnostics_logger.addHandler(file_handler)

# Main logger for other operations
logger = logging.getLogger(__name__)

def is_diagnostics_enabled():
    """Check if diagnostics are enabled via environment variable."""
    return os.getenv('ENABLE_DIAGNOSTICS', 'false').lower() == 'true'

def log_diagnostic_report(report):
    """Log diagnostic report to the diagnostics log file."""
    if not is_diagnostics_enabled():
        return

    diagnostics_logger.info("=== Diagnostic Report ===")
    diagnostics_logger.info(f"Timestamp: {report['timestamp']}")
    diagnostics_logger.info(f"Total Buildings Analyzed: {report['summary']['total_buildings_analyzed']}")
    diagnostics_logger.info(f"Buildings with Issues: {report['summary']['buildings_with_issues']}")
    diagnostics_logger.info(f"Active Alerts: {report['summary']['active_alerts']}")
    
    if report['zero_count_buildings']:
        diagnostics_logger.info("\n=== Zero Count Buildings ===")
        for building in report['zero_count_buildings']:
            diagnostics_logger.info(f"\nBuilding: {building['building_name']}")
            diagnostics_logger.info(f"Campus: {building['campus_name']}")
            diagnostics_logger.info(f"AP Status: {building['ap_status']}")
            diagnostics_logger.info(f"DNA Center Status: {building['dna_center_status']}")
            if building['issues']:
                diagnostics_logger.info("Issues:")
                for issue in building['issues']:
                    diagnostics_logger.info(f"- {issue}")
            if building['recommendations']:
                diagnostics_logger.info("Recommendations:")
                for rec in building['recommendations']:
                    diagnostics_logger.info(f"- {rec}")
    
    if report['health_alerts']:
        diagnostics_logger.info("\n=== Health Alerts ===")
        for alert in report['health_alerts']:
            diagnostics_logger.info(f"\nBuilding: {alert['building_name']}")
            diagnostics_logger.info(f"Current Count: {alert['current_count']}")
            diagnostics_logger.info(f"Historical Average: {alert['historical_avg']}")
            diagnostics_logger.info(f"Severity: {alert['severity']}")
            diagnostics_logger.info(f"Message: {alert['message']}")

def analyze_zero_count_buildings(wireless_db, apclient_db, auth_manager):
    """
    Analyze buildings with zero client counts to identify potential issues.
    Returns a detailed report of findings.
    """
    if not is_diagnostics_enabled():
        return {"message": "Diagnostics are not enabled"}

    report = {
        "timestamp": datetime.now(timezone.utc),
        "zero_count_buildings": [],
        "potential_issues": [],
        "recommendations": []
    }

    # Find buildings with zero counts in the last hour
    zero_buildings = wireless_db.query(
        Building, Campus
    ).join(
        Campus, Building.campus_id == Campus.campus_id
    ).outerjoin(
        ClientCount,
        and_(
            Building.building_id == ClientCount.building_id,
            ClientCount.time_inserted >= datetime.now(timezone.utc) - timedelta(hours=1)
        )
    ).filter(
        func.coalesce(ClientCount.client_count, 0) == 0
    ).all()

    for building, campus in zero_buildings:
        building_analysis = {
            "building_name": building.building_name,
            "campus_name": campus.campus_name,
            "ap_status": {},
            "dna_center_status": {},
            "issues": [],
            "recommendations": []
        }

        # Check AP status in apclientcount DB
        ap_building = apclient_db.query(ApBuilding).filter(
            ApBuilding.buildingname.ilike(building.building_name)
        ).first()

        if not ap_building:
            building_analysis["issues"].append("Building not found in apclientcount database")
            building_analysis["recommendations"].append("Verify building name mapping between databases")
            report["potential_issues"].append(f"Mapping issue: {building.building_name}")
            report["zero_count_buildings"].append(building_analysis)
            continue

        # Get AP counts and status
        aps = apclient_db.query(AccessPoint).filter(
            AccessPoint.buildingid == ap_building.buildingid
        ).all()

        building_analysis["ap_status"] = {
            "total_aps": len(aps),
            "active_aps": sum(1 for ap in aps if ap.isactive),
            "inactive_aps": sum(1 for ap in aps if not ap.isactive)
        }

        # Check DNA Center status
        try:
            dna_ap_data = fetch_ap_data(auth_manager)
            building_aps_in_dna = [
                ap for ap in dna_ap_data 
                if building.building_name.lower() in ap.get("location", "").lower()
            ]
            
            building_analysis["dna_center_status"] = {
                "total_aps_in_dna": len(building_aps_in_dna),
                "aps_with_clients": sum(
                    1 for ap in building_aps_in_dna 
                    if sum(ap.get("clientCount", {}).values()) > 0
                )
            }

            # Analyze potential issues
            if building_analysis["ap_status"]["total_aps"] == 0:
                building_analysis["issues"].append("No APs configured for this building")
                building_analysis["recommendations"].append("Verify AP configuration in DNA Center")
            elif building_analysis["ap_status"]["active_aps"] == 0:
                building_analysis["issues"].append("All APs are inactive")
                building_analysis["recommendations"].append("Check AP status in DNA Center")
            elif building_analysis["dna_center_status"]["total_aps_in_dna"] == 0:
                building_analysis["issues"].append("Building not found in DNA Center")
                building_analysis["recommendations"].append("Verify building location in DNA Center")
            elif building_analysis["dna_center_status"]["aps_with_clients"] == 0:
                building_analysis["issues"].append("No clients reported by any AP")
                building_analysis["recommendations"].append("Check AP coverage and client connectivity")

        except Exception as e:
            logger.error(f"Error checking DNA Center status for {building.building_name}: {str(e)}")
            building_analysis["issues"].append(f"Error checking DNA Center: {str(e)}")
            building_analysis["recommendations"].append("Verify DNA Center connectivity and credentials")

        report["zero_count_buildings"].append(building_analysis)

    return report

def monitor_building_health(wireless_db, apclient_db, auth_manager):
    """
    Monitor building health by comparing current client counts with historical data.
    Returns alerts for buildings that show unusual patterns.
    """
    if not is_diagnostics_enabled():
        return []

    alerts = []
    
    # Get buildings with client counts in the last hour
    recent_counts = wireless_db.query(
        Building, ClientCount
    ).join(
        ClientCount, Building.building_id == ClientCount.building_id
    ).filter(
        ClientCount.time_inserted >= datetime.now(timezone.utc) - timedelta(hours=1)
    ).all()

    for building, count in recent_counts:
        # Get historical average (last 24 hours)
        historical_avg = wireless_db.query(
            func.avg(ClientCount.client_count)
        ).filter(
            ClientCount.building_id == building.building_id,
            ClientCount.time_inserted >= datetime.now(timezone.utc) - timedelta(hours=24)
        ).scalar() or 0

        # If current count is zero but historical average is significant
        if count.client_count == 0 and historical_avg > 10:
            alert = {
                "building_name": building.building_name,
                "current_count": count.client_count,
                "historical_avg": round(historical_avg, 2),
                "timestamp": count.time_inserted,
                "severity": "high" if historical_avg > 50 else "medium",
                "message": f"Building {building.building_name} shows zero clients but had an average of {round(historical_avg, 2)} clients in the last 24 hours"
            }
            alerts.append(alert)

    return alerts

def generate_diagnostic_report(wireless_db, apclient_db, auth_manager):
    """
    Generate a comprehensive diagnostic report including zero count analysis and health monitoring.
    """
    if not is_diagnostics_enabled():
        return {"message": "Diagnostics are not enabled"}

    zero_count_analysis = analyze_zero_count_buildings(wireless_db, apclient_db, auth_manager)
    health_alerts = monitor_building_health(wireless_db, apclient_db, auth_manager)

    report = {
        "timestamp": datetime.now(timezone.utc),
        "zero_count_buildings": zero_count_analysis.get("zero_count_buildings", []),
        "health_alerts": health_alerts,
        "summary": {
            "total_buildings_analyzed": len(zero_count_analysis.get("zero_count_buildings", [])),
            "buildings_with_issues": len(zero_count_analysis.get("potential_issues", [])),
            "active_alerts": len(health_alerts)
        }
    }

    # Log the report to the diagnostics log file
    log_diagnostic_report(report)

    return report 
# **AP Monitor**

**AP Monitor** is a FastAPI-based application designed to monitor wireless Access Points (APs) and client counts by integrating with Cisco DNA Center APIs. The application periodically fetches AP data, stores it in a PostgreSQL database, and provides RESTful APIs for data retrieval and manual updates. It is designed for enterprise environments and supports deployment using `systemd` or Docker for virtualization.

---

## **How it Works**
- **Data Collection:** Periodically fetches AP and client count data from Cisco DNA Center APIs.
- **Database Storage:** Stores AP and client count data in a relational database (PostgreSQL in production, SQLite in tests).
- **RESTful API:** Exposes endpoints for retrieving APs, client counts, buildings, floors, rooms, and diagnostics.
- **Diagnostics:** Provides advanced endpoints for zero-count detection, health monitoring, incomplete device records, and API health.
- **Manual and Scheduled Updates:** Data can be updated on a schedule (APScheduler) or manually via API.
- **Logging:** All events and errors are logged for auditing and debugging.
- **Testing:** Comprehensive test suite using pytest, in-memory SQLite, and mock data for fast, isolated tests.

---

## **Project Structure**

```
client_count/
├── ap_monitor/
│   ├── __init__.py
│   ├── app/
│   │   ├── __init__.py
│   │   ├── db.py
│   │   ├── diagnostics.py
│   │   ├── dna_api.py
│   │   ├── main.py
│   │   ├── mapping.py
│   │   ├── models.py
│   │   ├── schemas.py
│   │   └── utils.py
│   ├── __init__.py
│   ├── __pycache__/
│   ├── main.py
│   ├── tests/
│   │   ├── __init__.py
│   │   ├── conftest.py
│   │   ├── test_apclientcount.py
│   │   ├── test_building_mapping.py
│   │   ├── test_db.py
│   │   ├── test_diagnostics.py
│   │   ├── test_dna_api.py
│   │   ├── test_location_parser.py
│   │   ├── test_main.py
│   │   ├── test_models.py
│   │   └── test_utils.py
│   ├── .env
│   ├── requirements.txt
├── Logs/
├── venv/
├── pytest.ini
├── README.md
```

---

## **Environment Configuration**

The application uses a `.env` file for configuration. This file is required for both production and testing, but **test runs override the database settings to use in-memory SQLite** for isolation and speed.

Example `.env` (edit as needed):

```env
# Database Configuration
DB_HOST=localhost
DB_NAME=wireless_count
DB_USER=postgres
DB_PASSWORD=your_password
DB_PORT=3306  

APCLIENT_DB_URL=postgresql://postgres:your_password@localhost:3306/apclientcount

# DNA Center API Configuration
DNA_API_URL=https://your-dnac-host/dna/intent/api/v1/
DNA_USERNAME=your_username
DNA_PASSWORD=your_password

# Application Configuration
LOG_LEVEL=INFO
ENABLE_DIAGNOSTICS=false
```

---

## **Testing Environment**

- **Database:** Uses **in-memory SQLite** for all tests (no real PostgreSQL required).
- **Data:** Uses **mock data** for API and database calls to ensure tests are fast, isolated, and do not affect production data or external services.
- **Test Runner:** Uses `pytest` for running all tests.
- **How to Run:**

```bash
TESTING=true PYTHONPATH=ap_monitor pytest -v ap_monitor/tests/
```

- **Note:**
  - The test suite does **not** require a running PostgreSQL instance or access to real Cisco DNA Center APIs.
  - All database and API interactions are mocked or use in-memory data.

---

## **Production Environment**

- **Database:** Uses **PostgreSQL** for persistent, real data storage.
- **Data:** Connects to **real Cisco DNA Center APIs** for live data.
- **Virtual Environment:** Runs in a Python `venv` for dependency isolation.
- **Process Management:** Managed by `systemd` (or Docker) for reliability and automatic restarts.
- **Logging:** Application logs are stored in the `Logs/` directory.
- **How to Run:**
  - Follow the setup and systemd instructions below.

---

## **Prerequisites**

Ensure the following are installed on the server:

- **Python**: Version 3.10 or higher
- **PostgreSQL**: Version 12 or higher
- **Docker** (optional): For containerized deployment
- **Systemd**: For managing the application as a service

---

## **Setup Instructions**

### 1. Prepare a Clean Deployment Directory

Choose a path for your new app. For example:

```bash
mkdir -p /home/statclcn/client_count
cd /home/statclcn/client_count
```

### 2. Create and Activate a Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Clone the Repository

```bash
git clone https://github.com/Ghosts6/client_count
cd client_count
```

### 4. Configure Environment Variables

Create a `.env` file in the root directory with the following contents. **Note:** The default PostgreSQL port is 5432, but this project uses 3306 (edit as needed):

```env
# Database Configuration
DB_HOST=localhost
DB_NAME=wireless_count
DB_USER=postgres
DB_PASSWORD=your_password
DB_PORT=3306  

APCLIENT_DB_URL=postgresql://postgres:your_password@localhost:3306/apclientcount

# DNA Center API Configuration
DNA_API_URL=https://your-dnac-host/dna/intent/api/v1/
DNA_USERNAME=your_username
DNA_PASSWORD=your_password

# Application Configuration
LOG_LEVEL=INFO
```

### 5. Install Dependencies

```bash
pip install -r ap_monitor/requirements.txt
```

### 6. Initialize the Database

Run the function that creates your tables (once):

```bash
python -c "from ap_monitor.app.db import init_db; init_db()"
```

### 7. Create a `systemd` Service

Save the following configuration as `/etc/systemd/system/ap_monitor.service` (edit paths and user/group as needed):

```ini
[Unit]
Description=AP Monitor FastAPI Application
After=network.target

[Service]
User=statclcn
Group=statclcn
WorkingDirectory=/path/to/project
Environment="PATH=/path/to/venv/bin"
ExecStart=/path/to/client_count/venv/bin/uvicorn ap_monitor.app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### 8. Start the New Service

Bring up the new service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable ap_monitor.service
sudo systemctl start ap_monitor.service
sudo systemctl status ap_monitor.service   # Verify it's running
```

---

## **Database Setup**

Ensure PostgreSQL is running and create the database:

```bash
createdb -h localhost -p 3306 -U postgres wireless_count
```

---

## **API Endpoints**

### **Health Check**

- **Endpoint**: `GET /health`
- **Description**: Returns the health status of the application.
- **Example:**
```bash
curl -i http://localhost:8000/health
```

### **Update AP Data**

- **Endpoint**: `POST /tasks/update-ap-data/`
- **Description**: Manually triggers an update of AP data from the DNA Center API.
- **Example:**
```bash
curl -X POST http://localhost:8000/tasks/update-ap-data/
```

### **Update Client Count Data**

- **Endpoint**: `POST /tasks/update-client-count/`
- **Description**: Manually triggers an update of client count data from the DNA Center API.
- **Example:**
```bash
curl -X POST http://localhost:8000/tasks/update-client-count/
```

### **List AP Data**

- **Endpoint**: `GET /aps`
- **Description**: Retrieves all AP data from the database. Supports query parameters for filtering.
- **Example:**
```bash
curl -i http://localhost:8000/aps
```

### **List Client Count Data**

- **Endpoint**: `GET /client-counts`
- **Description**: Retrieves client count data from the database with optional filters.
- **Example:**
```bash
curl -i http://localhost:8000/client-counts
```

### **List Buildings**

- **Endpoint**: `GET /buildings`
- **Description**: Retrieves a list of unique buildings from the client count data.
- **Example:**
```bash
curl -i http://localhost:8000/buildings
```

### **Diagnostics**

- **Purpose**: Provides advanced diagnostics and troubleshooting endpoints for AP and client count data quality, zero-counts, and incomplete device records. Only available if `ENABLE_DIAGNOSTICS=true`.

#### **Zero Count Diagnostics**

- **Endpoint**: `GET /diagnostics/zero-counts`
- **Description**: Returns diagnostics for buildings with zero client counts and potential issues.
- **Example:**
```bash
curl -i http://localhost:8000/diagnostics/zero-counts
```

#### **Building Health Alerts**

- **Endpoint**: `GET /diagnostics/health`
- **Description**: Returns health monitoring alerts for buildings (e.g., sudden drops in client count).
- **Example:**
```bash
curl -i http://localhost:8000/diagnostics/health
```

#### **Comprehensive Diagnostic Report**

- **Endpoint**: `GET /diagnostics/report`
- **Description**: Returns a comprehensive diagnostic report including zero count analysis and health monitoring.
- **Example:**
```bash
curl -i http://localhost:8000/diagnostics/report
```

#### **Incomplete Devices Diagnostics**

- **Endpoint**: `GET /diagnostics/incomplete-devices`
- **Description**: Returns a list of APs/devices with missing required fields (incomplete records) and their details.
- **Example:**
```bash
curl -i http://localhost:8000/diagnostics/incomplete-devices
```

#### **API Health Diagnostics**

- **Endpoint**: `GET /diagnostics/api_health`
- **Description**: Returns a summary of recent API error rates and details. Tracks the last 100 API errors (in memory, not persisted across restarts).
- **Response:**
```
{
  "total_errors_tracked": 12,
  "errors_last_hour": 3,
  "recent_errors": [
    {
      "timestamp": "2025-07-11T16:50:08.360339+00:00",
      "type": "APIError",
      "message": "No AP/client data available from any endpoint."
    },
    ...
  ]
}
```
- `total_errors_tracked`: Number of errors currently tracked (max 100).
- `errors_last_hour`: Number of errors in the last hour.
- `recent_errors`: The 10 most recent errors (timestamp, type, message).
- **Usage:**
  - `GET /diagnostics/api_health`
  - Useful for monitoring API health, rate limits, and diagnosing external API issues.

### **OpenAPI Documentation**

- **Endpoint**: `GET /openapi.json` and `/docs`
- **Description**: Returns the OpenAPI schema and interactive API docs.
- **Example:**
```bash
curl -i http://localhost:8000/openapi.json
```

---

## **Logging**

Application logs are stored in the `Logs/` directory:

```
Logs/ap-monitor.log
```

---

## **Testing**

The application uses `pytest` for testing. Tests are located in the `tests/` directory and cover the following areas:

- **Models:** Tests for database models (`test_models.py`).
- **APIs:** Tests for DNA Center API integration (`test_dna_api.py`).
- **Utilities:** Tests for utility functions like logging and scheduling (`test_utils.py`).
- **Location Parsing:** Tests for location parsing logic (`test_location_parser.py`).
- **Application Functionality:** Tests for FastAPI endpoints and database interactions.

**Test Environment Details:**
- All tests use **in-memory SQLite** (no PostgreSQL required).
- All external API calls are **mocked**.
- Tests are fast, isolated, and safe to run on any machine.

To run the tests, use the following command:

```bash
TESTING=true PYTHONPATH=ap_monitor pytest -v ap_monitor/tests/
```

Api endpoint test examples:

```bash
curl -i http://localhost:8000/
curl -i http://localhost:8000/openapi.json
curl -i http://localhost:8000/buildings
```

Run app manually:

```bash
uvicorn ap_monitor.app.main:app --host 0.0.0.0 --port 8000 --reload
```

---
## **Automated Cleanup with pg\_Cron**

This PostgreSQL setup uses the `pg_cron` extension to schedule a daily cleanup job that deletes old records (older than 30 days) from two tables:

* `clientcount` in the `apclientcount` database
* `client_counts` in the `wireless_count` database (via Unix socket)

### Configuration Overview

The cleanup is handled safely and automatically with the following configurations and components:

### **1. Install `pg_cron` Extension**

Install `pg_cron` using your package manager (example for Debian-based systems):

```bash
sudo apt install postgresql-14-cron
```

Enable the extension in your PostgreSQL configuration:

```bash
# postgresql.conf
shared_preload_libraries = 'pg_cron'
cron.database_name = 'apclientcount'
cron.host = '/var/run/postgresql'
cron.port = 3306
```

> 🔄 After updating the config, **restart** PostgreSQL:

```bash
sudo systemctl restart postgresql
```

### **2. Enable `pg_cron` in the Database**

Connect to the `apclientcount` database and enable the extension:

```sql
CREATE EXTENSION IF NOT EXISTS pg_cron;
```

### **3. Create Cleanup Function**

Define a reusable PL/pgSQL function to delete stale records:

```sql
CREATE OR REPLACE FUNCTION public.cleanup_counts() RETURNS void AS
$$
BEGIN
  -- Local cleanup
  DELETE FROM clientcount
   WHERE timestamp < NOW() - INTERVAL '30 days';

  -- Remote cleanup via Unix socket on port 3306
  PERFORM dblink_exec(
    'host=/var/run/postgresql port=3306 dbname=wireless_count user=postgres',
    'DELETE FROM client_counts WHERE time_inserted < NOW() - INTERVAL ''30 days'';'
  );
END
$$ LANGUAGE plpgsql;
```

### **4. Schedule the Daily Cleanup Job**

Create a daily cron job that runs at 3:00 AM:

```sql
SELECT cron.schedule(
  'daily_cleanup',
  '0 3 * * *',
  $$ SELECT cleanup_counts(); $$
);
```

#### **Manual Cleanup**
To run the cleanup manually, execute:

```sql
SELECT cleanup_counts();
```

### ✅ **Result**

* The task runs every day at 3:00 AM.
* It safely cleans both local and remote tables using a secure Unix socket.
* Logs and status can be monitored via:

```sql
SELECT jobid,
       runid,
       status,
       return_message,
       start_time,
       end_time
FROM cron.job_run_details
WHERE jobid = (
  SELECT jobid
  FROM cron.job
  WHERE jobname = 'daily_cleanup'
)
ORDER BY start_time DESC
LIMIT 5;
```

### cancel the scheduled job:

```sql
SELECT cron.unschedule('daily_cleanup');
```

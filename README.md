# **AP Monitor**

**AP Monitor** is a FastAPI-based application designed to monitor wireless Access Points (APs) and client counts by integrating with Cisco DNA Center APIs. The application periodically fetches AP data, stores it in a PostgreSQL database, and provides RESTful APIs for data retrieval and manual updates. It is designed for enterprise environments and supports deployment using `systemd` or Docker for virtualization.

---

## **Features**

- **Data Collection**: Fetches AP data (name, status, client count) and client count data from Cisco DNA Center APIs.
- **Database Integration**: Stores data in a PostgreSQL database with optimized schema for querying.
- **RESTful API**: Provides endpoints for manual updates, data retrieval, and health checks.
- **Scheduler**: Automatically updates data every 5 minutes using APScheduler.
- **Logging**: Logs application events and errors for debugging and auditing.
- **Scalable Deployment**: Supports deployment using `systemd` or Docker for portability and reliability.
- **Testing**: Comprehensive testing of models, APIs, and utility functions using `pytest`.

---

## **Project Structure**

```
client_count/
├── ap_monitor/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── db.py
│   │   ├── dna_api.py
│   │   ├── main.py
│   │   ├── models.py
│   │   └── utils.py
│   ├── __init__.py
│   ├── __pycache__/
│   ├── main.py
│   ├── tests/
│   │   ├── __init__.py
│   │   ├── conftest.py
│   │   ├── test_db.py
│   │   ├── test_dna_api.py
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

Create a `.env` file in the root directory with the following contents:

```env
# Database Configuration
DB_HOST=localhost
DB_NAME=wireless_count
DB_USER=postgres
DB_PASSWORD=your_password
DB_PORT=3306

# DNA Center API Configuration
DNA_API_URL=https://your-dnac-host/dna/intent/api/v1/
DNA_USERNAME=your_username
DNA_PASSWORD=your_password

# Application Configuration
LOG_LEVEL=INFO
```

### 5. Install Dependencies

```bash
pip install -r requirements.txt
```

### 6. Initialize the Database

Run the function that creates your tables (once):

```bash
source venv/bin/activate
python -c "from app.db import init_db; init_db()"
deactivate
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
WorkingDirectory=/home/statclcn/client_count
Environment="PATH=/home/statclcn/client_count/venv/bin"
ExecStart=/home/statclcn/client_count/venv/bin/uvicorn ap_monitor.app.main:app --host 0.0.0.0 --port 8000
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
sudo systemctl status ap_monitor.service   # Verify it’s running
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

- **Endpoint**: `GET /`
- **Description**: Returns the health status of the application.

### **Update AP Data**

- **Endpoint**: `POST /update-aps`
- **Description**: Manually triggers an update of AP data from the DNA Center API.

### **Update Client Count Data**

- **Endpoint**: `POST /update-client-counts`
- **Description**: Manually triggers an update of client count data from the DNA Center API.

### **List AP Data**

- **Endpoint**: `GET /aps`
- **Description**: Retrieves all AP data from the database.

### **List Client Count Data**

- **Endpoint**: `GET /client-counts`
- **Description**: Retrieves client count data from the database with optional filters.

### **List Buildings**

- **Endpoint**: `GET /buildings`
- **Description**: Retrieves a list of unique buildings from the client count data.

---

## **Logging**

Application logs are stored in the `Logs/` directory:

```
Logs/ap-monitor.log
```

---

## **Testing**

The application uses `pytest` for testing. Tests are located in the `tests/` directory and cover the following areas:

- **Models**: Tests for database models (`test_models.py`).
- **APIs**: Tests for DNA Center API integration (`test_dna_api.py`).
- **Utilities**: Tests for utility functions like logging and scheduling (`test_utils.py`).
- **Application Functionality**: Tests for FastAPI endpoints and database interactions.

To run the tests, use the following command:

```bash
TESTING=true PYTHONPATH=ap_monitor pytest -v ap_monitor/tests/
```

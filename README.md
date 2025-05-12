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
ap-monitor/
├── app/
│   ├── __init__.py
│   ├── main.py          # FastAPI application entry point
│   ├── db.py            # Database connection and initialization
│   ├── models.py        # Database schema (AccessPoint and ClientCount models)
│   ├── dna_api.py       # Handles communication with Cisco DNA Center API
│   ├── utils.py         # Utility functions (e.g., logging, scheduling)
├── Logs/
│   └── ap-monitor.log   # Application logs
├── tests/
│   ├── test_models.py   # Tests for database models
│   ├── test_main.py     # Tests for main
│   ├── test_db.py       # Tests for db 
│   ├── test_dna_api.py  # Tests for DNA Center API integration
│   ├── test_utils.py    # Tests for utility functions
│   ├── conftest.py      # Test fixtures for database and FastAPI client
├── .env                 # Environment variables
├── requirements.txt     # Project dependencies
├── pytest.ini           # Pytest configuration
├── README.md            # Project documentation
└── main.py              # Script to start the application
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

### 1. Clone the Repository

```bash
git clone https://github.com/Ghosts6/client_count
cd ap_monitor
```

### 2. Configure Environment Variables

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

Replace the placeholders with your actual database and Cisco DNA Center credentials.

### 3. Install Dependencies

#### Using a Virtual Environment:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## **Database Setup**

Ensure PostgreSQL is running and create the database:

```bash
createdb -h localhost -p 3306 -U postgres wireless_count
```

---

## **Running the Application**

### Using `systemd`

1. Create a `systemd` service file at `/etc/systemd/system/ap-monitor.service`:

   ```ini
   [Unit]
   Description=AP Monitor FastAPI Application
   After=network.target

   [Service]
   User=your_user
   WorkingDirectory=/path/to/ap-monitor
   ExecStart=/path/to/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
   Restart=always

   [Install]
   WantedBy=multi-user.target
   ```

2. Enable and start the service:

   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable ap-monitor
   sudo systemctl start ap-monitor
   ```

3. Verify the service status:

   ```bash
   sudo systemctl status ap-monitor
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

Ensure the `Logs/` directory is writable by the application.

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

---

## **Deployment Notes**

### **Security Considerations**

- Use a secure `.env` file to store sensitive credentials.
- Restrict access to the API endpoints using a reverse proxy (e.g., NGINX) or authentication mechanisms.
- Regularly monitor logs for errors or unauthorized access attempts.

### **Scaling**

- For high availability, deploy the application using Docker Swarm or Kubernetes.
- Use a load balancer to distribute traffic across multiple instances.

---

## **Troubleshooting**

### Common Issues

| Issue                          | Solution                                                                 |
|--------------------------------|-------------------------------------------------------------------------|
| Database connection error      | Ensure PostgreSQL is running and credentials in `.env` are correct.     |
| API authentication error       | Verify Cisco DNA Center credentials in `.env`.                         |
| Scheduler not running          | Check the `systemd` service status or Docker container logs.            |
| Logs not generated             | Ensure the `Logs/` directory exists and is writable.                   |

---
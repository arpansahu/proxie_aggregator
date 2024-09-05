import sqlite3
from flask import Flask, jsonify, request
import threading
import random
import docker
import time
import socket
from datetime import datetime
import os
import shutil
import atexit
import uuid


app = Flask(__name__)

# Celery setup
def make_celery(app):
    celery = Celery(
        app.import_name,
        backend=app.config['CELERY_RESULT_BACKEND'],
        broker=app.config['CELERY_BROKER_URL']
    )
    celery.conf.update(app.config)
    TaskBase = celery.Task

    class ContextTask(TaskBase):
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return TaskBase.__call__(self, *args, **kwargs)

    celery.Task = ContextTask
    return celery

# Configure Celery
app.config.update(
    CELERY_BROKER_URL='redis://localhost:6379/0',  # Use Redis as a broker
    CELERY_RESULT_BACKEND='redis://localhost:6379/0'
)

celery = make_celery(app)

@celery.task
def rotate_container():
    """Celery task to rotate the oldest container."""
    with pool_lock:
        # Fetch the pool from the database
        pool = get_container_pool()

        if pool:
            # Get the oldest container (assuming pool is already sorted by start time)
            oldest_container = pool[0]  # First in the list is the oldest

            print(f"Rotating container {oldest_container[0]}")

            # Stop and remove the oldest container
            stop_and_remove_container(oldest_container[0])

            # Update the database to set the stop time
            record_container_stop(oldest_container[0])

            # Start a new container after removing the old one
            print(f"Starting a new container to replace {oldest_container[0]}")
            start_gluetun_container()

# Path to the directory with keys and certificates
OPEVPN_DIR = "/Users/arpansahu/projects/profile/proxie_aggregator/opevpn"

# Rotation grace period in seconds (5 minutes)
GRACE_PERIOD = 120

COUNTRIES = ["Germany", "Netherlands", "United States", "France", "Canada", "United Kingdom"]
client = docker.from_env()

# Lock to manage access to the pool
pool_lock = threading.Lock()

# Pool to store proxy containers
container_pool = []

# Track last used time for each container
container_usage = {}

# SQLite DB setup
DB_PATH = 'proxy_container_records.db'

def init_db():
    """Initialize the SQLite database."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Single table to store container start and stop times
    cursor.execute('''CREATE TABLE IF NOT EXISTS proxy_containers (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        container_name TEXT,
                        country TEXT,
                        external_port INTEGER,
                        start_time TEXT,
                        stop_time TEXT  -- This will be NULL initially and updated when container stops
                      )''')
    conn.commit()
    conn.close()

def get_container_pool():
    """Fetch the current container pool from the database (containers without stop time)."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT container_name, country, external_port FROM proxy_containers WHERE stop_time IS NULL')
    containers = cursor.fetchall()
    conn.close()
    return containers

def record_container_start(container_name, country, external_port):
    """Record when a new container is started."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    start_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # Insert into the single table with NULL for stop_time initially
    cursor.execute('''INSERT INTO proxy_containers (container_name, country, external_port, start_time, stop_time)
                      VALUES (?, ?, ?, ?, NULL)''', (container_name, country, external_port, start_time))
    conn.commit()
    conn.close()

def record_container_stop(container_name):
    """Update the stop time of a container when it's stopped."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    stop_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # Update the stop_time where container_name matches
    cursor.execute('''UPDATE proxy_containers
                      SET stop_time = ?
                      WHERE container_name = ?''', (stop_time, container_name))
    conn.commit()
    conn.close()
    
# Function to find a free port on the host
def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]

# Function to start a Gluetun container
def start_gluetun_container(country=None):
    # If a specific country is provided, use it; otherwise, pick a random one
    if not country:
        country = random.choice(COUNTRIES)

    # Replace spaces with underscores in the country name to ensure valid container names
    country_safe = country.replace(" ", "_")

    # Generate a unique container name with the format: gluetun-vpn-location_name-datetime
    current_time = datetime.now().strftime('%Y%m%d-%H%M%S')
    container_name = f"gluetun-vpn-{country_safe.lower()}-{current_time}-{uuid.uuid4()}"
    # Get a free external port for the proxy
    external_port = get_free_port()

    # Set the environment variables
    env_vars = {
        "VPN_SERVICE_PROVIDER": "cyberghost",
        "OPENVPN_USER": "zmwdkM8gPv",  # Replace with your user
        "OPENVPN_PASSWORD": "WUAVGBc3BM",  # Replace with your password
        "SERVER_COUNTRIES": country,
        "HTTPPROXY": "on"
    }

    # Define the volume mapping (bind the same OPEVPN_DIR for all containers)
    volume_mapping = {
        OPEVPN_DIR: {  # Shared directory on the host
            "bind": "/gluetun",  # Container directory
            "mode": "rw"  # Read-write mode
        }
    }

    # Run the Gluetun container using Docker SDK
    try:
        container = client.containers.run(
            image="qmcgaw/gluetun",
            name=container_name,  # Dynamically assigned container name
            cap_add=["NET_ADMIN"],
            environment=env_vars,
            ports={"8888/tcp": external_port},  # Expose port 8888 for HTTP proxy
            volumes=volume_mapping,  # Pass the unique subdirectory as a volume
            detach=True,
        )
        # Add container to the pool
        with pool_lock:
            container_pool.append({
                "name": container_name,
                "id": container.id,
                "port": external_port,
                "country": country,
                "container": container,
                "created_at": time.time()  # Record the creation timestamp
            })

            # Record the container start in the SQLite database
            record_container_start(container_name, country, external_port)

            # Update container usage when container is started
            container_usage[container_name] = time.time()

        return container, country, external_port
    except Exception as e:
        print(f"Error starting container: {str(e)}")
        raise

# Function to stop and remove a container
def stop_and_remove_container(container_name):
    try:
        container = client.containers.get(container_name)
        if container.status == "running":
            container.stop()
        container.remove(v=True)
        
        # Record the container stop event
        record_container_stop(container_name)

        with pool_lock:
            container_pool[:] = [c for c in container_pool if c['name'] != container_name]
            if container_name in container_usage:
                del container_usage[container_name]
        print(f"Container {container_name} stopped and removed.")
    except docker.errors.NotFound:
        print(f"Container {container_name} not found.")
    except Exception as e:
        print(f"Error removing container {container_name}: {str(e)}")

import math
def rotate_oldest_container():
    # Fetch the pool from the database
    pool = get_container_pool()

    if pool:
        # Assuming the pool is already sorted by start time (oldest first)
        # You can add sorting logic here if needed, but this assumes it's ordered by start time.
        
        # Get the oldest container
        oldest_container = pool[0]  # First in the list is the oldest

        print(f"Rotating container {oldest_container[0]}")

        # Stop and remove the oldest container
        stop_and_remove_container(oldest_container[0])

        # Update the pool by setting the stop time in the database
        record_container_stop(oldest_container[0])

        # Start a new container and add it to the pool
        print(f"Starting a new container to replace {oldest_container[0]}")
        container, country, port = start_gluetun_container()

        # New container is automatically added to the database when started (handled by record_container_start)

    # Log the next rotation time
    next_rotation_time = GRACE_PERIOD - (time.time() - min(pool, key=lambda x: x[2])['created_at'])
    next_rotation_time = math.ceil(next_rotation_time)
    print(f"Next rotation will occur after {next_rotation_time} seconds.")


# Function to start initial proxy containers
def start_initial_proxies():
    print("Starting 5 initial proxy containers...")
    for _ in range(5):
        start_gluetun_container()
        time.sleep(5)  # 15-second delay between starting each container
    print("Initial 5 proxy containers started.")

# Function to stop and remove all active containers on app shutdown
def stop_all_proxies():
    print("Stopping all proxy containers on shutdown...")
    with pool_lock:
        for container in container_pool[:]:  # Clone list to avoid modification during iteration
            stop_and_remove_container(container['name'])
    print("All proxy containers stopped and removed.")

# Register shutdown cleanup function
atexit.register(stop_all_proxies)

# Route to handle requests through the available proxy container
@app.route('/handle-request', methods=['POST'])
def handle_request():
    try:
        # Get an available container
        with pool_lock:
            container_info = container_pool[0] if container_pool else None

        if not container_info:
            return jsonify({"error": "No available containers in the pool"}), 500

        proxy_port = container_info["port"]
        target_url = request.json.get("url")

        if not target_url:
            return jsonify({"error": "Missing URL in request"}), 400

        proxies = {
            "http": f"http://localhost:{proxy_port}",
            "https": f"http://localhost:{proxy_port}"
        }

        response = requests.get(target_url, proxies=proxies)

        # Update the last usage time
        with pool_lock:
            container_usage[container_info['name']] = time.time()

        return jsonify({"status": response.status_code, "data": response.text})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/start-proxy', methods=['POST'])
def start_proxy():
    try:
        container, country, external_port = start_gluetun_container()
        return jsonify({"message": f"Proxy started with country {country}", "container_name": container.name, "external_port": external_port})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/stop-proxy/<container_name>', methods=['POST'])
def stop_proxy(container_name):
    stop_and_remove_container(container_name)
    return jsonify({"message": f"Proxy container {container_name} stopped, removed, and deleted from the pool."})

@app.route('/list-proxies', methods=['GET'])
def list_proxies():
    if not container_pool:
        return jsonify({"message": "No active proxy containers."})
    
    # Serialize container pool information
    serialized_pool = [
        {
            "container_id": container_info["id"],
            "container_name": container_info["name"],
            "country": container_info["country"],
            "external_port": container_info["port"]
        }
        for container_info in container_pool
    ]
    
    return jsonify({"available_proxies": serialized_pool})

from flask import render_template

@app.route('/list-container-records', methods=['GET'])
def list_container_records():
    """List all container records including their start and stop times."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Select all records from the proxy_containers table
    cursor.execute('''SELECT * FROM proxy_containers''')
    containers = cursor.fetchall()
    conn.close()

    # Render the HTML template and pass the container records to it
    return render_template('list_container_records.html', containers=containers)


@app.route('/start-rotation', methods=['POST'])
def start_rotation():
    """Manually trigger container rotation via Celery."""
    rotate_container.apply_async()
    return jsonify({"message": "Container rotation started."})


if __name__ == '__main__':
    init_db()  # Initialize the database first
    start_initial_proxies()  # Start 5 proxy containers on app startup

    app.run(host='0.0.0.0', port=8489)
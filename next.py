from flask import Flask, jsonify, request, render_template
from celery_config import make_celery
import random
import docker
import time
import socket
from datetime import datetime
import os
import atexit
import uuid
import sqlite3

app = Flask(__name__)

# Configure Celery
app.config.update(
    CELERY_BROKER_URL='redis://localhost:6379/0',  # Use Redis as a broker
    CELERY_RESULT_BACKEND='redis://localhost:6379/0'
)

# Initialize Celery
celery = make_celery(app)

# Path to the directory with keys and certificates
OPEVPN_DIR = "/Users/arpansahu/projects/profile/proxie_aggregator/opevpn"

# Rotation grace period in seconds
GRACE_PERIOD = 120

COUNTRIES = ["Germany", "Netherlands", "United States", "France", "Canada", "United Kingdom"]
client = docker.from_env()

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
    if not country:
        country = random.choice(COUNTRIES)
    country_safe = country.replace(" ", "_")

    current_time = datetime.now().strftime('%Y%m%d-%H%M%S')
    container_name = f"gluetun-vpn-{country_safe.lower()}-{current_time}-{uuid.uuid4()}"
    external_port = get_free_port()

    env_vars = {
        "VPN_SERVICE_PROVIDER": "cyberghost",
        "OPENVPN_USER": "zmwdkM8gPv",
        "OPENVPN_PASSWORD": "WUAVGBc3BM",
        "SERVER_COUNTRIES": country,
        "HTTPPROXY": "on"
    }

    volume_mapping = {
        OPEVPN_DIR: {"bind": "/gluetun", "mode": "rw"}
    }

    try:
        container = client.containers.run(
            image="qmcgaw/gluetun",
            name=container_name,
            cap_add=["NET_ADMIN"],
            environment=env_vars,
            ports={"8888/tcp": external_port},
            volumes=volume_mapping,
            detach=True,
        )

        record_container_start(container_name, country, external_port)
        return container, country, external_port
    except Exception as e:
        print(f"Error starting container: {str(e)}")
        raise

def stop_and_remove_container(container_name):
    try:
        container = client.containers.get(container_name)
        if container.status == "running":
            container.stop()
        container.remove(v=True)
        record_container_stop(container_name)
        print(f"Container {container_name} stopped and removed.")
    except docker.errors.NotFound:
        print(f"Container {container_name} not found.")
    except Exception as e:
        print(f"Error removing container {container_name}: {str(e)}")

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

@celery.task(name="next.rotate_container")  # Explicit task name
def rotate_container():
    pool = get_container_pool()
    if pool:
        oldest_container = pool[0]
        stop_and_remove_container(oldest_container[0])
        print(f"Rotating container {oldest_container[0]}")
        start_gluetun_container()

@app.route('/start-rotation', methods=['POST'])
def start_rotation():
    """Manually trigger container rotation via Celery."""
    rotate_container.apply_async()
    return jsonify({"message": "Container rotation started."})

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

if __name__ == '__main__':
    init_db()
    start_initial_proxies()
    app.run(host='0.0.0.0', port=8489)
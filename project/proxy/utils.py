import docker
import random
import socket
import uuid
from datetime import datetime
import sqlite3
from flask import current_app
import time  # Add this import

client = docker.from_env()
COUNTRIES = ["Germany", "Netherlands", "United States", "France", "Canada", "United Kingdom"]
OPEVPN_DIR = "/Users/arpansahu/projects/profile/proxie_aggregator/opevpn"


def get_db_path():
    """Fetch the DB path from the Flask app config"""
    return current_app.config['DB_PATH']

def init_db():
    """Initialize the SQLite database."""
    db_path = get_db_path()  # Fetch DB path from config
    conn = sqlite3.connect(db_path)
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

# Your utility functions like get_free_port, start_gluetun_container, etc. would be moved here

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



def get_container_pool():
    """Fetch the current container pool from the database (containers without stop time)."""
    conn = sqlite3.connect(get_db_path())
    cursor = conn.cursor()
    cursor.execute('SELECT container_name, country, external_port FROM proxy_containers WHERE stop_time IS NULL')
    containers = cursor.fetchall()
    conn.close()
    return containers

def record_container_start(container_name, country, external_port):
    """Record when a new container is started."""
    conn = sqlite3.connect(get_db_path())
    cursor = conn.cursor()
    start_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # Insert into the single table with NULL for stop_time initially
    cursor.execute('''INSERT INTO proxy_containers (container_name, country, external_port, start_time, stop_time)
                      VALUES (?, ?, ?, ?, NULL)''', (container_name, country, external_port, start_time))
    conn.commit()
    conn.close()

def record_container_stop(container_name):
    """Update the stop time of a container when it's stopped."""
    conn = sqlite3.connect(get_db_path())
    cursor = conn.cursor()
    stop_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # Update the stop_time where container_name matches
    cursor.execute('''UPDATE proxy_containers
                      SET stop_time = ?
                      WHERE container_name = ?''', (stop_time, container_name))
    conn.commit()
    conn.close()

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
    """Stop and remove all active proxy containers on shutdown."""
    print("Stopping all active proxy containers on shutdown...")

    active_containers = get_container_pool()
    
    if not active_containers:
        print("No active containers found.")
        return
    
    for container in active_containers:
        container_name = container[0]  # container_name is the first column in the result

        try:
            # Stop and remove the container
            stop_and_remove_container(container_name)

            # Record the stop time
            record_container_stop(container_name)
        except Exception as e:
            print(f"Failed to stop and remove container {container_name}: {str(e)}")

    print("All active proxy containers stopped and removed.")

from flask import jsonify, request
from .tasks import rotate_container
from flask import render_template
from project.proxy.utils import (
    start_gluetun_container,
    stop_and_remove_container,
    stop_all_proxies,
    get_container_pool
)
import sqlite3
from flask import current_app
from . import proxy_blueprint  # Import the blueprint
import random
import requests

def get_db_path():
    """Fetch the DB path from the Flask app config"""
    return current_app.config['DB_PATH']


@proxy_blueprint.route('/start-rotation', methods=['POST'])
def start_rotation():
    """Manually trigger container rotation via Celery."""
    rotate_container.apply_async()
    return jsonify({"message": "Container rotation started."})

# Route to handle requests through the available proxy container
@proxy_blueprint.route('/handle-request', methods=['POST'])
def handle_request():
    try:
        # Get available containers from the database using utils.get_container_pool()
        container_pool = get_container_pool()

        if not container_pool:
            return jsonify({"error": "No available containers in the pool"}), 500

        # Randomly select a container from the pool
        container_info = random.choice(container_pool)
        container_name = container_info[0]  # container_name is the first column
        proxy_port = container_info[2]  # external_port is the third column

        target_url = request.json.get("url")
        if not target_url:
            return jsonify({"error": "Missing URL in request"}), 400

        proxies = {
            "http": f"http://localhost:{proxy_port}",
            "https": f"http://localhost:{proxy_port}"
        }

        # Make a request through the proxy
        response = requests.get(target_url, proxies=proxies)

        # Optionally update the last usage time in your DB or in-memory data structure
        # Here you can record usage if necessary

        return jsonify({"status": response.status_code, "data": response.text})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@proxy_blueprint.route('/start-proxy', methods=['POST'])
def start_proxy():
    try:
        container, country, external_port = start_gluetun_container()
        return jsonify({"message": f"Proxy started with country {country}", "container_name": container.name, "external_port": external_port})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@proxy_blueprint.route('/stop-proxy', methods=['POST'])
def stop_proxy():
    """
    Stop and remove a specific proxy container or all proxies if no container is specified.
    Request body can include a container_name to stop and remove that container,
    or leave it blank to stop and remove all proxy containers.
    """
    data = request.get_json()
    container_name = data.get('container_name')

    try:
        if container_name:
            # Stop and remove the specific container
            stop_and_remove_container(container_name)
            return jsonify({"message": f"Proxy container '{container_name}' stopped, removed, and deleted from the pool."}), 200
        else:
            # Stop all proxy containers if no specific container is provided
            stop_all_proxies()
            return jsonify({"message": "All proxy containers have been stopped, removed, and deleted from the pool."}), 200
    except ContainerNotFoundException as e:
        # Handle case where container is not found
        return jsonify({"error": str(e), "message": f"Container '{container_name}' not found."}), 404
    except Exception as e:
        # Handle any unexpected errors
        return jsonify({"error": "An error occurred while stopping the proxy.", "details": str(e)}), 500

@proxy_blueprint.route('/list-proxies', methods=['GET'])
def list_proxies():
    # Fetch the container pool from the database
    container_pool = get_container_pool()

    if not container_pool:
        return jsonify({"message": "No active proxy containers."})

    # Serialize the container pool information
    serialized_pool = [
        {
            "container_name": container_info[0],  # container_name from the first column
            "country": container_info[1],         # country from the second column
            "external_port": container_info[2]    # external_port from the third column
        }
        for container_info in container_pool
    ]
    
    return jsonify({"available_proxies": serialized_pool})



@proxy_blueprint.route('/list-container-records', methods=['GET'])
def list_container_records():
    """List all container records including their start and stop times."""
    conn = sqlite3.connect(get_db_path())
    cursor = conn.cursor()
    
    # Select all records from the proxy_containers table
    cursor.execute('''SELECT * FROM proxy_containers''')
    containers = cursor.fetchall()
    conn.close()

    # Render the HTML template and pass the container records to it
    return render_template('list_container_records.html', containers=containers)

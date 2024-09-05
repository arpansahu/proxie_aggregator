import atexit
from project import create_app, ext_celery
from project.proxy.utils import init_db, stop_all_proxies, start_initial_proxies

# Create the Flask app
app = create_app()

# Create the Celery app
celery = ext_celery.celery

# Function to stop all proxies within the app context during shutdown
def shutdown_cleanup():
    with app.app_context():
        stop_all_proxies()

# Register the shutdown cleanup function to stop proxies on exit
atexit.register(shutdown_cleanup)

if __name__ == "__main__":
    # Initialize the database and start initial proxies within the app context
    with app.app_context():
        init_db()  # Initialize the SQLite database schema
        start_initial_proxies()  # Start the initial proxy containers
    
    # Run the Flask app
    app.run(host="0.0.0.0", port=8489)
import os
from flask import Flask
from flask_celeryext import FlaskCeleryExt
from project.celery_utils import make_celery
from project.config import config

# Celery extension
ext_celery = FlaskCeleryExt(create_celery_app=make_celery)

def create_app(config_name=None):
    # Determine config
    if config_name is None:
        config_name = os.environ.get('FLASK_CONFIG', 'development')

    # Create the Flask app
    app = Flask(__name__)

    # Set config
    app.config.from_object(config[config_name])

    # Initialize Celery
    ext_celery.init_app(app)

    # Register blueprints
    from project.proxy import proxy_blueprint
    app.register_blueprint(proxy_blueprint)

    return app
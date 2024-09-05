from flask import Blueprint

proxy_blueprint = Blueprint("proxy", __name__, template_folder="templates")

from . import views  # Import the views to attach routes
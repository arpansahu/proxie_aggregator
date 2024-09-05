import os
from pathlib import Path

class BaseConfig:
    """Base configuration"""
    BASE_DIR = Path(__file__).parent.parent
    TESTING = False
    CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
    CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")
    
    # Move DB_PATH here
    DB_PATH = os.environ.get("DB_PATH", str(BASE_DIR / "proxy_container_records.db"))

class DevelopmentConfig(BaseConfig):
    """Development configuration"""
    DEBUG = True

class ProductionConfig(BaseConfig):
    """Production configuration"""
    DEBUG = False

config = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
}
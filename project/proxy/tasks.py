from celery import shared_task
from project.proxy.utils import stop_and_remove_container, start_gluetun_container, get_container_pool
from flask import current_app

@shared_task
def rotate_container():
    with current_app.app_context():
        # Fetch the current container pool
        pool = get_container_pool()
        if pool:
            oldest_container = pool[0]
            stop_and_remove_container(oldest_container[0])
            print(f"Rotating container {oldest_container[0]}")
            start_gluetun_container()
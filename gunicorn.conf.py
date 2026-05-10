import multipro
import os

bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8001")
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "sync"
worker_connections = 1000
timeout = 30
keepalive = 2

accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")

max_requests = 1000
max_requests_jitter = 50

preload_app = True

raw_env = [
    "FLASK_ENV=production",
]

def on_starting(server):
    pass

def on_reload(server):
    pass

def worker_int(worker):
    pass

def worker_abort(worker):
    pass
import os

# Render provides PORT dynamically. Gunicorn must bind to all interfaces
# so Render can detect the service and route traffic to it.
bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"
workers = 1
timeout = 120
accesslog = '-'
errorlog = '-'

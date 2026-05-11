#!/bin/bash
cd /home/dm/recipes
# If using a virtualenv (highly recommended):
source /home/dm/recipes/.venv/bin/activate
exec gunicorn --workers 3 --bind 0.0.0.0:5000 app:app
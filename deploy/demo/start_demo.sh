#!/bin/sh
set -eu

# Refuse a non-demo database before the shared entrypoint can run migrations.
python -c 'from app.services.demo import require_demo, validate_demo_database; require_demo(); validate_demo_database()'
exec sh scripts/start_server.sh

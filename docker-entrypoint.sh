#!/bin/sh
set -e

# Applied before every command, not just the default one, so a one-off
# `docker run mlb-visualizer poetry run python scripts/import_team_season.py ...`
# lands on a migrated schema exactly like starting the app does.
alembic upgrade head

exec "$@"

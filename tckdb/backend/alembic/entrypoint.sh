#!/bin/bash
set -e  # Exit immediately if a command exits with a non-zero status.

# Initialize Conda
source /opt/conda/etc/profile.d/conda.sh

# Activate the Conda environment
conda activate tck_env

# Print current working directory for debugging
echo "Current working directory: $(pwd)"

# List files to verify `alembic.ini` presence
echo "Files in current directory:"
ls -la

# List files in tckd/backend directory
echo "Files in tckdb/backend directory:"
ls -la /code/tckdb/backend

# Wait for PostgreSQL to be ready
echo "Waiting for PostgreSQL to start..."
while ! nc -z db 5432; do
  sleep 0.1
done
echo "PostgreSQL started."

# Run Alembic migrations with explicit config path
echo "Running Alembic migrations..."
alembic -c /code/tckdb/backend/alembic.ini upgrade head
echo "Database is up to date!"

# Keep the container running (optional)
tail -f /dev/null

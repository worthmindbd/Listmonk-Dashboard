#!/bin/bash
echo "Starting ListMonk Dashboard..."

# Check if port 8000 is already in use
if lsof -i :8000 > /dev/null; then
    echo "⚠️ Port 8000 is already in use. Please stop the existing server first."
    exit 1
fi

# Activate virtual environment
source venv/bin/activate

# Start the server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

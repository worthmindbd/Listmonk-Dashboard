#!/bin/bash
set -euo pipefail

echo "Starting ListMonk Dashboard..."

if [ ! -d "venv" ]; then
    echo "Error: virtual environment not found. Run 'python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt' first."
    exit 1
fi

# Check if port 8000 is already in use by a previous instance of THIS app and
# stop it. Only uvicorn processes are killed — an unrelated service that
# happens to listen on :8000 must not be terminated.
STALE_PIDS=$(lsof -ti :8000 2>/dev/null || true)
if [ -n "$STALE_PIDS" ]; then
    OUR_PIDS=""
    for pid in $STALE_PIDS; do
        if ps -p "$pid" -o command= 2>/dev/null | grep -q "uvicorn"; then
            OUR_PIDS="$OUR_PIDS $pid"
        fi
    done
    if [ -n "$OUR_PIDS" ]; then
        echo "⚠️ Port 8000 is already in use by a previous uvicorn instance. Stopping it..."
        kill $OUR_PIDS 2>/dev/null || true
        sleep 1
        # Force kill if still running
        STILL_RUNNING=""
        for pid in $OUR_PIDS; do
            if kill -0 "$pid" 2>/dev/null; then STILL_RUNNING="$STILL_RUNNING $pid"; fi
        done
        if [ -n "$STILL_RUNNING" ]; then
            kill -9 $STILL_RUNNING 2>/dev/null || true
            sleep 1
        fi
        echo "✅ Previous process stopped."
    else
        echo "⚠️ Port 8000 is in use by another process (not uvicorn); leaving it alone."
        exit 1
    fi
fi

# Activate virtual environment
source venv/bin/activate

# Start the server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

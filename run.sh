#!/bin/bash

# Purplle Store Intelligence System — Service Runner
# Automatically handles virtualenv setup, requirements installation, and launches both services.

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

API_PORT=8000
DASHBOARD_PORT=3000

echo "=========================================================="
echo "💅 Starting Purplle Store Intelligence System Services..."
echo "=========================================================="

# 1. Setup Virtual Environment
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment 'venv'..."
    python3 -m venv venv
else
    echo "✓ Virtual environment found."
fi

echo "🔌 Activating virtual environment..."
source venv/bin/activate

# 2. Install/Upgrade Dependencies
echo "📥 Installing dependencies from requirements.txt..."
pip install --upgrade pip
pip install -r requirements.txt

# 3. Launch Services
echo "🚀 Launching FastAPI backend on http://localhost:$API_PORT..."
# Disable stdout buffering so we get clean logs instantly
PYTHONUNBUFFERED=1 uvicorn app.main:app --host 0.0.0.0 --port $API_PORT > api.log 2>&1 &
API_PID=$!

echo "🚀 Launching Dashboard static server on http://localhost:$DASHBOARD_PORT..."
python3 -m http.server $DASHBOARD_PORT --directory dashboard > dashboard.log 2>&1 &
DASHBOARD_PID=$!

# Graceful cleanup on exit
cleanup() {
    echo ""
    echo "🧹 Stopping services (PIDs: API=$API_PID, Dashboard=$DASHBOARD_PID)..."
    kill $API_PID $DASHBOARD_PID 2>/dev/null || true
    echo "✓ Services stopped."
}
trap cleanup EXIT SIGINT SIGTERM

# 4. Wait & Check Service Health
echo "⏳ Waiting for services to initialize..."
sleep 3

# Check if servers are running
if ps -p $API_PID > /dev/null && ps -p $DASHBOARD_PID > /dev/null; then
    echo "=========================================================="
    echo "🚀 BOTH SERVICES RUNNING SUCCESSFULLY!"
    echo "=========================================================="
    echo "📱 API Swagger UI: http://localhost:$API_PORT/docs"
    echo "🖥️ Live Dashboard:  http://localhost:$DASHBOARD_PORT"
    echo "=========================================================="
    echo "Metrics verification check:"
    curl -s "http://localhost:$API_PORT/stores/ST1008/metrics?date=2026-04-10" | grep -q "unique_visitors" && echo "  ✓ Database health check: PASSED" || echo "  ⚠️ Database health check: FAILED"
    echo "Press [Ctrl+C] to stop both servers."
    echo "=========================================================="
else
    echo "❌ Error: One or both services failed to start."
    echo "API log (api.log):"
    tail -n 10 api.log || true
    echo "Dashboard log (dashboard.log):"
    tail -n 10 dashboard.log || true
    exit 1
fi

# Keep script running to maintain services
while true; do
    sleep 1
done

#!/bin/bash
# Stop ReadabilityRSS backend + web servers

echo "Stopping ReadabilityRSS services..."

pkill -f "uvicorn.*8001" 2>/dev/null && echo "Backend stopped" || echo "Backend not running"

# Reaps the pre-consolidation static servers on 3000/3010/3020. They are no longer
# started, but a host still running them from before the single-origin switch would
# otherwise keep them forever. Scoped to this project so it cannot hit another one.
pkill -f "serve_spa.py.*readabilityrss" 2>/dev/null && echo "Legacy static servers stopped" || true

# Give processes a moment to exit gracefully, then force-kill any survivors
sleep 2
pkill -9 -f "uvicorn.*8001" 2>/dev/null || true
pkill -9 -f "serve_spa.py.*readabilityrss" 2>/dev/null || true

# Clear the port to ensure clean restart
fuser -k 8001/tcp 2>/dev/null || true

sleep 1
echo "Done!"

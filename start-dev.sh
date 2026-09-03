#!/bin/bash
# Start backend plus production-like frontend/reader servers (no dev servers).

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Starting ReadabilityRSS (Backend + Web Servers)"
echo "=================================================="

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Kill any existing processes on the required ports
echo -e "${YELLOW}Cleaning up existing processes...${NC}"
pkill -f "uvicorn.*8001" 2>/dev/null || true
# Ensure the port is free
fuser -k 8001/tcp 2>/dev/null || true
sleep 2

# Load backend environment variables
if [ -f "$PROJECT_DIR/backend/.env" ]; then
    set -a
    source "$PROJECT_DIR/backend/.env"
    set +a
    echo -e "${GREEN}Loaded backend/.env${NC}"
fi

# Start Backend
echo -e "${BLUE}Starting Backend (Port 8001)...${NC}"
cd "$PROJECT_DIR"
nohup python3 -c "
import sys
sys.path.insert(0, '$PROJECT_DIR')
import uvicorn
uvicorn.run('backend.app.main:app', host='0.0.0.0', port=8001, reload=False)
" > /tmp/readabilityrss-backend.log 2>&1 &
BACKEND_PID=$!
echo -e "${GREEN}Backend started (PID: $BACKEND_PID)${NC}"

# Wait for backend to be ready
echo "Waiting for backend to start..."
sleep 3

# Build Frontend (production-like). The backend serves frontend/build at /manage.
echo -e "${BLUE}Building Frontend...${NC}"
cd "$PROJECT_DIR/frontend"
# REACT_APP_API_URL is passed explicitly rather than left to the source default:
# an untracked frontend/.env.local outranks .env in CRA, and only a real shell
# var outranks that. It must be non-empty — Windows cannot pass an empty one.
PUBLIC_URL=/manage \
REACT_APP_API_URL=/api \
npm run build > /tmp/readabilityrss-frontend-build.log 2>&1

# Build Reader (production-like)
echo -e "${BLUE}Building Reader...${NC}"
cd "$PROJECT_DIR/reader"
VITE_API_URL= npm run build > /tmp/readabilityrss-reader-build.log 2>&1

echo ""
echo -e "${GREEN}=================================================="
echo "ReadabilityRSS is running!"
echo "=================================================="
echo -e "Reader:     ${BLUE}http://localhost:8001/${NC}"
echo -e "Management: ${BLUE}http://localhost:8001/manage/${NC}"
echo ""
echo "One process serves everything on port 8001:"
echo "  Reader:     http://localhost:8001/"
echo "  Management: http://localhost:8001/manage/"
echo "  API:        http://localhost:8001/api"
echo ""
echo "Logs:"
echo "  Backend:  tail -f /tmp/readabilityrss-backend.log"
echo "  Frontend build: tail -f /tmp/readabilityrss-frontend-build.log"
echo "  Reader build:   tail -f /tmp/readabilityrss-reader-build.log"
echo ""
echo "To stop: bash stop-dev.sh   (or kill $BACKEND_PID)"
echo -e "${NC}"

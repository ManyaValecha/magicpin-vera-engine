#!/bin/bash

# Vera Growth Engine - Unified Startup Script

echo "🚀 Starting Vera Growth Engine..."

# 1. Setup Backend
echo "📦 Setting up backend..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate
pip install -r requirements.txt

# 2. Setup Frontend
echo "📦 Setting up frontend..."
cd frontend
if [ ! -d "node_modules" ]; then
    npm install
fi

# 3. Launch Both
echo "⚡ Launching Vera Dashboard & Brain..."
cd ..
# Run backend in background, then run frontend
python app.py & 
cd frontend && npm run dev

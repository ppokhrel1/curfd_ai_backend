#!/bin/bash
set -e

echo "⏳ Waiting for ngrok sidecar to establish tunnel..."
# Try to fetch the URL from the ngrok service API
# We use the service name 'ngrok' as the hostname
MAX_RETRIES=10
COUNT=0

while [ $COUNT -lt $MAX_RETRIES ]; do
  NGROK_URL=$(curl -s http://ngrok:4040/api/tunnels | jq -r '.tunnels[0].public_url')
  
  if [ "$NGROK_URL" != "null" ] && [ -n "$NGROK_URL" ]; then
    echo "✅ Ngrok Tunnel found: $NGROK_URL"
    export BACKEND_URL=$NGROK_URL
    break
  fi
  
  echo "...ngrok not ready yet, retrying in 2s ($COUNT/$MAX_RETRIES)..."
  sleep 2
  COUNT=$((COUNT+1))
done

# Start the actual FastAPI server
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --reload-exclude "generated_files/*"
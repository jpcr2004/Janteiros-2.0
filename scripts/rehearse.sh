#!/bin/sh
# Final rehearsal on arena-sim in real time: two waves, all cases in parallel. Usage: scripts/rehearse.sh [port] [tag]
PORT=${1:-8010}; TAG=${2:-r}
mkdir -p runs/rehearsal
uv run arena-sim serve --port $PORT --data data/package --final-rehearsal --rehearsal-wait 15 --rehearsal-cap 240 --rehearsal-break 20 > runs/rehearsal/sim-$TAG.log 2>&1 &
SIM=$!
sleep 5
KEY=$(curl -s -X POST localhost:$PORT/v1/register -H 'content-type: application/json' -d '{"team_name":"janteiros","event_code":"sim"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['api_key'])")
ARENA_URL=http://localhost:$PORT ARENA_API_KEY=$KEY uv run jt run --set final --waves 2 > runs/rehearsal/run-$TAG.log 2>&1
echo "exit $?"
kill $SIM
cat runs/rehearsal/run-$TAG.log | cut -c1-200

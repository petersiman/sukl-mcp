#!/usr/bin/env bash
# Spustí SÚKL MCP server lokálně.
# Použití: ./run_server.sh [port]   (výchozí port: 8000)

PORT=${1:-8000}
PYTHON=/opt/homebrew/bin/python3.11

echo "Spouštím SÚKL MCP server na portu $PORT..."
$PYTHON "$(dirname "$0")/sukl_mcp_server.py" "$PORT"

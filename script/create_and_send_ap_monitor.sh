#!/bin/bash
# Transfer the tarball to the remote server

set -e

if [ ! -d "ap_monitor" ]; then
  echo "Error: ap_monitor directory not found. Please run this script from the project root directory." >&2
  exit 1
fi

# Variables
REMOTE_USER="statclcn"
REMOTE_HOST="statifi.netops.yorku.ca"
REMOTE_DIR="/home/statclcn/client_count"
TAR_NAME="ap_monitor.tar.gz"

find ap_monitor -type d -name "__pycache__" -exec rm -rf {} +

COPYFILE_DISABLE=1 tar -czvf "$TAR_NAME" ap_monitor

scp "$TAR_NAME" "$REMOTE_USER@$REMOTE_HOST:$REMOTE_DIR"

rm -f "$TAR_NAME"

echo "Done! Archive created, transferred, and cleaned up." 
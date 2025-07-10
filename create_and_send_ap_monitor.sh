#!/bin/bash

# Exit on error
set -e

# Variables (edit these as needed)
REMOTE_USER="statclcn"
REMOTE_HOST="statifi.netops.yorku.ca"
REMOTE_DIR="/home/statclcn/client_count"
TAR_NAME="ap_monitor.tar.gz"

# 1. Remove all __pycache__ directories
find ap_monitor -type d -name "__pycache__" -exec rm -rf {} +

# 2. Create a clean tar.gz archive, excluding macOS metadata
COPYFILE_DISABLE=1 tar -czvf "$TAR_NAME" ap_monitor

# 3. Transfer the tarball to the remote server
scp "$TAR_NAME" "$REMOTE_USER@$REMOTE_HOST:$REMOTE_DIR"

# 4. Remove the tarball locally
rm -f "$TAR_NAME"

echo "Done! Archive created, transferred, and cleaned up." 
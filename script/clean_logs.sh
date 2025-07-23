#!/bin/bash
# Clean logs in the Logs/ directory

LOGS_DIR="Logs"
DIAG_DIR="$LOGS_DIR/diagnostics"

# Truncate ap-monitor.log to last 100 lines if it exists
if [ -f "$LOGS_DIR/ap-monitor.log" ]; then
    tail -n 100 "$LOGS_DIR/ap-monitor.log" > "$LOGS_DIR/ap-monitor.log.tmp" && mv "$LOGS_DIR/ap-monitor.log.tmp" "$LOGS_DIR/ap-monitor.log"
    echo "Truncated $LOGS_DIR/ap-monitor.log to last 100 lines."
else
    echo "$LOGS_DIR/ap-monitor.log does not exist."
fi

# Remove diagnostics.log if it exists
if [ -f "$DIAG_DIR/diagnostics.log" ]; then
    rm -f "$DIAG_DIR/diagnostics.log"
    echo "Removed $DIAG_DIR/diagnostics.log."
else
    echo "$DIAG_DIR/diagnostics.log does not exist."
fi

# Remove diagnostics_incomplete.json if it exists
if [ -f "$DIAG_DIR/diagnostics_incomplete.json" ]; then
    rm -f "$DIAG_DIR/diagnostics_incomplete.json"
    echo "Removed $DIAG_DIR/diagnostics_incomplete.json."
else
    echo "$DIAG_DIR/diagnostics_incomplete.json does not exist."
fi

echo "Logs cleaned in $LOGS_DIR." 
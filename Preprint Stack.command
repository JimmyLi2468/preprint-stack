#!/bin/bash
# Double-click to start Preprint Stack; it opens in your browser. Close this window to stop it.
cd "$(dirname "$0")"
exec python3 server.py "$@"

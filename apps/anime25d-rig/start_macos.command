#!/bin/sh
set -eu
cd -- "$(dirname -- "$0")"
exec python3 obs_server.py --port 8016 --open-browser

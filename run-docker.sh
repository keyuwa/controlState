#!/bin/sh
set -eu
cd "$(dirname "$0")"
# stdin avoids Docker VM bind-mount access to macOS Documents.
docker run --rm -i --network none python:3.12 python - < demo.py

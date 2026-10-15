#!/bin/sh
# Build Apprentice.app into dist/ and zip it for the installer prompt (INSTALL.md). Doesn't touch ~/Applications.
set -e
cd "$(dirname "$0")/.."
rm -rf dist; mkdir dist
APP="$PWD/dist/Apprentice.app" scripts/build.sh >/dev/null
ditto -c -k --keepParent dist/Apprentice.app dist/Apprentice.zip
rm -rf dist/Apprentice.app
echo "$PWD/dist/Apprentice.zip"

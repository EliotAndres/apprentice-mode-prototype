#!/bin/sh
# Developer ID build, notarized and stapled, zipped into dist/Apprentice.zip, then published as a GitHub release.
# Needs a "Developer ID Application" certificate in the keychain and a notarytool profile:
#   xcrun notarytool store-credentials apprentice-notary ...
# Usage: scripts/release.sh v0.1.0
set -e
cd "$(dirname "$0")/.."
TAG="${1:?usage: scripts/release.sh <tag>}"
IDENTITY="${IDENTITY:-$(security find-identity -v -p codesigning | sed -n 's/.*"\(Developer ID Application: [^"]*\)".*/\1/p' | head -1)}"
[ -n "$IDENTITY" ] || { echo "no Developer ID Application certificate in the keychain"; exit 1; }
PROFILE="${NOTARY_PROFILE:-apprentice-notary}"
rm -rf dist; mkdir dist
APP="$PWD/dist/Apprentice.app" SIGN_ID="$IDENTITY" CODESIGN_FLAGS="--options runtime --timestamp" scripts/build.sh >/dev/null
ditto -c -k --keepParent dist/Apprentice.app dist/Apprentice.zip
xcrun notarytool submit dist/Apprentice.zip --keychain-profile "$PROFILE" --wait
xcrun stapler staple dist/Apprentice.app
spctl -a -vv dist/Apprentice.app
rm dist/Apprentice.zip; ditto -c -k --keepParent dist/Apprentice.app dist/Apprentice.zip  # re-zip with the ticket
gh release create "$TAG" dist/Apprentice.zip --title "Apprentice $TAG" --notes "Signed with Developer ID and notarized. Unzip, move Apprentice.app to ~/Applications, open it, and allow Screen Recording, Accessibility and Input Monitoring."

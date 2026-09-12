#!/usr/bin/env bash
#
# Generate, validate, sign, and stage all three Brightwheel shortcuts.
#
#   ./build.sh
#
# Writes both the unsigned .xml and the signed .shortcut into dist/, which is
# gitignored. Not for privacy — the shortcut asks Brightwheel who the children
# are, so a build carries none — but because every build mints fresh UUIDs, so
# all six files change completely each time and the churn never deltas. The
# built shortcuts ship as release assets instead. The signer also archives a
# timestamped copy of the unsigned XML into dist/<date>/, next to the build.
#
# The validating and signing happen inside build_shortcuts.py, through
# shortcut-forge: the waived validator rules are listed there, each with its
# reason, and the structural checks run before anything is validated.
set -euo pipefail
cd "$(dirname "$0")"

# --debug bakes .env values in and drops the setup questions, for fast test
# cycles. Its output carries real credentials, so it goes to a gitignored
# directory and never to dist/.
if [ "${1:-}" = "--debug" ]; then
    DIST="dist-debug"
    GEN_ARGS="--debug"
else
    DIST="dist"
    GEN_ARGS=""
fi

rm -rf "$DIST"
mkdir -p "$DIST"

uv run python build_shortcuts.py "$DIST" $GEN_ARGS

echo
if [ "$DIST" = "dist-debug" ]; then
    echo "DEBUG artifacts in $DIST/ — credentials are baked in, and so is the"
    echo "signer's XML archive under $DIST/. Do NOT commit or share these. Run"
    echo "./build.sh with no arguments for a clean build before committing."
else
    echo "Artifacts in $DIST/. Not committed — every build mints fresh UUIDs,"
    echo "so nothing here would ever delta. Share them from a release instead."
fi
echo "Reminder: delete the old shortcut on the phone before re-importing;"
echo "a same-name import is silently skipped."

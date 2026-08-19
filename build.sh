#!/usr/bin/env bash
#
# Generate, validate, sign, and stage all three Brightwheel shortcuts.
#
#   ./build.sh
#
# Writes both the unsigned .xml and the signed .shortcut into dist/, which is
# committed. sign-shortcut also archives a timestamped copy of the unsigned XML
# into the Shortcuts Playground output directory; that archive is incidental and
# lives outside this repo.
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
OUTPUT_DIR="${CLAUDE_PLUGIN_OPTION_OUTPUT_DIR:-$HOME/Documents/Shortcuts Playground}"

# The Shortcuts Playground attribution comment was deliberately removed, so this
# one validator rule is expected to fail for every shortcut. Any other reported
# error is real and must stop the build.
WAIVED="Shortcuts Playground prompt text"

rm -rf "$DIST"
mkdir -p "$DIST"

python3 build_shortcuts.py "$DIST" $GEN_ARGS
echo

for xml in "$DIST"/*.xml; do
    name="$(basename "$xml" .xml)"
    printf '%-24s ' "$name"

    report="$(validate-shortcut "$xml" --target-macos 27 --target-platform ios 2>&1 || true)"
    unexpected="$(printf '%s\n' "$report" | grep '^- ' | grep --invert-match "$WAIVED" || true)"
    if [ -n "$unexpected" ]; then
        echo "FAILED"
        printf '%s\n' "$unexpected"
        exit 1
    fi

    sign-shortcut "$xml" --name "$name" >/dev/null
    cp "$OUTPUT_DIR/$name.shortcut" "$DIST/$name.shortcut"
    echo "validated, signed -> $DIST/$name.shortcut"
done

echo
if [ "$DIST" = "dist-debug" ]; then
    echo "DEBUG artifacts in $DIST/ — credentials are baked in."
    echo "Do NOT commit or share these. Run ./build.sh with no arguments for a"
    echo "clean build before committing."
else
    echo "Artifacts in $DIST/ — commit them alongside any generator change."
fi
echo "Reminder: delete the old shortcut on the phone before re-importing;"
echo "a same-name import is silently skipped."

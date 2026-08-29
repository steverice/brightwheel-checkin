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
# built shortcuts ship as release assets instead. sign-shortcut also archives a
# timestamped copy of the unsigned XML into the Shortcuts Playground output
# directory; that archive is incidental and lives outside this repo.
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

# Validator errors that are expected and deliberate. Anything else is real and
# must stop the build.
#   1. The Shortcuts Playground attribution comment was removed on purpose.
#   2. is.workflow.actions.scanbarcode, on two counts. It has no row in the
#      bundled iOS 27 ToolKit snapshot, so the validator calls it macOS-only,
#      and the validator also demands an imageFile parameter. Neither holds for
#      the iOS live scanner, which takes WFScanCodeActionMode and no image at
#      all. Both are contradicted by a working shortcut exported off an iOS 27
#      phone; the docs describe the macOS scan-an-image variant.
#   3. Glyphs 62021 and 62022 (a plane departing / arriving). The validator
#      checks against a 507-entry mapping; the device's own icon picker offers
#      far more than that. Both numbers came out of real shortcuts built on a
#      device, and both were rendered on a simulator to confirm. Waived by
#      number rather than by rule, so a genuine typo in a glyph still fails.
#   4. "Unit conversion detected". The wrappers carry the setup diagram as a
#      base64 string, and ~100k characters of it trip the heuristic that
#      looks for unit words in action text. There is no measurement action
#      in either wrapper.
#   5. The two comment-block rules. Both exist to keep the Shortcuts Playground
#      attribution comment in the file; that comment was removed on purpose, so
#      the second action is no longer a prompt block and the wrappers sit one
#      under the density threshold. The comments that remain each explain a
#      real block, and adding a fourth to satisfy a ratio would be noise in a
#      shortcut whose working part is two actions.
WAIVED="Shortcuts Playground prompt text|is\.workflow\.actions\.scanbarcode|Scan QR or Barcode missing imageFile|WFWorkflowIconGlyphNumber (62021|62022) is not in the official|Unit conversion detected|Second action must be the prompt Comment block|Insufficient Comment blocks"

rm -rf "$DIST"
mkdir -p "$DIST"

python3 build_shortcuts.py "$DIST" $GEN_ARGS
echo

for xml in "$DIST"/*.xml; do
    name="$(basename "$xml" .xml)"
    printf '%-24s ' "$name"

    report="$(validate-shortcut "$xml" --target-macos 27 --target-platform ios 2>&1 || true)"
    unexpected="$(printf '%s\n' "$report" | grep '^- ' | grep --extended-regexp --invert-match "$WAIVED" || true)"
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
    echo "Artifacts in $DIST/. Not committed — every build mints fresh UUIDs,"
    echo "so nothing here would ever delta. Share them from a release instead."
fi
echo "Reminder: delete the old shortcut on the phone before re-importing;"
echo "a same-name import is silently skipped."

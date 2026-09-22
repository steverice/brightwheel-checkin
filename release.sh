#!/usr/bin/env bash
#
# Cut a release: build, sign, and attach the artifacts people actually install.
#
#   ./release.sh v1.3.0                     # draft, for you to review and publish
#   ./release.sh v1.3.0 --publish           # publish straight away
#   ./release.sh v1.3.0 --notes-file NOTES.md
#
# Only the zip goes up. GitHub replaces the spaces in an asset's name with dots,
# and an imported shortcut is named after its file, so a loose
# "Brightwheel.Check.In.shortcut" installs as "Brightwheel.Check.In" — and the
# wrappers, which find Brightwheel Attendance by name, can't find it. The zip is
# built here, so its entries keep their spaces. Phones install from the page's
# iCloud links, which carry the names too. See ARCHITECTURE.md.
#
# Drafts are the default because publishing a release is public and hard to take
# back. Nothing here signs anything itself — build_shortcuts.py does, pinned to
# --mode anyone so the artifacts import for people who have never met you.
#
# A draft has no tag yet: gh creates it when the draft is published, at whatever
# main's HEAD is then. So publish before pushing anything else — a commit pushed
# in between takes the tag, and the release then points at a commit that did not
# build the zip attached to it. Publishing first is what puts the tag on the
# build. The page update is the usual thing waiting behind this, and it has to
# wait anyway: its version badge links to the tag, which 404s until then.
set -euo pipefail
cd "$(dirname "$0")"

TAG="${1:-}"
if [ -z "$TAG" ]; then
    echo "usage: ./release.sh <tag> [--publish] [--notes-file PATH]" >&2
    exit 1
fi
shift

DRAFT="--draft"
NOTES=()
while [ $# -gt 0 ]; do
    case "$1" in
        --publish)    DRAFT=""; shift ;;
        --notes-file) NOTES=(--notes-file "$2"); shift 2 ;;
        *)            echo "unknown argument: $1" >&2; exit 1 ;;
    esac
done

if [ ${#NOTES[@]} -eq 0 ]; then
    NOTES=(--generate-notes)
fi

./build.sh

ZIP="dist/brightwheel-shortcuts.zip"
rm -f "$ZIP"
# --junk-paths so the zip has no dist/ prefix inside it. -X leaves out the
# resource forks that make a Mac-built zip look like junk everywhere else; it
# has no long form in the zip macOS ships, which is the only reason it is short.
zip --junk-paths -X --quiet "$ZIP" dist/*.shortcut

assets=("$ZIP")
echo
echo "attaching:"
printf '  %s\n' "${assets[@]}"
echo

# shellcheck disable=SC2086  # DRAFT is deliberately unquoted: it is a flag or nothing
gh release create "$TAG" $DRAFT --title "$TAG" "${NOTES[@]}" "${assets[@]}"

echo
if [ -n "$DRAFT" ]; then
    echo "Draft created. Review it, then publish from the Releases page"
    echo "or with: gh release edit $TAG --draft=false"
    echo "Publish before pushing anything else to main: the tag is made at"
    echo "publish time from main's HEAD, so a commit pushed first takes it."
fi

# The site links each shortcut by iCloud link, and an iCloud link is frozen at
# the moment it was shared — a new release does not reach the old ones. Ask now,
# while cutting the release is still fresh, rather than leaving the page quietly
# serving the previous version.
#
# The links are minted in a throwaway clone of the macOS 26 guest, never on a
# library holding a copy anyone set up; the README's "Mint links from a library
# that holds none of your own copies" says why, and where the procedure lives.
echo
echo "The page's iCloud links still point at the previous build. To mint $TAG's:"
echo "  1. In a fresh clone of the macOS 26 guest, import dist/*.shortcut."
echo "  2. Copy its library there, and check the copy here before minting:"
echo "       sqlite3 ~/Library/Shortcuts/Shortcuts.sqlite \".backup /tmp/library.sqlite\""
echo "       uv run python tools/check_library.py --database <that copy>"
echo "     A non-zero exit means the library would publish a configured or stale copy."
echo "  3. Run Brightwheel Share Links there, save what it copied (pbpaste > links.html),"
echo "     and bring that file back."
echo
# `|| true`: a closed stdin (a pipe, CI) reads as "skip" instead of ending the
# script under `set -e` before it can say what to run later.
read -r -p "Path to the saved links, 'clipboard' for this Mac's, or Enter to skip: " links || true
case "${links:-}" in
    "")
        echo "Skipped. Run these whenever you have them:"
        echo "  uv run python tools/verify_links.py --file links.html"
        echo "  uv run python tools/update_links.py --file links.html --version $TAG"
        ;;
    *)
        if [ "$links" = "clipboard" ]; then
            source=(--clipboard)
        else
            source=(--file "$links")
        fi
        # Check before writing. A link can carry the wrong build or arrive
        # without its setup questions, and both failures are silent — the
        # shortcut installs in one tap, looks right, and never asks for
        # credentials. Comparing each link's record against dist/ is how to
        # see it before a parent does. The badge names this release, not
        # whatever GitHub calls latest, which a draft is not.
        if uv run python tools/verify_links.py "${source[@]}"; then
            uv run python tools/update_links.py "${source[@]}" --version "$TAG" || true
        else
            echo
            echo "Links not written. Re-mint them and run:"
            echo "  uv run python tools/verify_links.py ${source[*]}"
            echo "  uv run python tools/update_links.py ${source[*]} --version $TAG"
        fi
        ;;
esac

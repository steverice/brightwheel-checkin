#!/usr/bin/env bash
#
# Integration tests: the real shortcuts, running in Shortcuts.app on an iOS 27
# simulator, against a mock Brightwheel.
#
#   ./test.sh                        # all tests, newest iOS 27 simulator
#   ./test.sh check_out              # only tests whose name contains this
#   ./test.sh --erase                # wipe the simulator library first
#   ./test.sh --runtime "iOS 26.5"   # run against another iOS version
#
# Nothing here can reach the real Brightwheel: the shortcuts are built with
# --api-base pointing at the local mock and with fake credentials baked in.
#
# Requires an iOS 27 iPhone simulator, and Accessibility permission for the
# terminal running this (System Settings -> Privacy & Security -> Accessibility)
# because taps are synthesized. See TESTING.md.
set -euo pipefail
cd "$(dirname "$0")"

RUNTIME="iOS 27"
if [ "${1:-}" = "--runtime" ]; then RUNTIME="$2"; fi
if ! xcrun simctl list runtimes | grep --quiet "$RUNTIME"; then
    echo "No $RUNTIME runtime installed — add one in Xcode > Settings > Components." >&2
    exit 1
fi

exec uv run python tests/test_attendance.py "$@"

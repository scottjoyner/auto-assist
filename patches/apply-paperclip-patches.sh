#!/usr/bin/env bash
# Apply local_trusted + Docker networking patches for Paperclip.
# Run after Paperclip version updates to re-apply the dev overrides.
set -euo pipefail

PAPERCLIP_DIR="${PAPERCLIP_DIR:-$HOME/git/hermes-agent/paperclip/server}"
PATCH_FILE="$(dirname "$0")/paperclip-local-trusted.patch"

if [ ! -d "$PAPERCLIP_DIR" ]; then
    echo "Error: Paperclip directory not found at $PAPERCLIP_DIR"
    echo "Set PAPERCLIP_DIR or clone the repo to the expected path."
    exit 1
fi

if [ ! -f "$PATCH_FILE" ]; then
    echo "Error: Patch file not found at $PATCH_FILE"
    exit 1
fi

cd "$PAPERCLIP_DIR"

# Check if patches are already applied
if grep -q "NOTE: Dev override" src/config.ts; then
    echo "Patches already applied to config.ts"
else
    echo "Applying config.ts patches..."
    patch -p1 < "$PATCH_FILE" 2>/dev/null || {
        # If patch doesn't apply cleanly, try with fuzz
        echo "Trying with fuzz factor..."
        patch -p1 -F 3 < "$PATCH_FILE" || {
            echo "ERROR: Failed to apply config.ts patches. Paperclip may have changed significantly."
            exit 1
        }
    }
fi

if grep -q "NOTE: Dev override" src/index.ts; then
    echo "Patches already applied to index.ts"
else
    echo "Applying index.ts patches..."
    patch -p1 < "$PATCH_FILE" 2>/dev/null || {
        echo "Trying with fuzz factor..."
        patch -p1 -F 3 < "$PATCH_FILE" || {
            echo "ERROR: Failed to apply index.ts patches. Paperclip may have changed significantly."
            exit 1
        }
    }
fi

echo "Paperclip patches applied successfully."
echo "Restart Paperclip: systemctl --user restart paperclip"

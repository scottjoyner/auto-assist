#!/bin/bash
# Research-only rootless Hermes entrypoint; not a production promotion.
set -euo pipefail

deny() {
    printf 'hermes rootless startup denied: %s\n' "$1" >&2
    exit 64
}

[ "$(id -u)" -ne 0 ] || deny 'root execution denied (UID 0)'
: "${HOME:?HOME required}"
: "${HERMES_HOME:?HERMES_HOME required}"
[ "${ENV_SEED+x}" != x ] || deny 'credential environment seeding'

# The Compose candidate fixes HOME to /workspace. Accept alternate homes only
# for isolated tests; their filesystem paths must already be canonical.
case "$HOME" in
    /*) ;;
    *) deny 'HOME must be absolute' ;;
esac
case "$HOME" in
    /|/root|/root/*|/etc|/etc/*|/proc|/proc/*|/sys|/sys/*|/dev|/dev/*)
        deny 'restricted HOME' ;;
esac
[ -d "$HOME" ] && [ -w "$HOME" ] || deny 'HOME must exist and be writable'
home_resolved="$(realpath -e -- "$HOME")" || deny 'HOME could not be resolved'
[ "$home_resolved" = "$HOME" ] || deny 'HOME alias or traversal'

# Prevent arbitrary absolute targets, ".." traversal and symlink escapes.
# The only allowed writable targets are direct children of HOME.
[ "$HERMES_HOME" = "$HOME/hermes-home" ] || deny 'Hermes home outside private HOME'
config_dir="${HERMES_CONFIG_HOME:-$HOME/.hermes}"
[ "$config_dir" = "$HOME/.hermes" ] || deny 'config outside private HOME'

safe_dir() {
    local target="$1"
    [ ! -L "$target" ] || deny 'symlinked writable directory'
    if [ -e "$target" ]; then
        [ -d "$target" ] || deny 'writable target is not a directory'
        [ "$(realpath -e -- "$target")" = "$target" ] ||
            deny 'directory path escapes HOME'
    else
        mkdir -- "$target" || deny 'cannot initialize private directory'
    fi
    [ ! -L "$target" ] && [ "$(realpath -e -- "$target")" = "$target" ] ||
        deny 'private directory changed during initialization'
}

umask 077
safe_dir "$HERMES_HOME"
safe_dir "$config_dir"

# Never overwrite or follow a preexisting config symlink to an arbitrary file.
[ ! -L "$HERMES_HOME/config.yaml" ] || deny 'symlinked primary config'
if [ ! -e "$HERMES_HOME/config.yaml" ]; then
    cp -- /app/hermes-home.defaults/config.yaml "$HERMES_HOME/config.yaml"
fi
[ -f "$HERMES_HOME/config.yaml" ] &&
    [ ! -L "$HERMES_HOME/config.yaml" ] || deny 'invalid primary config'

if [ -L "$config_dir/config.yaml" ]; then
    [ "$(readlink -- "$config_dir/config.yaml")" = "$HERMES_HOME/config.yaml" ] ||
        deny 'unexpected config symlink'
elif [ -e "$config_dir/config.yaml" ]; then
    deny 'unexpected pre-existing config file'
else
    ln -s -- "$HERMES_HOME/config.yaml" "$config_dir/config.yaml"
fi

for part in sessions logs cache harvest memories profiles tmp; do
    safe_dir "$HERMES_HOME/$part"
done

exec "$@"

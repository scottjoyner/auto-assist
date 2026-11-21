#!/bin/bash
# Research candidate: non-root Hermes startup without credential serialization.
# Not a drop-in production replacement until image build + Compose review.
set -eu
if [ "$(id -u)" -ne 0 ]; then
    : "${HOME:?HOME required}"
    : "${HERMES_HOME:?HERMES_HOME required}"
    case "$HOME" in /*) ;; *) echo 'HOME must be absolute' >&2; exit 64 ;; esac
    case "$HERMES_HOME" in /*) ;; *) echo 'HERMES_HOME must be absolute' >&2; exit 64 ;; esac
    case "$HERMES_HOME" in /root|/root/*|/etc|/etc/*) echo 'restricted Hermes home' >&2; exit 64 ;; esac
    if [ "${ENV_SEED+x}" = x ]; then
        echo 'non-root startup refuses credential environment seeding' >&2
        exit 64
    fi
    config_dir="${HERMES_CONFIG_HOME:-$HOME/.hermes}"
    case "$config_dir" in /*) ;; *) echo 'config directory must be absolute' >&2; exit 64 ;; esac
    case "$config_dir" in /root|/root/*|/etc|/etc/*) echo 'restricted config dir' >&2; exit 64 ;; esac
    umask 077
    mkdir -p "$HERMES_HOME" "$config_dir"
    if [ ! -f "$HERMES_HOME/config.yaml" ]; then
        cp /app/hermes-home.defaults/config.yaml "$HERMES_HOME/config.yaml"
    fi
    if [ ! -e "$config_dir/config.yaml" ]; then
        ln -s "$HERMES_HOME/config.yaml" "$config_dir/config.yaml"
    fi
    for part in sessions logs cache harvest memories profiles tmp; do
        mkdir -p "$HERMES_HOME/$part"
    done
    exec "$@"
fi

# Never allow this candidate image to fall back to root + credential seeding.
echo 'root execution denied in hardened Hermes candidate' >&2
exit 64

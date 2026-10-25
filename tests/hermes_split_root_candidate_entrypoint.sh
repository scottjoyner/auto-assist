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
    # Reject path traversal, host-root homes and all existing symlink
    # ancestors before *any* write. This is a userspace preflight, not a
    # kernel TOCTOU fence against concurrent path replacement.
    check_path() {
        local raw="$1" part current=""
        case "$raw" in
            /|*/|*//*|*/./*|*/../*|*/.|*/..|*$'\n'*|*$'\r'*|*$'\t'*)
                echo 'unsafe noncanonical workspace path' >&2; exit 64 ;;
            /*) ;;
            *) echo 'workspace paths must be absolute' >&2; exit 64 ;;
        esac
        local -a parts
        IFS='/' read -r -a parts <<< "$raw"
        for part in "${parts[@]}"; do
            [ -n "$part" ] || continue
            current="$current/$part"
            if [ -L "$current" ]; then
                echo 'symlinked workspace path denied' >&2
                exit 64
            fi
        done
    }
    case "$HOME" in /root|/root/*|/etc|/etc/*|/proc|/proc/*|/sys|/sys/*) echo 'restricted HOME' >&2; exit 64 ;; esac
    case "$HERMES_HOME" in "$HOME"/*) ;; *) echo 'Hermes home must be inside workspace HOME' >&2; exit 64 ;; esac
    case "$config_dir" in "$HOME"/*) ;; *) echo 'Hermes config must be inside workspace HOME' >&2; exit 64 ;; esac
    check_path "$HOME"
    check_path "$HERMES_HOME"
    check_path "$config_dir"
    # Do not seed unreviewed /app/hermes-home.defaults or environment data.
    # The rootless Hermes config must be present through separate custody.
    if [ ! -f "$HERMES_HOME/config.yaml" ] || [ -L "$HERMES_HOME/config.yaml" ]; then
        echo 'independently preprovisioned Hermes config required' >&2
        exit 64
    fi
    # Refuse preexisting redirected session/log/cache directories *before*
    # creating any new config alias or home subdirectories.
    for part in sessions logs cache harvest memories profiles tmp; do
        check_path "$HERMES_HOME/$part"
    done
    umask 077
    mkdir -p "$config_dir"
    if [ -e "$config_dir/config.yaml" ] || [ -L "$config_dir/config.yaml" ]; then
        if [ ! -L "$config_dir/config.yaml" ] ||
           [ "$(readlink "$config_dir/config.yaml")" != "$HERMES_HOME/config.yaml" ]; then
            echo 'unapproved preexisting Hermes config alias' >&2
            exit 64
        fi
    else
        ln -s "$HERMES_HOME/config.yaml" "$config_dir/config.yaml"
    fi
    for part in sessions logs cache harvest memories profiles tmp; do
        check_path "$HERMES_HOME/$part"
        mkdir -p "$HERMES_HOME/$part"
    done
    exec "$@"
fi

# Never allow this candidate image to fall back to root + credential seeding.
echo 'root execution denied in hardened Hermes candidate' >&2
exit 64

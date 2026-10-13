#!/bin/sh
set -eu
if [ ! -s /run/secrets/demo_users ] || [ ! -r /run/secrets/demo_users ]; then
    echo 'Refusing to serve: provision a nonempty Caddy user/password-hash file.' >&2
    exit 1
fi
exec "$@"

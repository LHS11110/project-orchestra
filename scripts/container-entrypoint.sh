#!/bin/sh
set -eu
# Microsoft ODBC uses the system trust store. Only public CA material is accepted.
if [ -n "${ORCHESTRA_SQL_CA:-}" ] && [ -f "$ORCHESTRA_SQL_CA" ]; then
    if grep -q 'PRIVATE KEY' "$ORCHESTRA_SQL_CA"; then
        echo 'SQL trust input must contain public certificates only.' >&2; exit 1
    fi
    cp "$ORCHESTRA_SQL_CA" /usr/local/share/ca-certificates/orchestra-sql.crt
    update-ca-certificates >/dev/null
fi
if [ -d /results ]; then chown 10001:10001 /results; fi
exec setpriv --reuid=10001 --regid=10001 --init-groups "$@"

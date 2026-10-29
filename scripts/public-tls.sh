#!/bin/sh
# Public edge certificates only. Internal service PKI is managed separately.
set -eu

action=${1:-status}
certificate=${2:-${ORCHESTRA_PUBLIC_TLS_CERT_NAME:-}}
certbot=${ORCHESTRA_CERTBOT_BIN:-/snap/bin/certbot}
case "$action" in
    status|renew|verify-renewal) ;;
    *) echo 'Usage: public-tls.sh {status|renew|verify-renewal} CERT_NAME' >&2; exit 2 ;;
esac
case "$certificate" in
    ''|.*|-*|*[!a-zA-Z0-9.:-]*) echo 'Provide an existing public certificate name (IP address or domain).' >&2; exit 2 ;;
esac
if [ ! -x "$certbot" ]; then
    echo 'Certbot is unavailable. Install Certbot 5.4+ or set ORCHESTRA_CERTBOT_BIN.' >&2
    exit 1
fi
version=$("$certbot" --version)
python3 - "$version" <<'PY'
import re
import sys
match = re.search(r'\b(\d+)\.(\d+)', sys.argv[1])
if not match or tuple(map(int, match.groups())) < (5, 4):
    sys.exit('Certbot 5.4+ is required for IP address webroot renewal. Use /snap/bin/certbot.')
PY

case "$action" in
    status)
        printf '%s\n' "$version"
        openssl x509 -in "/etc/letsencrypt/live/$certificate/fullchain.pem" \
            -noout -issuer -subject -dates -ext subjectAltName
        systemctl list-timers certbot.timer --all --no-pager
        ;;
    renew)
        exec "$certbot" renew --cert-name "$certificate" --no-random-sleep-on-renew
        ;;
    verify-renewal)
        exec "$certbot" renew --cert-name "$certificate" --dry-run \
            --run-deploy-hooks --no-random-sleep-on-renew
        ;;
esac

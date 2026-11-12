#!/bin/sh
# Run as root on hosts with a separate native SQL Server installation.
# Keep local SQL clients working; deny non-loopback TCP ingress on both IP families.
set -eu
ufw status | head -1 | grep -q 'Status: active' || { echo 'UFW must already be active; refusing to enable it or change SSH policy.' >&2; exit 1; }
ufw deny 1433/tcp

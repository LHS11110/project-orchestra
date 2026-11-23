#!/usr/bin/env bash
set -euo pipefail

sync_main=0
if [[ ${1:-} == --sync-main ]]; then sync_main=1; shift; fi
if [[ $# != 6 || ( $1 != --check && $1 != --allow-backend-restart ) || ( $1 == --check && $sync_main == 1 ) ]]; then
  echo "Usage: $0 [--sync-main] --check|--allow-backend-restart WORKSPACE FE_SHA BE_SHA WALL_SHA DB_SHA" >&2
  exit 2
fi
mode=$1
workspace=$(realpath "$2")
shift 2

if [[ $mode != --check ]]; then
  exec 9>"${XDG_RUNTIME_DIR:-/tmp}/agora-agent-release-${UID}.lock"
  flock -n 9 || { echo 'Another Agora agent release is running.' >&2; exit 1; }
fi

repos=(project-agora-FE project-agora-BE project-agora-Wall project-agora-DB)
revisions=("$@")
for index in "${!repos[@]}"; do
  repo="$workspace/${repos[$index]}"
  revision="${revisions[$index]}"
  [[ $revision =~ ^[a-f0-9]{40}$ ]] || { echo "Expected full Git SHA for $repo" >&2; exit 2; }
  [[ -z $(git -C "$repo" status --porcelain) ]] || { echo "Expected a clean checkout in $repo" >&2; exit 2; }
  if ((sync_main)); then
    [[ $(git -C "$repo" symbolic-ref --short HEAD) == main ]]
    git -C "$repo" fetch origin refs/heads/main
    [[ $(git -C "$repo" rev-parse FETCH_HEAD) == "$revision" ]]
    git -C "$repo" merge-base --is-ancestor HEAD "$revision"
  else
    [[ $(git -C "$repo" rev-parse HEAD) == "$revision" ]] ||
      { echo "Expected checkout at $revision in $repo" >&2; exit 2; }
  fi
done
if ((sync_main)); then
  for index in "${!repos[@]}"; do
    git -C "$workspace/${repos[$index]}" merge --ff-only "${revisions[$index]}"
  done
fi

fe="$workspace/project-agora-FE"
be="$workspace/project-agora-BE"
wall="$workspace/project-agora-Wall"
db="$workspace/project-agora-DB"
migration="$db/mssql/migrations/2026-11-23-agent-canvas-management.sql"

frontend() { (cd "$fe" && docker compose -f compose.yaml "$@"); }
backend() { (cd "$be" && docker compose -f docker-compose.backend.yml -f docker-compose.ollama.yml "$@"); }
broker() { (cd "$wall" && docker compose -f docker-compose.broker.yml "$@"); }
probe() {
  docker exec agora-nginx curl --fail --silent --show-error --connect-timeout 3 --max-time 10 \
    --cacert /etc/nginx/ssl/agora/ca.pem --output /dev/null "https://localhost$1"
}

[[ -r $migration ]]
frontend config --quiet
backend config --quiet
broker config --quiet
probe /
probe /api/auth/health
[[ $mode != --check ]] || exit 0

containers=(agora-agent agora-spring agora-cpp agora-broker)
images=(agora-agent:local agora-spring:local agora-cpp:local project-agora-broker:local)
previous=()
for container in "${containers[@]}"; do
  previous+=("$(docker inspect --format '{{.Image}}' "$container")")
done
previous_frontend=$(docker exec agora-nginx readlink /usr/share/nginx/html/current)
[[ $previous_frontend =~ ^releases/release-[a-zA-Z0-9]+$ ]]

switching=0
published=0
rollback() {
  local status=$?
  trap - EXIT
  if ((status != 0 && switching)); then
    echo 'Release failed. Restoring the prior application images.' >&2
    set +e
    if ((published)); then
      docker run --rm -v agora-frontend-dist:/output --entrypoint node project-agora-frontend:local \
        -e 'const fs=require("node:fs");const link="/output/.rollback-"+process.pid;fs.symlinkSync(process.argv[1],link);fs.renameSync(link,"/output/current");' "$previous_frontend"
    fi
    for i in "${!images[@]}"; do docker tag "${previous[$i]}" "${images[$i]}"; done
    backend up -d --no-build --no-deps --wait --wait-timeout 180 agent spring cpp
    broker up -d --no-build --no-deps --wait --wait-timeout 180 broker
    probe /api/auth/health
  fi
  exit "$status"
}
trap rollback EXIT

frontend build frontend-build
backend build agent spring cpp
broker build broker

# The migration only adds the new terms row. Keep the old terms for rollback and audit.
docker exec -i agora-mssql sh -c '
  export SQLCMDPASSWORD="$MSSQL_SA_PASSWORD"
  exec /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -d "$MSSQL_DB" -C -b -I -f 65001
' < "$migration"

switching=1
backend up -d --no-build --no-deps --wait --wait-timeout 180 agent spring cpp
broker up -d --no-build --no-deps --wait --wait-timeout 180 broker
probe /api/auth/health
published=1
frontend run --rm --no-deps frontend-build
probe /
probe /api/auth/health
echo 'Agora agent release is serving through the existing gateway.'

#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  keylime-openstack-compose-deploy.sh [deploy|build|migrate|bootstrap|up|health|down]

Default action:
  deploy

Environment:
  KEYLIME_OPENSTACK_ENV_FILE   /etc/keylime-openstack/keylime-openstack.env
  KEYLIME_OPENSTACK_API_URL    http://127.0.0.1:8088
  KEYLIME_OPENSTACK_HEALTH_RETRIES 30
  KEYLIME_OPENSTACK_HEALTH_INTERVAL_SECONDS 2

The deploy action prepares local directories, creates an env file when missing,
builds the image, starts PostgreSQL, runs Alembic migrations, bootstraps the
default inventory, starts API/worker, and runs health checks.
EOF
}

ACTION="${1:-deploy}"
if [ "$ACTION" = "-h" ] || [ "$ACTION" = "--help" ]; then
  usage
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_FILE="${KEYLIME_OPENSTACK_ENV_FILE:-/etc/keylime-openstack/keylime-openstack.env}"
ENV_TEMPLATE="$REPO_ROOT/deploy/env/keylime-openstack.env.example"
COMPOSE_FILE="$REPO_ROOT/deploy/compose/keylime-openstack-control-plane.yml"
API_URL="${KEYLIME_OPENSTACK_API_URL:-http://127.0.0.1:8088}"
HEALTH_RETRIES="${KEYLIME_OPENSTACK_HEALTH_RETRIES:-30}"
HEALTH_INTERVAL_SECONDS="${KEYLIME_OPENSTACK_HEALTH_INTERVAL_SECONDS:-2}"

compose() {
  KEYLIME_OPENSTACK_ENV_FILE="$ENV_FILE" \
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

random_hex() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 24
  elif command -v python3 >/dev/null 2>&1; then
    python3 - <<'PY'
import secrets
print(secrets.token_hex(24))
PY
  else
    date +%s%N
  fi
}

set_env_value() {
  local key="$1"
  local value="$2"
  local escaped
  escaped="$(printf '%s' "$value" | sed 's/[\/&]/\\&/g')"
  if grep -qE "^${key}=" "$ENV_FILE"; then
    sed -i "s/^${key}=.*/${key}=${escaped}/" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

get_env_value() {
  local key="$1"
  if [ ! -r "$ENV_FILE" ]; then
    return 0
  fi
  grep -E "^${key}=" "$ENV_FILE" | tail -1 | cut -d= -f2- || true
}

find_docker_compose_plugin() {
  local candidate
  for candidate in \
    /usr/libexec/docker/cli-plugins/docker-compose \
    /usr/lib/docker/cli-plugins/docker-compose \
    /usr/local/libexec/docker/cli-plugins/docker-compose \
    /usr/local/lib/docker/cli-plugins/docker-compose \
    "${HOME:-/root}/.docker/cli-plugins/docker-compose"; do
    if [ -x "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

is_unset_or_placeholder() {
  local value="${1:-}"
  [ -z "$value" ] || [ "$value" = "change-me" ] || [ "$value" = "keylime_openstack" ]
}

prepare() {
  if [ ! -r "$ENV_TEMPLATE" ]; then
    echo "missing env template: $ENV_TEMPLATE" >&2
    exit 1
  fi
  if [ ! -r "$COMPOSE_FILE" ]; then
    echo "missing compose file: $COMPOSE_FILE" >&2
    exit 1
  fi

  install -d -m 0755 "$(dirname "$ENV_FILE")"
  install -d -m 0755 /etc/keylime-openstack
  install -d -m 0700 /etc/keylime-openstack/ansible
  install -d -m 0755 /var/lib/keylime-openstack/postgres
  install -d -m 0755 /var/lib/keylime-openstack-sync/policies/runtime
  install -d -m 0755 /tmp/keylime-openstack

  if [ ! -r "$ENV_FILE" ]; then
    install -m 0600 "$ENV_TEMPLATE" "$ENV_FILE"
    pg_password="$(random_hex)"
    admin_token="$(random_hex)"
    set_env_value POSTGRES_PASSWORD "$pg_password"
    set_env_value DATABASE_URL "postgresql+psycopg://keylime_openstack:${pg_password}@postgres:5432/keylime_openstack"
    set_env_value ADMIN_TOKEN "$admin_token"
    echo "created_env_file=$ENV_FILE"
  fi

  current_pg_password="$(get_env_value POSTGRES_PASSWORD)"
  if is_unset_or_placeholder "$current_pg_password"; then
    pg_password="$(random_hex)"
    set_env_value POSTGRES_PASSWORD "$pg_password"
    set_env_value DATABASE_URL "postgresql+psycopg://keylime_openstack:${pg_password}@postgres:5432/keylime_openstack"
    echo "generated_postgres_password=true"
  fi

  current_admin_token="$(get_env_value ADMIN_TOKEN)"
  if is_unset_or_placeholder "$current_admin_token"; then
    admin_token="$(random_hex)"
    set_env_value ADMIN_TOKEN "$admin_token"
    echo "generated_admin_token=true"
  fi

  current_docker_network="$(get_env_value KEYLIME_OPENSTACK_DOCKER_NETWORK)"
  if [ -z "$current_docker_network" ]; then
    set_env_value KEYLIME_OPENSTACK_DOCKER_NETWORK "keylime_openstack_net"
  fi

  current_docker_subnet="$(get_env_value KEYLIME_OPENSTACK_DOCKER_SUBNET)"
  if [ -z "$current_docker_subnet" ]; then
    set_env_value KEYLIME_OPENSTACK_DOCKER_SUBNET "10.245.0.0/24"
  fi

  current_host_docker_bin="$(get_env_value HOST_DOCKER_BIN)"
  if [ -z "$current_host_docker_bin" ]; then
    host_docker_bin="$(command -v docker || true)"
    if [ -n "$host_docker_bin" ]; then
      set_env_value HOST_DOCKER_BIN "$host_docker_bin"
    else
      echo "WARN: docker CLI not found on host; Keylime tenant-tool operations will be unavailable." >&2
    fi
  fi

  current_host_compose_plugin="$(get_env_value HOST_DOCKER_COMPOSE_PLUGIN)"
  if [ -z "$current_host_compose_plugin" ]; then
    if host_compose_plugin="$(find_docker_compose_plugin)"; then
      set_env_value HOST_DOCKER_COMPOSE_PLUGIN "$host_compose_plugin"
    else
      echo "WARN: docker compose plugin not found on host; Keylime tenant-tool operations will be unavailable." >&2
    fi
  fi

  current_trust_agent_map="$(get_env_value TRUST_AGENT_TYPE_MAP)"
  if [ -z "$current_trust_agent_map" ]; then
    set_env_value TRUST_AGENT_TYPE_MAP "csri8=keylime,csri9=keylime,hygon22=unmanaged,hygon23=opentcsm_tpcm"
  fi

  current_opentcsm_fresh="$(get_env_value OPENTCSM_EVIDENCE_FRESH_SECONDS)"
  if [ -z "$current_opentcsm_fresh" ]; then
    set_env_value OPENTCSM_EVIDENCE_FRESH_SECONDS "300"
  fi

  current_opentcsm_active_collect="$(get_env_value OPENTCSM_ACTIVE_COLLECT_ENABLED)"
  if [ -z "$current_opentcsm_active_collect" ]; then
    set_env_value OPENTCSM_ACTIVE_COLLECT_ENABLED "true"
  fi

  current_opentcsm_collect_interval="$(get_env_value OPENTCSM_COLLECT_INTERVAL_SECONDS)"
  if [ -z "$current_opentcsm_collect_interval" ]; then
    set_env_value OPENTCSM_COLLECT_INTERVAL_SECONDS "60"
  fi

  current_ansible_user="$(get_env_value ANSIBLE_REMOTE_USER)"
  if [ -z "$current_ansible_user" ]; then
    set_env_value ANSIBLE_REMOTE_USER "root"
  fi

  current_ansible_python="$(get_env_value ANSIBLE_PYTHON_INTERPRETER)"
  if [ -z "$current_ansible_python" ]; then
    set_env_value ANSIBLE_PYTHON_INTERPRETER "auto_silent"
  fi

  current_ansible_key="$(get_env_value ANSIBLE_SSH_PRIVATE_KEY_FILE)"
  if [ -z "$current_ansible_key" ]; then
    set_env_value ANSIBLE_SSH_PRIVATE_KEY_FILE "/etc/keylime-openstack/ansible/id_ed25519"
  fi

  current_ansible_known_hosts="$(get_env_value ANSIBLE_KNOWN_HOSTS_FILE)"
  if [ -z "$current_ansible_known_hosts" ]; then
    set_env_value ANSIBLE_KNOWN_HOSTS_FILE "/etc/keylime-openstack/ansible/known_hosts"
  fi

  chmod 0600 "$ENV_FILE"
}

build_image() {
  compose build
}

start_postgres() {
  compose up -d postgres
}

migrate() {
  start_postgres
  compose run --rm api alembic upgrade head
}

bootstrap() {
  compose run --rm api keylime-openstackctl bootstrap
}

up_services() {
  compose up -d api worker
}

health() {
  health_endpoint "/api/health" "API health"
  echo
  health_endpoint "/api/overview" "API overview" "admin-token"
  echo
}

health_endpoint() {
  local path="$1"
  local label="$2"
  local auth_mode="${3:-public}"
  local attempt
  local output
  local admin_token
  local curl_args=(-fsS)

  if [ "$auth_mode" = "admin-token" ]; then
    admin_token="$(get_env_value ADMIN_TOKEN)"
    if [ -z "$admin_token" ]; then
      echo "ERROR: $label requires ADMIN_TOKEN for authenticated health check." >&2
      return 1
    fi
    curl_args+=(-H "X-Admin-Token: $admin_token")
  fi

  for attempt in $(seq 1 "$HEALTH_RETRIES"); do
    if output="$(curl "${curl_args[@]}" "$API_URL$path" 2>&1)"; then
      printf '%s\n' "$output"
      return 0
    fi
    if [ "$attempt" -eq "$HEALTH_RETRIES" ]; then
      echo "ERROR: $label failed after $attempt attempts: $output" >&2
      compose ps api worker >&2 || true
      compose logs --tail=80 api worker >&2 || true
      return 1
    fi
    sleep "$HEALTH_INTERVAL_SECONDS"
  done
}

case "$ACTION" in
  deploy)
    prepare
    build_image
    migrate
    bootstrap
    up_services
    health
    ;;
  build)
    prepare
    build_image
    ;;
  migrate)
    prepare
    migrate
    ;;
  bootstrap)
    prepare
    bootstrap
    ;;
  up)
    prepare
    up_services
    ;;
  health)
    health
    ;;
  down)
    compose down
    ;;
  *)
    usage
    exit 1
    ;;
esac

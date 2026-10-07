#!/bin/sh
# Foreground stdio launcher for the dedicated one-off Compose app-server.
# On normal exit or a supervisor signal, stop the exact named container. That
# sends SIGTERM to its foreground app-server and Compose's --rm removes it.
set -eu

compose_file=${1:?usage: launch-app-server.sh /absolute/path/compose.yaml}
container_name=${2:?supply a stable unique container name for this connector instance}
case "$container_name" in
  *[!a-zA-Z0-9_.-]*|'') echo "launcher: invalid container name" >&2; exit 2 ;;
esac
case "$compose_file" in
  /*) ;;
  *) echo "launcher: compose file path must be absolute" >&2; exit 2 ;;
esac
if [ ! -f "$compose_file" ]; then
  echo "launcher: compose file does not exist: $compose_file" >&2
  exit 2
fi

project_dir=$(dirname -- "$compose_file")
created=0
attach_pid=

compose() {
  docker compose --project-directory "$project_dir" -f "$compose_file" "$@"
}

cleanup() {
  code=$?
  trap - EXIT HUP INT TERM
  if [ "$created" -eq 1 ]; then
    # Stop the remote container, not just the local Docker attach client.
    # This terminates codex app-server even if the stdio pipe was interrupted.
    docker stop --time 2 "$container_name" >/dev/null 2>&1 || true
  fi
  if [ -n "$attach_pid" ]; then
    wait "$attach_pid" 2>/dev/null || true
  fi
  exit "$code"
}

trap cleanup EXIT
trap 'exit 0' HUP INT TERM

# A stable deployment name detects a remote orphan even after the local
# launcher was killed. Never create another controller over a surviving agent.
if docker container inspect "$container_name" >/dev/null 2>&1; then
  echo "launcher: existing instance container requires operator inspection: $container_name" >&2
  exit 2
fi

compose run --detach --interactive --no-TTY --no-deps --rm \
  --name "$container_name" agent codex app-server --stdio >/dev/null
created=1

# stdout/stderr are inherited. In particular, app-server stdout is the only
# source of protocol frames consumed by the supervisor.
# Save stdin on a separate descriptor before creating the background command:
# some POSIX shells replace its fd 0 before applying redirections.
exec 3<&0
docker attach --sig-proxy=false "$container_name" <&3 &
exec 3<&-
attach_pid=$!
wait "$attach_pid" || exit $?

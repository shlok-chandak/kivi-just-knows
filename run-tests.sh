#!/usr/bin/env bash
# Run the test suite.
#
# The worker is stopped first: it competes with the tests for jobs in the
# queue, which makes queue and pipeline assertions fail intermittently.
# It is restarted afterwards if it was running.
set -euo pipefail

was_running=$(docker compose ps --services --filter status=running | grep -c '^worker$' || true)

if [ "$was_running" != "0" ]; then
  echo "stopping worker for the duration of the tests"
  docker compose stop worker >/dev/null
fi

restore() {
  if [ "$was_running" != "0" ]; then
    echo "restarting worker"
    docker compose start worker >/dev/null
  fi
}
trap restore EXIT

docker compose exec -T backend pytest "${@:-tests/}"

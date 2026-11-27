#!/bin/sh

set -e

if [ -n "$DB_HOST" ] && [ -n "$DB_PORT" ]; then
  remaining=60
  until nc -z -w 1 "$DB_HOST" "$DB_PORT"; do
    remaining=$((remaining - 1))
    if [ "$remaining" -le 0 ]; then
      echo "PostgreSQL TCP probe did not become ready. Check database service and networking." >&2
      exit 1
    fi
    sleep 1
  done
fi

exec "$@"

#!/bin/sh
set -eu

# Namespace names are unique. After every registration attempt (including a
# lost response or AlreadyExists), confirm readability before releasing workers.
max_attempts=${TEMPORAL_NAMESPACE_INIT_ATTEMPTS:-12}
retry_delay=${TEMPORAL_NAMESPACE_INIT_DELAY_SECONDS:-2}
case "$max_attempts" in
  ""|*[!0-9]*|0) echo "Invalid namespace initialization attempt limit" >&2; exit 2 ;;
esac
case "$retry_delay" in
  ""|*[!0-9]*) echo "Invalid namespace initialization retry delay" >&2; exit 2 ;;
esac

describe_namespace() {
  temporal --command-timeout 10s --client-connect-timeout 3s operator namespace describe --namespace default
}

attempt=1
while [ "$attempt" -le "$max_attempts" ]; do
  if describe_namespace >/dev/null; then
    echo "Namespace default is available."
    exit 0
  fi

  echo "Namespace check failed (attempt $attempt/$max_attempts); ensuring registration." >&2
  if temporal --command-timeout 10s --client-connect-timeout 3s operator namespace create --namespace default --retention 72h; then
    echo "Namespace registration accepted; confirming availability."
  else
    echo "Registration did not confirm availability; reconciling namespace state." >&2
  fi

  if describe_namespace >/dev/null; then
    echo "Namespace default is available."
    exit 0
  fi

  if [ "$attempt" -lt "$max_attempts" ]; then
    sleep "$retry_delay"
  fi
  attempt=$((attempt + 1))
done

echo "Namespace default could not be verified after $max_attempts attempts." >&2
exit 1

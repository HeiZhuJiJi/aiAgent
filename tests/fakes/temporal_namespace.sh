# Sourced only by bootstrap regression tests; no real Temporal calls are made.
fake_describe_count=0
fake_create_count=0

temporal() {
  case "$*" in
    *"operator namespace describe"*)
      fake_describe_count=$((fake_describe_count + 1))
      echo "TEST_CALL describe" >&2
      case "$TEST_NAMESPACE_CASE" in
        existing) return 0 ;;
        created|already_exists|response_loss)
          if [ "$fake_describe_count" -ge 2 ]; then return 0; fi ;;
        recovering)
          if [ "$fake_describe_count" -ge 3 ]; then return 0; fi ;;
      esac
      echo "Namespace query unavailable or not found" >&2
      return 1
      ;;
    *"operator namespace create"*)
      fake_create_count=$((fake_create_count + 1))
      echo "TEST_CALL create" >&2
      case "$TEST_NAMESPACE_CASE" in
        created|unconfirmed_create) return 0 ;;
        already_exists) echo "Namespace default already exists" >&2 ;;
        response_loss) echo "Registration response deadline exceeded" >&2 ;;
        *) echo "Registration unavailable" >&2 ;;
      esac
      return 1
      ;;
    *) echo "Unexpected Temporal test command" >&2; return 99 ;;
  esac
}

#!/usr/bin/env bash

loopback_http_url() {
  local address="$1"
  local path="${2:-}"
  local host="${address%:*}"

  case "$host" in
    127.0.0.1|localhost|\[::1\])
      printf 'https://%s%s' "$address" "$path"
      ;;
    *)
      echo "refusing clear-text HTTP for non-loopback address: $address" >&2
      return 1
      ;;
  esac
}

#!/usr/bin/env bash
set -euo pipefail

require_env() {
  if [ -z "${!1:-}" ]; then
    echo "$1 is required" >&2
    exit 1
  fi
}

write_output() {
  if [ -n "${GITHUB_OUTPUT:-}" ]; then
    printf '%s=%s\n' "$1" "$2" >> "$GITHUB_OUTPUT"
  fi
}

yaml_quote() {
  printf "%s" "$1" | sed "s/'/''/g; s/^/'/; s/$/'/"
}

require_env SSH_DEPLOY_PUBLIC_KEY
require_env SSH_HOST_PRIVATE_KEY
require_env SSH_HOST_PUBLIC_KEY

if [[ ! "$SSH_HOST_PUBLIC_KEY" =~ ^ssh-ed25519[[:space:]] ]]; then
  echo "SSH_HOST_PUBLIC_KEY must be an ed25519 public key (ssh-ed25519 ...)" >&2
  exit 1
fi

if ! command -v doctl >/dev/null 2>&1; then
  echo "doctl is required. Install it with digitalocean/action-doctl in GitHub Actions." >&2
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required" >&2
  exit 1
fi

DO_REGION="${DO_REGION:-nyc3}"
DO_SIZE="${DO_SIZE:-s-1vcpu-1gb}"
DO_IMAGE="${DO_IMAGE:-ubuntu-24-04-x64}"
DO_DROPLET_NAME="${DO_DROPLET_NAME:-dlsite-opds}"
DO_SSH_KEY_NAME="${DO_SSH_KEY_NAME:-${DO_DROPLET_NAME}-deploy}"
DO_FIREWALL_NAME="${DO_FIREWALL_NAME:-${DO_DROPLET_NAME}-ssh}"
DO_TAG="${DO_TAG:-${DO_DROPLET_NAME}}"
DO_ENABLE_BACKUPS="${DO_ENABLE_BACKUPS:-false}"
DO_PROJECT_NAME="${DO_PROJECT_NAME:-DLsite OPDS}"
DO_PROJECT_PURPOSE="${DO_PROJECT_PURPOSE:-Service or API}"
DO_PROJECT_ENVIRONMENT="${DO_PROJECT_ENVIRONMENT:-Production}"
DO_PROJECT_DESCRIPTION="${DO_PROJECT_DESCRIPTION:-}"
DO_SWAP_SIZE="${DO_SWAP_SIZE:-1G}"

if [[ ! "$DO_SWAP_SIZE" =~ ^(0|[1-9][0-9]*[KMG]?)$ ]]; then
  echo "DO_SWAP_SIZE must match ^(0|[1-9][0-9]*[KMG]?)$ (e.g. 0 to disable, 512M, 1G)" >&2
  exit 1
fi

public_key_file="$(mktemp)"
cloud_init_file="$(mktemp)"

cleanup() {
  rm -f "$public_key_file" "$cloud_init_file"
}
trap cleanup EXIT

printf '%s\n' "$SSH_DEPLOY_PUBLIC_KEY" > "$public_key_file"

ssh_key_fingerprint="$(ssh-keygen -E md5 -lf "$public_key_file" | awk '{print $2}' | sed 's/^MD5://')"
ssh_public_key_yaml="$(yaml_quote "$SSH_DEPLOY_PUBLIC_KEY")"
ssh_host_private_key_b64="$(printf '%s' "$SSH_HOST_PRIVATE_KEY" | base64 -w0)"

SSH_PUBLIC_KEY_YAML="$ssh_public_key_yaml" \
SSH_HOST_PRIVATE_KEY_B64="$ssh_host_private_key_b64" \
SSH_HOST_PUBLIC_KEY="$SSH_HOST_PUBLIC_KEY" \
DO_SWAP_SIZE="$DO_SWAP_SIZE" \
perl -0pe '
  s/\{\{SSH_PUBLIC_KEY_YAML\}\}/$ENV{SSH_PUBLIC_KEY_YAML}/g;
  s/\{\{SSH_HOST_PRIVATE_KEY_B64\}\}/$ENV{SSH_HOST_PRIVATE_KEY_B64}/g;
  s/\{\{SSH_HOST_PUBLIC_KEY\}\}/$ENV{SSH_HOST_PUBLIC_KEY}/g;
  s/\{\{DO_SWAP_SIZE\}\}/$ENV{DO_SWAP_SIZE}/g;
' infra/digitalocean/cloud-init.yml > "$cloud_init_file"

echo "Provisioning DigitalOcean resources for ${DO_DROPLET_NAME}"

echo "Looking up tag ${DO_TAG}"
if doctl compute tag get "$DO_TAG" >/dev/null 2>&1; then
  echo "Reusing tag ${DO_TAG}"
else
  echo "Creating tag ${DO_TAG}"
  doctl compute tag create "$DO_TAG"
fi

echo "Listing SSH keys"
ssh_keys_json="$(doctl compute ssh-key list --output json)"
ssh_key_id="$(
  jq -r --arg fp "$ssh_key_fingerprint" '
    map(select(.fingerprint == $fp)) |
    if length > 1 then error("multiple SSH keys match fingerprint")
    elif length == 1 then .[0].id
    else "" end
  ' <<< "$ssh_keys_json"
)"

if [ -n "$ssh_key_id" ]; then
  echo "Reusing SSH key with fingerprint ${ssh_key_fingerprint}"
else
  existing_key_by_name="$(
    jq -r --arg name "$DO_SSH_KEY_NAME" '
      map(select(.name == $name)) |
      if length > 1 then error("multiple SSH keys match name")
      elif length == 1 then .[0].fingerprint
      else "" end
    ' <<< "$ssh_keys_json"
  )"

  if [ -n "$existing_key_by_name" ]; then
    echo "DigitalOcean SSH key ${DO_SSH_KEY_NAME} exists with a different fingerprint" >&2
    exit 1
  fi

  echo "Creating SSH key ${DO_SSH_KEY_NAME}"
  ssh_key_id="$(
    doctl compute ssh-key import "$DO_SSH_KEY_NAME" \
      --public-key-file "$public_key_file" \
      --format ID \
      --no-header |
      awk 'NR == 1 { print $1 }'
  )"
fi

echo "Listing Droplets"
droplets_json="$(doctl compute droplet list --output json)"
droplet_count="$(
  jq -r --arg name "$DO_DROPLET_NAME" '[.[] | select(.name == $name)] | length' <<< "$droplets_json"
)"

if [ "$droplet_count" -gt 1 ]; then
  echo "Found multiple DigitalOcean Droplets named ${DO_DROPLET_NAME}" >&2
  exit 1
fi

if [ "$droplet_count" -eq 1 ]; then
  droplet_id="$(jq -r --arg name "$DO_DROPLET_NAME" '.[] | select(.name == $name) | .id' <<< "$droplets_json")"
  echo "Reusing Droplet ${DO_DROPLET_NAME}"
else
  echo "Creating Droplet ${DO_DROPLET_NAME}"
  create_args=(
    compute droplet create "$DO_DROPLET_NAME"
    --region "$DO_REGION"
    --size "$DO_SIZE"
    --image "$DO_IMAGE"
    --ssh-keys "$ssh_key_id"
    --tag-names "$DO_TAG"
    --user-data-file "$cloud_init_file"
    --enable-ipv6
    --enable-monitoring
    --wait
    --format ID
    --no-header
  )

  if [[ "$DO_ENABLE_BACKUPS" =~ ^(1|true|TRUE|yes|YES|on|ON)$ ]]; then
    create_args+=(--enable-backups)
  fi

  create_stdout="$(mktemp)"
  create_stderr="$(mktemp)"
  set +e
  doctl "${create_args[@]}" >"$create_stdout" 2>"$create_stderr"
  create_rc=$?
  set -e
  echo "doctl droplet create exit=${create_rc}"
  echo "--- doctl stdout ---"
  cat "$create_stdout"
  echo "--- doctl stderr ---"
  cat "$create_stderr"
  echo "--------------------"
  droplet_id="$(awk 'NR == 1 { print $1 }' "$create_stdout")"
  rm -f "$create_stdout" "$create_stderr"
  if [ "$create_rc" -ne 0 ] || [ -z "$droplet_id" ]; then
    echo "Falling back to looking up Droplet ${DO_DROPLET_NAME} by name after create call" >&2
    droplets_json="$(doctl compute droplet list --output json)"
    droplet_id="$(jq -r --arg name "$DO_DROPLET_NAME" '[.[] | select(.name == $name)] | (.[0].id // empty)' <<< "$droplets_json")"
    if [ -z "$droplet_id" ]; then
      echo "Droplet ${DO_DROPLET_NAME} was not created" >&2
      exit 1
    fi
    echo "Recovered droplet_id=${droplet_id} from list lookup"
  fi
fi

if [ -n "$DO_PROJECT_NAME" ]; then
  echo "Looking up project ${DO_PROJECT_NAME}"
  projects_stdout="$(mktemp)"
  projects_stderr="$(mktemp)"
  set +e
  doctl projects list --output json >"$projects_stdout" 2>"$projects_stderr"
  projects_rc=$?
  set -e
  echo "doctl projects list exit=${projects_rc}"
  if [ "$projects_rc" -ne 0 ]; then
    echo "--- doctl projects list stderr ---"
    cat "$projects_stderr"
    echo "----------------------------------"
    rm -f "$projects_stdout" "$projects_stderr"
    exit "$projects_rc"
  fi
  projects_json="$(cat "$projects_stdout")"
  rm -f "$projects_stdout" "$projects_stderr"
  project_id="$(
    jq -r --arg name "$DO_PROJECT_NAME" '
      [.[] | select(.name == $name)] |
      if length > 1 then error("multiple Projects match name")
      elif length == 1 then .[0].id
      else "" end
    ' <<< "$projects_json"
  )"

  if [ -n "$project_id" ]; then
    echo "Reusing project ${DO_PROJECT_NAME}"
  else
    echo "Creating project ${DO_PROJECT_NAME}"
    create_project_args=(
      projects create
      --name "$DO_PROJECT_NAME"
      --purpose "$DO_PROJECT_PURPOSE"
      --environment "$DO_PROJECT_ENVIRONMENT"
    )
    if [ -n "$DO_PROJECT_DESCRIPTION" ]; then
      create_project_args+=(--description "$DO_PROJECT_DESCRIPTION")
    fi
    create_project_args+=(--format ID --no-header)
    project_id="$(doctl "${create_project_args[@]}" | awk 'NR == 1 { print $1 }')"
  fi

  echo "Assigning Droplet ${DO_DROPLET_NAME} to project ${DO_PROJECT_NAME}"
  doctl projects resources assign "$project_id" --resource="do:droplet:${droplet_id}" >/dev/null
fi

echo "Listing Firewalls"
firewalls_json="$(doctl compute firewall list --output json)"
firewall_count="$(
  jq -r --arg name "$DO_FIREWALL_NAME" '[.[] | select(.name == $name)] | length' <<< "$firewalls_json"
)"

if [ "$firewall_count" -gt 1 ]; then
  echo "Found multiple DigitalOcean Firewalls named ${DO_FIREWALL_NAME}" >&2
  exit 1
fi

inbound_rules="protocol:tcp,ports:22,address:0.0.0.0/0,address:::/0 protocol:tcp,ports:80,address:0.0.0.0/0,address:::/0 protocol:tcp,ports:443,address:0.0.0.0/0,address:::/0"
outbound_rules="protocol:icmp,address:0.0.0.0/0,address:::/0 protocol:tcp,ports:all,address:0.0.0.0/0,address:::/0 protocol:udp,ports:all,address:0.0.0.0/0,address:::/0"

if [ "$firewall_count" -eq 1 ]; then
  firewall_id="$(jq -r --arg name "$DO_FIREWALL_NAME" '.[] | select(.name == $name) | .id' <<< "$firewalls_json")"
  echo "Updating Firewall ${DO_FIREWALL_NAME}"
  doctl compute firewall update "$firewall_id" \
    --name "$DO_FIREWALL_NAME" \
    --inbound-rules "$inbound_rules" \
    --outbound-rules "$outbound_rules" \
    --droplet-ids "$droplet_id"
else
  echo "Creating Firewall ${DO_FIREWALL_NAME}"
  doctl compute firewall create \
    --name "$DO_FIREWALL_NAME" \
    --inbound-rules "$inbound_rules" \
    --outbound-rules "$outbound_rules" \
    --droplet-ids "$droplet_id"
fi

for attempt in {1..60}; do
  droplet_json="$(doctl compute droplet get "$droplet_id" --output json)"
  droplet_status="$(jq -r '.[0].status' <<< "$droplet_json")"
  opds_host="$(jq -r '[.[0].networks.v4[]? | select(.type == "public") | .ip_address][0] // ""' <<< "$droplet_json")"

  if [ "$droplet_status" = "active" ] && [ -n "$opds_host" ]; then
    write_output opds_host "$opds_host"
    write_output droplet_id "$droplet_id"
    echo "Droplet ready: ${DO_DROPLET_NAME} (${opds_host})"
    exit 0
  fi

  echo "Waiting for Droplet ${DO_DROPLET_NAME} to become active..."
  sleep 10
done

echo "Timed out waiting for Droplet ${DO_DROPLET_NAME}" >&2
exit 1

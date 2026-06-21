#!/usr/bin/env bash
# Ensure the DigitalOcean firewall allows OPDS on 2580/2581 (idempotent).
set -euo pipefail

if ! command -v doctl >/dev/null 2>&1; then
  echo "doctl is required" >&2
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required" >&2
  exit 1
fi

DO_DROPLET_NAME="${DO_DROPLET_NAME:-dlsite-opds}"
DO_FIREWALL_NAME="${DO_FIREWALL_NAME:-${DO_DROPLET_NAME}-ssh}"

droplet_id="$(doctl compute droplet list \
  --format Name,ID \
  --no-header \
  | awk -v name="${DO_DROPLET_NAME}" '$1 == name { print $2; exit }')"

if [ -z "${droplet_id}" ]; then
  echo "No droplet named ${DO_DROPLET_NAME}; skipping firewall update." >&2
  exit 0
fi

inbound_rules="protocol:tcp,ports:22,address:0.0.0.0/0,address:::/0 protocol:tcp,ports:80,address:0.0.0.0/0,address:::/0 protocol:tcp,ports:443,address:0.0.0.0/0,address:::/0 protocol:tcp,ports:2580,address:0.0.0.0/0,address:::/0 protocol:tcp,ports:2581,address:0.0.0.0/0,address:::/0"
outbound_rules="protocol:icmp,address:0.0.0.0/0,address:::/0 protocol:tcp,ports:all,address:0.0.0.0/0,address:::/0 protocol:udp,ports:all,address:0.0.0.0/0,address:::/0"

firewalls_json="$(doctl compute firewall list --output json)"
firewall_count="$(
  jq -r --arg name "$DO_FIREWALL_NAME" '[.[] | select(.name == $name)] | length' <<< "$firewalls_json"
)"

if [ "$firewall_count" -gt 1 ]; then
  echo "Found multiple DigitalOcean Firewalls named ${DO_FIREWALL_NAME}" >&2
  exit 1
fi

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

echo "Firewall ${DO_FIREWALL_NAME} allows ports 2580 and 2581."

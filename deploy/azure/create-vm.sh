#!/bin/bash
# Create the Clashforces VM from scratch. Run in Azure Cloud Shell (Bash):
#
#   curl -fsSL https://raw.githubusercontent.com/ChunChun451/Clashforces/main/deploy/azure/create-vm.sh \
#     | DOMAIN=clashforces.duckdns.org bash
#
# DOMAIN         the site's name (e.g. a DuckDNS name). Point its DNS at the IP printed at the end.
#                Unset: the site is served on the free Azure name <label>.<region>.cloudapp.azure.com.
# REDIRECT_FROM  extra names that 301-redirect to DOMAIN. The Azure cloudapp name is always added.
# LABEL          the Azure DNS label (default clashforces-<random>). LOCATION (default indiasouthcentral),
#                SIZE (default Standard_B1s). REF: git branch for the deploy scripts (default main;
#                the app itself always deploys main).
# Refuses to touch an existing VM: to update one, use apply.sh (see README).
set -euo pipefail
LOCATION="${LOCATION:-indiasouthcentral}"
SIZE="${SIZE:-Standard_B1s}"
REF="${REF:-main}"
RG="${RG:-clashforces}"
VM=clashforces
LABEL="${LABEL:-clashforces-$(head -c 4 /dev/urandom | od -An -tx1 | tr -d ' \n')}"
FQDN="$LABEL.$LOCATION.cloudapp.azure.com"
DOMAIN="${DOMAIN:-}"
REDIRECT_FROM="${REDIRECT_FROM:-}"
if [ -z "$DOMAIN" ]; then
    DOMAIN="$FQDN"
else
    REDIRECT_FROM="$FQDN ${REDIRECT_FROM//,/ }"
fi
REDIRECT_FROM="$(echo "$REDIRECT_FROM" | xargs)"   # trim

if az vm show -g "$RG" -n "$VM" -o none 2> /dev/null; then
    echo "VM $RG/$VM already exists. To update it, run apply.sh on it (see README); to rebuild, delete it first."
    exit 1
fi

env_b64="$(printf "DOMAIN=%s\nREDIRECT_FROM='%s'\nSCRIPTS_REF=%s\n" "$DOMAIN" "$REDIRECT_FROM" "$REF" | base64 -w0)"
curl -fsSL "https://raw.githubusercontent.com/ChunChun451/Clashforces/$REF/deploy/azure/cloud-init.yaml" \
    | sed -e "s|__DEPLOY_ENV_B64__|$env_b64|" -e "s|__REF__|$REF|" > /tmp/clashforces-init.yaml

az group create -n "$RG" -l "$LOCATION" -o none
az vm create -g "$RG" -n "$VM" -l "$LOCATION" \
    --image Ubuntu2404 --size "$SIZE" \
    --admin-username clash --generate-ssh-keys \
    --public-ip-sku Standard --public-ip-address-dns-name "$LABEL" \
    --os-disk-size-gb 64 --storage-sku Premium_LRS \
    --custom-data /tmp/clashforces-init.yaml -o table
az vm open-port -g "$RG" -n "$VM" --port 80,443 --priority 900 -o none
IP="$(az vm show -d -g "$RG" -n "$VM" --query publicIps -o tsv)"

echo
echo "Done. Public IP: $IP   Azure name: $FQDN"
if [ "$DOMAIN" != "$FQDN" ]; then
    echo "Point $DOMAIN at $IP (on duckdns.org: set the IP of the subdomain), then it goes live within"
    echo "~5 minutes (Caddy gets the certificate once DNS answers). $FQDN redirects to it."
fi
echo "Site: https://$DOMAIN    First-boot log on the VM: /var/log/clashforces-apply.log"

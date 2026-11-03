#!/bin/bash
# Run in Azure Cloud Shell (Bash):
#   curl -fsSL https://raw.githubusercontent.com/ChunChun451/Clashforces/main/deploy/azure/create-vm.sh | bash
# Optional: REF=<branch> to fetch cloud-init from another branch (the app itself always deploys main).
# Optional: LOCATION=southindia (default centralindia), SIZE=Standard_B2ats_v2 (default Standard_B1s).
set -euo pipefail
LOCATION="${LOCATION:-centralindia}"
SIZE="${SIZE:-Standard_B1s}"
REF="${REF:-main}"
RG=clashforces
LABEL="clashforces-$(head -c 4 /dev/urandom | od -An -tx1 | tr -d ' \n')"
FQDN="$LABEL.$LOCATION.cloudapp.azure.com"

curl -fsSL https://raw.githubusercontent.com/ChunChun451/Clashforces/$REF/deploy/azure/cloud-init.yaml \
  | sed "s/__DOMAIN__/$FQDN/g" > /tmp/clashforces-init.yaml

az group create -n "$RG" -l "$LOCATION" -o none
az vm create -g "$RG" -n clashforces -l "$LOCATION" \
  --image Ubuntu2404 --size "$SIZE" \
  --admin-username clash --generate-ssh-keys \
  --public-ip-sku Standard --public-ip-address-dns-name "$LABEL" \
  --os-disk-size-gb 64 --storage-sku Premium_LRS \
  --custom-data /tmp/clashforces-init.yaml -o table
az vm open-port -g "$RG" -n clashforces --port 80,443 --priority 900 -o none

echo
echo "Done. In ~5 minutes the site will be live at: https://$FQDN"
echo "Redeploy later:  az vm run-command invoke -g $RG -n clashforces --command-id RunShellScript --scripts /opt/clashforces/update.sh"

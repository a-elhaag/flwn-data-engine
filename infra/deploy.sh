#!/usr/bin/env bash
# Deploys the data engine to Azure Container Apps and locks Postgres and Blob storage to the
# app's VNet. Re-runnable (create-or-update). Run after provision.sh. Secrets are read from the
# gitignored .env and passed as Container Apps secrets; nothing is printed.
#
# Cost choices (UAE North, roughly $27/month on top of Postgres):
#   ACR Basic ~$5 | 2 private endpoints ~$15 | 2 private DNS zones ~$1 | VNet free
#   Container Apps: Consumption profile only (no base fee), 0.5 vCPU / 1 GiB, scales to zero
#   (the file queue is in Postgres, so a restart loses nothing). Set --min-replicas 1 for prod.
#   No Log Analytics workspace (`az containerapp logs show` still streams live logs).
set -euo pipefail
cd "$(dirname "$0")/.."

SUB="${SUB:-939aff7a-6509-457f-810a-b90459c40d21}"
RG="${RG:-flwn-data-engine}"
LOC="${LOC:-uaenorth}"
PG="${PG:-flwn-pg-uae-01}"
SA="${SA:-stflwndatauae01}"
ACR="${ACR:-acrflwnuae01}"
VNET="${VNET:-vnet-flwn-uae-01}"
ID="${ID:-id-flwn-data-uae-01}"
CAE="${CAE:-cae-flwn-uae-01}"
APP="${APP:-ca-flwn-data-uae-01}"
TAGS="project=flwn env=dev"
IMAGE="flwn-data-engine:$(git rev-parse --short HEAD)"
az() { command az "$@" --subscription "$SUB"; }
env_val() { grep -E "^$1=" .env | head -1 | cut -d= -f2-; }

az provider register --namespace Microsoft.Network --wait -o none
az provider register --namespace Microsoft.App --wait -o none
az provider register --namespace Microsoft.ContainerRegistry --wait -o none

# ---- Registry, identity, network ------------------------------------------------------------
az acr create -n "$ACR" -g "$RG" -l "$LOC" --sku Basic --admin-enabled false --tags $TAGS -o none
az identity create -n "$ID" -g "$RG" -l "$LOC" --tags $TAGS -o none
ID_ID=$(az identity show -n "$ID" -g "$RG" --query id -o tsv)
ID_PRINCIPAL=$(az identity show -n "$ID" -g "$RG" --query principalId -o tsv)

az network vnet create -n "$VNET" -g "$RG" -l "$LOC" --address-prefixes 10.20.0.0/16 --tags $TAGS -o none
az network vnet subnet create -n snet-apps --vnet-name "$VNET" -g "$RG" \
  --address-prefixes 10.20.0.0/27 --delegations Microsoft.App/environments -o none
az network vnet subnet create -n snet-pe --vnet-name "$VNET" -g "$RG" --address-prefixes 10.20.1.0/28 -o none

ACR_ID=$(az acr show -n "$ACR" -g "$RG" --query id -o tsv)
SA_ID=$(az storage account show -n "$SA" -g "$RG" --query id -o tsv)
PG_ID=$(az postgres flexible-server show -n "$PG" -g "$RG" --query id -o tsv)
for spec in "AcrPull:$ACR_ID" "Storage Blob Data Contributor:$SA_ID"; do
  az role assignment create --assignee-object-id "$ID_PRINCIPAL" --assignee-principal-type ServicePrincipal \
    --role "${spec%%:*}" --scope "${spec#*:}" -o none || true   # already assigned on re-run
done

# ---- Private endpoints: Postgres and Blob, each with its private DNS zone -------------------
private_endpoint() { # name resource-id group-id dns-zone
  az network private-endpoint show -n "$1" -g "$RG" -o none 2>/dev/null && return   # re-run: already done
  az network private-endpoint create -n "$1" -g "$RG" -l "$LOC" --vnet-name "$VNET" --subnet snet-pe \
    --private-connection-resource-id "$2" --group-id "$3" --connection-name "$1" --tags $TAGS -o none
  az network private-dns zone create -n "$4" -g "$RG" --tags $TAGS -o none
  az network private-dns link vnet create -n "link-$VNET" -g "$RG" -z "$4" -v "$VNET" -e false -o none
  az network private-endpoint dns-zone-group create -n default -g "$RG" --endpoint-name "$1" \
    --private-dns-zone "$4" --zone-name "${4//./-}" -o none
}
private_endpoint pe-flwn-pg-uae-01 "$PG_ID" postgresqlServer privatelink.postgres.database.azure.com
private_endpoint pe-flwn-blob-uae-01 "$SA_ID" blob privatelink.blob.core.windows.net

# ---- Image (built in ACR, no local Docker needed) -------------------------------------------
az acr repository show -n "$ACR" --image "$IMAGE" -o none 2>/dev/null \
  || az acr build -r "$ACR" -t "$IMAGE" --no-logs .   # skip if this commit is already built

# ---- Container Apps: VNet environment, public ingress ---------------------------------------
SUBNET_ID=$(az network vnet subnet show -n snet-apps --vnet-name "$VNET" -g "$RG" --query id -o tsv)
az containerapp env create -n "$CAE" -g "$RG" -l "$LOC" --infrastructure-subnet-resource-id "$SUBNET_ID" \
  --enable-workload-profiles true --logs-destination none --tags $TAGS -o none
CAE_DOMAIN=$(az containerapp env show -n "$CAE" -g "$RG" --query properties.defaultDomain -o tsv)
FQDN="$APP.$CAE_DOMAIN"

APP_ARGS=(
  --image "$ACR.azurecr.io/$IMAGE" --registry-server "$ACR.azurecr.io" --registry-identity "$ID_ID"
  --user-assigned "$ID_ID" --workload-profile-name Consumption
  --cpu 0.5 --memory 1Gi --min-replicas 0 --max-replicas 1
  --target-port 8002 --ingress external
  --secrets "database-url=$(env_val DATABASE_URL)" "foundry-key=$(env_val AZURE_FOUNDRY_KEY)" \
            "data-api-key=$(env_val DATA_API_KEY)" "token-secret=$(env_val MEMORY_TOKEN_SECRET)"
  --env-vars DATABASE_URL=secretref:database-url AZURE_FOUNDRY_KEY=secretref:foundry-key \
             DATA_API_KEY=secretref:data-api-key MEMORY_TOKEN_SECRET=secretref:token-secret \
             "AZURE_FOUNDRY_ENDPOINT=$(env_val AZURE_FOUNDRY_ENDPOINT)" \
             "AZURE_STORAGE_ACCOUNT_URL=https://$SA.blob.core.windows.net" \
             AZURE_CLIENT_ID="$(az identity show -n "$ID" -g "$RG" --query clientId -o tsv)" \
             "MCP_ALLOWED_HOSTS=$FQDN"
)
if az containerapp show -n "$APP" -g "$RG" -o none 2>/dev/null; then
  az containerapp update -n "$APP" -g "$RG" --image "$ACR.azurecr.io/$IMAGE" -o none
else
  az containerapp create -n "$APP" -g "$RG" --environment "$CAE" --tags $TAGS "${APP_ARGS[@]}" -o none
fi

# ---- Gate: lock the data stores only once the app reaches Postgres over the private endpoint --
for _ in $(seq 1 30); do
  curl -fsS -H "X-Data-Api-Key: $(env_val DATA_API_KEY)" "https://$FQDN/readyz" && break
  sleep 10
done || { echo "readyz never passed; data stores left open"; exit 1; }

az postgres flexible-server update -n "$PG" -g "$RG" --public-access Disabled -o none
az storage account update -n "$SA" -g "$RG" --public-network-access Disabled --default-action Deny -o none
echo "Deployed: https://$FQDN  (Postgres and Blob now reachable only from $VNET)"

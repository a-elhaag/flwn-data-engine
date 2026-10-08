#!/usr/bin/env bash
# Azure resources for the data engine, as they were created. Re-runnable: every command is
# create-or-update. Needs `az login` with rights on the subscription. No secrets live here:
# passwords are generated at run time and printed only as the .env lines you must save.
set -euo pipefail

SUB="${SUB:-939aff7a-6509-457f-810a-b90459c40d21}"   # Azure subscription 1
RG="${RG:-flwn-data-engine}"
LOC="${LOC:-uaenorth}"
PG="${PG:-flwn-pg-uae-01}"
SA="${SA:-stflwndatauae01}"
MY_IP="${MY_IP:-$(curl -fsS https://api.ipify.org)}"

az group create -n "$RG" -l "$LOC" --subscription "$SUB" --tags project=flwn env=dev -o none
az provider register --namespace Microsoft.DBforPostgreSQL --subscription "$SUB" --wait
az provider register --namespace Microsoft.Storage --subscription "$SUB" --wait

# ---- PostgreSQL: cheapest tier (about $19/month), pgvector allowed ------------------------
if ! az postgres flexible-server show -n "$PG" -g "$RG" --subscription "$SUB" -o none 2>/dev/null; then
  ADMIN_PW="$(openssl rand -base64 30 | tr -d '/+=\n' | cut -c1-32)"
  az postgres flexible-server create -n "$PG" -g "$RG" -l "$LOC" --subscription "$SUB" \
    --tier Burstable --sku-name Standard_B1ms --storage-size 32 --version 16 \
    --admin-user flwnadmin --admin-password "$ADMIN_PW" --public-access "$MY_IP" \
    --backup-retention 7 --geo-redundant-backup Disabled --tags project=flwn env=dev --yes -o none
  echo "DATABASE_ADMIN_URL=postgresql+psycopg://flwnadmin:${ADMIN_PW}@${PG}.postgres.database.azure.com:5432/flwn?sslmode=require"
fi
az postgres flexible-server parameter set --server-name "$PG" -g "$RG" --subscription "$SUB" \
  --name azure.extensions --value VECTOR -o none
az postgres flexible-server db create --server-name "$PG" -g "$RG" --subscription "$SUB" -d flwn -o none 2>/dev/null || true
# Then install the schema and the least-privilege app role (the admin role bypasses row-level
# security, so the API must not use it):
#   APP_DB_PASSWORD=<generate one> python -m app.db.install --app-role flwn_app

# ---- Blob storage: no public access, no account keys ----------------------------------------
az storage account create -n "$SA" -g "$RG" -l "$LOC" --subscription "$SUB" \
  --sku Standard_LRS --kind StorageV2 --access-tier Hot \
  --allow-blob-public-access false --min-tls-version TLS1_2 --https-only true \
  --allow-shared-key-access false --tags project=flwn env=dev -o none
for container in workspace-files chat-media meeting-recordings agent-reports; do
  az storage container-rm create --storage-account "$SA" -g "$RG" --subscription "$SUB" \
    --name "$container" --public-access off -o none
done
az storage account blob-service-properties update --account-name "$SA" -g "$RG" --subscription "$SUB" \
  --enable-delete-retention true --delete-retention-days 7 \
  --enable-container-delete-retention true --container-delete-retention-days 7 -o none
POLICY="$(mktemp)"
cat > "$POLICY" <<'JSON'
{"rules": [{"enabled": true, "name": "recordings-to-cool", "type": "Lifecycle",
  "definition": {"filters": {"blobTypes": ["blockBlob"], "prefixMatch": ["meeting-recordings/"]},
                 "actions": {"baseBlob": {"tierToCool": {"daysAfterModificationGreaterThan": 30}}}}}]}
JSON
az storage account management-policy create --account-name "$SA" -g "$RG" --subscription "$SUB" --policy "@$POLICY" -o none
rm -f "$POLICY"

# The service reaches storage through its Entra identity. Grant that identity (a developer for
# local work, the managed identity of the deployed app) this role on the account:
#   az role assignment create --assignee-object-id <oid> --role "Storage Blob Data Contributor" \
#     --scope "$(az storage account show -n $SA -g $RG --subscription $SUB --query id -o tsv)"
echo "AZURE_STORAGE_ACCOUNT_URL=https://${SA}.blob.core.windows.net"

#!/bin/bash
# ============================================================
# Azure パラメータ収集スクリプト
# 使い方: ./collect_azure_params.sh <ResourceGroupName>
# 出力: azure_params_<RG名>.json
# ============================================================

set -e

if [ -z "$1" ]; then
  echo "Usage: $0 <ResourceGroupName>"
  exit 1
fi

RG="$1"
OUTPUT_FILE="azure_params_${RG}.json"

echo "対象リソースグループ: $RG"
echo "収集中..."

# ログインチェック
az account show > /dev/null 2>&1 || { echo "az login してください"; exit 1; }

collect_vnets() {
  az network vnet list -g "$RG" --query "[].{
    resourceType: 'VNet',
    name: name,
    location: location,
    addressSpace: addressSpace.addressPrefixes,
    dnsServers: dhcpOptions.dnsServers,
    ddosProtection: enableDdosProtection,
    vmProtection: enableVmProtection,
    subnets: subnets[].{
      name: name,
      addressPrefix: addressPrefix,
      nsgId: networkSecurityGroup.id,
      routeTableId: routeTable.id,
      serviceEndpoints: serviceEndpoints[].service,
      privateEndpointNetworkPolicies: privateEndpointNetworkPolicies,
      privateLinkServiceNetworkPolicies: privateLinkServiceNetworkPolicies,
      delegations: delegations[].serviceName
    }
  }" -o json 2>/dev/null || echo "[]"
}

collect_nsgs() {
  az network nsg list -g "$RG" --query "[].{
    resourceType: 'NSG',
    name: name,
    location: location,
    securityRules: securityRules[].{
      name: name,
      priority: priority,
      direction: direction,
      access: access,
      protocol: protocol,
      sourceAddressPrefix: sourceAddressPrefix,
      sourcePortRange: sourcePortRange,
      destinationAddressPrefix: destinationAddressPrefix,
      destinationPortRange: destinationPortRange,
      description: description
    }
  }" -o json 2>/dev/null || echo "[]"
}

collect_storage() {
  az storage account list -g "$RG" --query "[].{
    resourceType: 'StorageAccount',
    name: name,
    location: location,
    sku: sku.name,
    kind: kind,
    accessTier: accessTier,
    httpsOnly: enableHttpsTrafficOnly,
    tlsVersion: minimumTlsVersion,
    allowBlobPublicAccess: allowBlobPublicAccess,
    allowSharedKeyAccess: allowSharedKeyAccess,
    networkAclsDefaultAction: networkRuleSet.defaultAction,
    networkAclsBypass: networkRuleSet.bypass,
    networkAclsIpRules: networkRuleSet.ipRules[].iPAddressOrRange,
    networkAclsVnetRules: networkRuleSet.virtualNetworkRules[].id,
    largeFileShares: largeFileSharesState,
    blobSoftDelete: properties.deleteRetentionPolicy.enabled,
    blobSoftDeleteDays: properties.deleteRetentionPolicy.days,
    isHnsEnabled: isHnsEnabled,
    encryption: encryption.services.blob.keyType
  }" -o json 2>/dev/null || echo "[]"
}

collect_keyvault() {
  az keyvault list -g "$RG" --query "[].{
    resourceType: 'KeyVault',
    name: name,
    location: location,
    sku: properties.sku.name,
    tenantId: properties.tenantId,
    softDeleteEnabled: properties.enableSoftDelete,
    softDeleteRetentionDays: properties.softDeleteRetentionInDays,
    purgeProtection: properties.enablePurgeProtection,
    rbacAuthorization: properties.enableRbacAuthorization,
    publicNetworkAccess: properties.publicNetworkAccess,
    networkAclsDefaultAction: properties.networkAcls.defaultAction,
    networkAclsBypass: properties.networkAcls.bypass,
    networkAclsIpRules: properties.networkAcls.ipRules[].value,
    networkAclsVnetRules: properties.networkAcls.virtualNetworkRules[].id
  }" -o json 2>/dev/null || echo "[]"
}

collect_container_app_env() {
  az containerapp env list -g "$RG" --query "[].{
    resourceType: 'ContainerAppsEnvironment',
    name: name,
    location: location,
    infrastructureSubnetId: properties.vnetConfiguration.infrastructureSubnetId,
    internal: properties.vnetConfiguration.internal,
    dockerBridgeCidr: properties.vnetConfiguration.dockerBridgeCidr,
    platformReservedCidr: properties.vnetConfiguration.platformReservedCidr,
    platformReservedDnsIP: properties.vnetConfiguration.platformReservedDnsIP,
    logAnalyticsWorkspaceId: properties.appLogsConfiguration.logAnalyticsConfiguration.customerId,
    zoneRedundant: properties.zoneRedundant
  }" -o json 2>/dev/null || echo "[]"
}

collect_container_apps() {
  az containerapp list -g "$RG" --query "[].{
    resourceType: 'ContainerApp',
    name: name,
    location: location,
    environmentId: properties.environmentId,
    ingressExternal: properties.configuration.ingress.external,
    ingressTargetPort: properties.configuration.ingress.targetPort,
    ingressTransport: properties.configuration.ingress.transport,
    minReplicas: properties.template.scale.minReplicas,
    maxReplicas: properties.template.scale.maxReplicas,
    containers: properties.template.containers[].{
      name: name,
      image: image,
      cpu: resources.cpu,
      memory: resources.memory
    },
    managedIdentity: identity.type,
    registries: properties.configuration.registries[].server
  }" -o json 2>/dev/null || echo "[]"
}

# 全リソースを収集してJSONにまとめる
python3 - <<PYEOF
import json, subprocess, sys

def run(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    try:
        return json.loads(r.stdout) if r.stdout.strip() else []
    except:
        return []

rg = "$RG"
result = {
    "resourceGroup": rg,
    "vnets":                run(f'az network vnet list -g {rg} -o json'),
    "nsgs":                 run(f'az network nsg list -g {rg} -o json'),
    "storageAccounts":      run(f'az storage account list -g {rg} -o json'),
    "keyVaults":            run(f'az keyvault list -g {rg} -o json'),
    "containerAppEnvs":     run(f'az containerapp env list -g {rg} -o json'),
    "containerApps":        run(f'az containerapp list -g {rg} -o json'),
    "applicationGateways":  run(f'az network application-gateway list -g {rg} -o json'),
    "firewalls":            run(f'az network firewall list -g {rg} -o json'),
}

with open("$OUTPUT_FILE", "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

print(f"収集完了: $OUTPUT_FILE")
counts = {k: len(v) for k, v in result.items() if isinstance(v, list)}
for k, v in counts.items():
    print(f"  {k}: {v}件")
PYEOF

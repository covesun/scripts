# arm_docs_cache.json — 仕様ドキュメント

Azure Portal のポータルUI名と ARM プロパティ名のマッピングキャッシュ。
パラメータシートの合否判定スクリプトから参照することを想定している。

---

## ファイル構造

```json
{
  "<resource_type>": {
    "<arm_key>": {
      "name": "<ARMプロパティ名（ドット記法）>",
      "portal_name": "<ポータルUI名（代表）>",
      "portal_names": {
        "<メニューパス>": "<そのメニューでの表示名>",
        ...
      },
      "confirmed": true,
      "description": "<MSLearnドキュメントからの説明>",
      "type_info": "<型情報>",
      "value_map": {
        "<ポータル表示値>": "<ARM値>",
        ...
      }
    }
  }
}
```

### resource_type 一覧

| キー | Azureリソース |
|---|---|
| `storage` | Microsoft.Storage/storageAccounts |
| `network` | Microsoft.Network/virtualNetworks |
| `nsg` | Microsoft.Network/networkSecurityGroups |
| `keyvault` | Microsoft.KeyVault/vaults |
| `caenv` | Microsoft.App/managedEnvironments |
| `ca` | Microsoft.App/containerApps |
| `agw` | Microsoft.Network/applicationGateways |
| `firewall` | Microsoft.Network/azureFirewalls |
| `firewallpolicy` | Microsoft.Network/firewallPolicies |
| `wafpolicy` | Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies |

---

## arm_key の生成ルール

ARM プロパティのドット記法パスから以下のロジックでキーを生成する。

```python
import re

GENERIC_KEYS = {
    "enabled", "disabled", "days", "name", "id", "type",
    "value", "key", "tier", "action", "choice", "state",
}

def arm_to_key(arm_prop: str) -> str:
    """
    ARMプロパティパスをキャッシュキーに変換する。

    末端キーが汎用名（enabled, days, name など）の場合は
    親プロパティと組み合わせて一意にする。

    Examples:
        "minimumTlsVersion"                    → "minimumtlsversion"
        "properties.minimumTlsVersion"         → "minimumtlsversion"
        "deleteRetentionPolicy.enabled"        → "deleteretentionpolicy.enabled"
        "containerDeleteRetentionPolicy.days"  → "containerdeleteretentionpolicy.days"
        "sku.name"                             → "sku.name"
        "sku.tier"                             → "sku.tier"
    """
    parts = [p for p in re.split(r"[.\[\*\]]+", arm_prop) if p]
    last = parts[-1].lower()
    if last in GENERIC_KEYS and len(parts) >= 2:
        return f"{parts[-2].lower()}.{last}"
    return last
```

---

## ポータルUI名の正引き（ARMプロパティ → UI名）

```python
import json
import re

GENERIC_KEYS = {
    "enabled", "disabled", "days", "name", "id", "type",
    "value", "key", "tier", "action", "choice", "state",
}

def arm_to_key(arm_prop: str) -> str:
    parts = [p for p in re.split(r"[.\[\*\]]+", arm_prop) if p]
    last = parts[-1].lower()
    if last in GENERIC_KEYS and len(parts) >= 2:
        return f"{parts[-2].lower()}.{last}"
    return last


def get_portal_name(cache: dict, resource_type: str, arm_path: str) -> str:
    """
    ARM プロパティパスからポータルUI名（代表名）を返す。
    マッチしない場合は空文字。

    Args:
        cache:         arm_docs_cache.json を json.load() したdict
        resource_type: "storage" / "network" / "nsg" / "keyvault" / "caenv" /
                       "ca" / "agw" / "firewall" / "firewallpolicy" / "wafpolicy"
        arm_path:      ARMプロパティのパス
                       例: "properties.minimumTlsVersion"
                           "minimumTlsVersion"
                           "sku.name"

    Returns:
        ポータルUI名文字列。例: "TLS の最小バージョン"
        portal_names が複数ある場合は
        "メニューパス > UI名 | メニューパス > UI名" の形式。
    """
    key = arm_to_key(arm_path)
    store = cache.get(resource_type, {})
    entry = store.get(key)
    if not entry:
        return ""

    portal_names = entry.get("portal_names", {})
    if portal_names:
        return " | ".join(f"{path} > {name}" for path, name in portal_names.items())
    return entry.get("portal_name", "")


# 使用例
with open("arm_docs_cache.json", encoding="utf-8") as f:
    cache = json.load(f)

# Storage Account
print(get_portal_name(cache, "storage", "properties.minimumTlsVersion"))
# → "概要 / プロパティ / セキュリティ > TLS の最小バージョン | 設定 / 構成 > TLS の最小バージョン"

print(get_portal_name(cache, "storage", "properties.allowBlobPublicAccess"))
# → "概要 / プロパティ / Blob service > BLOB 匿名アクセス | 設定 / 構成 > BLOB 匿名アクセスを許可する"

# Key Vault
print(get_portal_name(cache, "keyvault", "properties.softDeleteRetentionInDays"))
# → "設定 / プロパティ > 削除されたコンテナーを保持する日数"
```

---

## 値マッピング（ARM値 ↔ ポータル表示値）

`value_map` フィールドに「ポータル表示値 → ARM値」の対応が入っている。

```python
def get_value_map(cache: dict, resource_type: str, arm_path: str) -> dict:
    """
    ARM値とポータル表示値の対応表を返す。
    例: {"バージョン 1.2": "TLS1_2", "バージョン 1.0": "TLS1_0"}
    """
    key = arm_to_key(arm_path)
    entry = cache.get(resource_type, {}).get(key, {})
    return entry.get("value_map", {})

# 使用例: ポータル表示値 "バージョン 1.2" → ARM値 "TLS1_2" に変換
vm = get_value_map(cache, "storage", "properties.minimumTlsVersion")
arm_value = vm.get("バージョン 1.2")  # → "TLS1_2"
```

---

## パラメータシートとの突合フロー

```
1. ARM JSON を az CLI で取得
   az storage account show -n <name> -g <rg> -o json > actual.json

2. パラメータシート（設計書）のUI名列を読む
   例: "TLS の最小バージョン" → 期待値 "バージョン 1.2"

3. ARM JSON をフラット化してプロパティ値を取り出す
   actual["properties"]["minimumTlsVersion"] → "TLS1_2"

4. value_map で ARM値をポータル表示値に変換
   "TLS1_2" → "バージョン 1.2"

5. パラメータシートの期待値と比較 → 合否判定
   "バージョン 1.2" == "バージョン 1.2" → OK
```

---

## confirmed フラグ

`"confirmed": true` のエントリは、実際の Azure Portal スクリーンショットと ARM JSON の値を照合して確認済みであることを示す。

`confirmed` が `false` またはないエントリは、MS Learn ドキュメントからの推定マッピング。

---

## portal_names のメニューパス形式

```
"左メニューカテゴリ / 左メニュー項目 / ページ内タブ or セクション"
```

例：
- `"概要 / プロパティ / Blob service"`
- `"設定 / 構成"`
- `"セキュリティとネットワーク / ネットワーク / パブリック アクセス"`
- `"データ管理 / データ保護 / 復旧"`

同一プロパティが複数のポータル画面に表示される場合、すべてのパスが `portal_names` に記録されている。

---

## カバレッジ（2026-05-31 時点）

| リソース | カバレッジ |
|---|---|
| Container Apps Env | 100% |
| Azure Firewall | 100% |
| WAF Policy | 100% |
| VNet | 94% |
| Storage Account | 93% |
| NSG | 93% |
| Key Vault | 93% |
| Container App | 90% |
| Application Gateway | 83% |
| Firewall Policy | 83% |
| **合計** | **92%** |

※ 未カバーの項目は主に `sku.family`（固定値）、サブリソースID（ARM内部ID）、
  HTTPS バックエンド専用設定（`validateSNI` 等）。

---

## キャッシュの更新方法

新しいリソースタイプや未登録プロパティを追加する場合：

```bash
# 1. フィンガープリント済み ARM テンプレートをデプロイ
az deployment group create -g rg-mapping-test \
  --template-file mapping_templates/storage_profile_a.json

# 2. ポータルスクリーンショットを自動キャプチャ（ARM JSON も自動保存）
python3 capture_portal_ui.py \
  --resource-type storage \
  --arm-json mapping_templates/storage_profile_a.json \
  --no-restart

# 3. カバレッジ確認（削除前に必ず実行）
python3 json_to_csv.py arm_samples/storage.json /tmp/check.csv --resource storage

# 4. 不足ページがあれば追加キャプチャ（--pages オプション）
python3 capture_portal_ui.py --resource-type storage \
  --arm-json mapping_templates/storage_profile_a.json \
  --pages accountEncryption --no-restart

# 5. 削除
az group delete --name rg-mapping-test --yes --no-wait
```

# Azure Parameter Sheet Toolset

Azure リソースのパラメータシート自動生成・突合チェックツールセット。

Azure Portal のUI名と ARM プロパティのマッピング情報（`arm_docs_cache.json`）を中心に、
設計書との差分確認・パラメータシートExcel生成を行う。

---

## スクリプト一覧

| スクリプト | 役割 |
|---|---|
| `collect_azure_params.sh` | az CLI で実環境の ARM JSON を収集 |
| `generate_param_sheet.py` | ARM JSON → パラメータシート Excel を生成 |
| `check_param_diff.py` | 設計書 Excel × 実環境 JSON → 合否判定 Excel を生成 |
| `json_to_csv.py` | ARM JSON → CSV（ポータルUI名付き）に変換 |
| `capture_portal_ui.py` | Azure Portal を CDP 経由で自動キャプチャ（キャッシュ更新用） |
| `import_screenshots.py` | スクリーンショット × ARM JSON → キャッシュに値照合マッピングを登録 |
| `fetch_arm_docs.py` | MS Learn ARM ドキュメントから description を取得 |

---

## クイックスタート

### 1. 実環境からパラメータ収集

```bash
bash collect_azure_params.sh <resource-group-name>
# → output/<rg-name>_params.json が生成される
```

### 2. パラメータシート生成（UI名付き Excel）

```bash
python3 generate_param_sheet.py output/<rg-name>_params.json
# → output/<rg-name>_paramsheet.xlsx が生成される
```

### 3. 設計書との突合チェック

```bash
python3 check_param_diff.py 設計書.xlsx output/<rg-name>_params.json
# → 差分があるセルが赤ハイライトで出力される
```

### 4. ARM JSON を CSV で確認（ポータルUI名付き）

```bash
# az resource show の出力や ARMテンプレートを食わせる
az storage account show -n <name> -g <rg> -o json > storage.json
python3 json_to_csv.py storage.json --resource storage
# → storage.csv（JSONパス / 値 / ポータルUI名(候補) 列）
```

---

## キャッシュの活用（外部スクリプトから）

`arm_docs_cache.json` は ARM プロパティ名 → ポータルUI名のマッピングを提供する。
詳細仕様は **[CACHE_SPEC.md](./CACHE_SPEC.md)** を参照。

### 最小サンプル

```python
import json, re

with open("arm_docs_cache.json", encoding="utf-8") as f:
    cache = json.load(f)

GENERIC_KEYS = {"enabled","disabled","days","name","id","type",
                "value","key","tier","action","choice","state"}

def arm_to_key(arm_prop: str) -> str:
    parts = [p for p in re.split(r"[.\[\*\]]+", arm_prop) if p]
    last = parts[-1].lower()
    if last in GENERIC_KEYS and len(parts) >= 2:
        return f"{parts[-2].lower()}.{last}"
    return last

def get_portal_name(resource_type: str, arm_path: str) -> str:
    key = arm_to_key(arm_path)
    entry = cache.get(resource_type, {}).get(key, {})
    portal_names = entry.get("portal_names", {})
    if portal_names:
        return " | ".join(f"{p} > {n}" for p, n in portal_names.items())
    return entry.get("portal_name", "")

# 例
get_portal_name("storage", "properties.minimumTlsVersion")
# → "概要 / プロパティ / セキュリティ > TLS の最小バージョン | 設定 / 構成 > TLS の最小バージョン"

get_portal_name("keyvault", "properties.enableSoftDelete")
# → "設定 / プロパティ > 論理的な削除"
```

---

## キャッシュのカバレッジ（2026-05-31 時点）

| リソース | カバレッジ |
|---|---|
| Container Apps Environment | 100% |
| Azure Firewall | 100% |
| WAF Policy (AGW) | 100% |
| VNet | 94% |
| Storage Account | 93% |
| NSG | 93% |
| Key Vault | 93% |
| Container App | 90% |
| Application Gateway | 83% |
| Firewall Policy | 83% |
| **合計** | **92%** |

---

## 対応リソースタイプ

| キー | ARM リソースタイプ |
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

## キャッシュの更新

ポータル画面が変わった場合やリソースタイプを追加する場合の手順：

1. `mapping_templates/` に ARM テンプレート（フィンガープリント済み値）を作成
2. Azure にデプロイ
3. `capture_portal_ui.py` で全ページをキャプチャ（ARM JSON も自動保存）
4. **削除前に** `json_to_csv.py` でカバレッジを確認
5. 不足ページがあれば追加キャプチャ
6. リソース削除

詳細は [CACHE_SPEC.md](./CACHE_SPEC.md) の「キャッシュの更新方法」を参照。

---

## 依存ライブラリ

```bash
pip install openpyxl requests websocket-client anthropic openai
```

## API キー設定（キャッシュ更新時のみ必要）

| 環境変数 | 用途 |
|---|---|
| `AZURE_FOUNDRY_ENDPOINT` + `AZURE_FOUNDRY_API_KEY` | Azure AI Foundry (Claude) — 会社環境推奨 |
| `AZURE_OPENAI_ENDPOINT` + `AZURE_OPENAI_API_KEY` | Azure OpenAI (GPT-4o) |
| `GITHUB_TOKEN` | GitHub Models (GPT-4o) |
| `ANTHROPIC_API_KEY` | Anthropic Claude — 個人環境 |

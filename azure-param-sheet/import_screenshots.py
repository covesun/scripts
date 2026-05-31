#!/usr/bin/env python3
"""
Azure Portal スクリーンショット + ARMテンプレートからUI名マッピングをキャッシュに保存する

仕組み:
  1. ARMテンプレート（または az resource show の出力）からプロパティ値を読み込む
  2. ポータルのスクリーンショットをAIに送る
  3. AIが「ARMの値と画面の表示値が一致する」項目だけをマッピングとして返す
  4. arm_docs_cache.json に保存する

使い方:
  # ARMテンプレートをダウンロードしてスクリーンショットと照合
  python3 import_screenshots.py <スクリーンショットフォルダ> --arm-json <template.json>

  # az resource show の出力でも可
  az storage account show -n <name> -g <rg> -o json > storage.json
  python3 import_screenshots.py <フォルダ> --arm-json storage.json

API は環境変数で自動判定（優先順位順）:

  1. Azure AI Foundry（Claude Sonnet）← 会社環境の推奨
       AZURE_FOUNDRY_ENDPOINT=https://xxx.services.ai.azure.com/...
       AZURE_FOUNDRY_API_KEY=xxxxxx
       AZURE_FOUNDRY_MODEL=claude-sonnet-4-6   # 省略時: claude-sonnet-4-6

  2. Azure OpenAI（GPT-4o）
       AZURE_OPENAI_ENDPOINT=https://xxx.openai.azure.com/
       AZURE_OPENAI_API_KEY=xxxxxx
       AZURE_OPENAI_MODEL=gpt-4o               # 省略時: gpt-4o

  3. GitHub Models（GitHub Token で使える）
       GITHUB_TOKEN=ghp_xxxx

  4. Anthropic（個人環境）
       ANTHROPIC_API_KEY=sk-ant-xxxx

インストール:
  pip install openai anthropic
"""

import base64
import json
import os
import re
import sys
from pathlib import Path

CACHE_FILE = Path(__file__).parent / "arm_docs_cache.json"

# リソースタイプ文字列 → キャッシュキー
_RESOURCE_TYPE_MAP = {
    "microsoft.storage/storageaccounts":       "storage",
    "microsoft.network/virtualnetworks":        "network",
    "microsoft.network/networksecuritygroups":  "nsg",
    "microsoft.keyvault/vaults":                "keyvault",
    "microsoft.app/managedenvironments":        "caenv",
    "microsoft.app/containerapps":              "ca",
    "microsoft.network/applicationgateways":    "agw",
    "microsoft.network/azurefirewalls":         "firewall",
}

# 末端キーだけでは一意にならない汎用名（親プロパティと組み合わせてキーにする）
_GENERIC_KEYS = {
    "enabled", "disabled", "days", "name", "id", "type",
    "value", "key", "tier", "action", "choice", "state",
}


def _arm_to_key(arm_prop: str) -> str:
    """
    ARMプロパティ名をキャッシュキーに変換する。
    末端キーが汎用名（enabled, days, name 等）の場合は
    親プロパティと組み合わせて一意にする。

    例:
      deleteRetentionPolicy.enabled → "deleteretentionpolicy.enabled"
      containerDeleteRetentionPolicy.days → "containerdeleteretentionpolicy.days"
      minimumTlsVersion → "minimumtlsversion"
      sku.name → "sku.name"
    """
    parts = [p for p in re.split(r"[.\[\*\]]+", arm_prop) if p]
    last = parts[-1].lower()
    if last in _GENERIC_KEYS and len(parts) >= 2:
        return f"{parts[-2].lower()}.{last}"
    return last


# フラット化時に除外するARMメタデータキー
_SKIP_KEYS = {
    "$schema", "contentVersion", "apiVersion", "dependsOn",
    "id", "etag", "type", "systemData", "tags",
}


# ============================================================
# API クライアント自動判定
# ============================================================

def create_client():
    """戻り値: (client, backend, model)"""
    if os.environ.get("AZURE_FOUNDRY_ENDPOINT") and os.environ.get("AZURE_FOUNDRY_API_KEY"):
        import anthropic
        client = anthropic.Anthropic(
            base_url=os.environ["AZURE_FOUNDRY_ENDPOINT"],
            api_key=os.environ["AZURE_FOUNDRY_API_KEY"],
        )
        model = os.environ.get("AZURE_FOUNDRY_MODEL", "claude-sonnet-4-6")
        print(f"API: Azure AI Foundry / Claude ({model})")
        return client, "anthropic", model

    if os.environ.get("AZURE_OPENAI_ENDPOINT") and os.environ.get("AZURE_OPENAI_API_KEY"):
        from openai import AzureOpenAI
        client = AzureOpenAI(
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            api_version="2024-05-01-preview",
        )
        model = os.environ.get("AZURE_OPENAI_MODEL", "gpt-4o")
        print(f"API: Azure OpenAI / GPT-4o ({model})")
        return client, "openai", model

    if os.environ.get("GITHUB_TOKEN"):
        from openai import OpenAI
        client = OpenAI(
            base_url="https://models.inference.ai.azure.com",
            api_key=os.environ["GITHUB_TOKEN"],
        )
        print("API: GitHub Models / GPT-4o")
        return client, "openai", "gpt-4o"

    if os.environ.get("ANTHROPIC_API_KEY"):
        import anthropic
        client = anthropic.Anthropic()
        model = "claude-sonnet-4-6"
        print(f"API: Anthropic / Claude ({model})")
        return client, "anthropic", model

    print("エラー: API キーが設定されていません。以下のいずれかを設定してください:")
    print("  Azure AI Foundry: AZURE_FOUNDRY_ENDPOINT + AZURE_FOUNDRY_API_KEY")
    print("  Azure OpenAI:     AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_API_KEY")
    print("  GitHub Models:    GITHUB_TOKEN")
    print("  Anthropic:        ANTHROPIC_API_KEY")
    sys.exit(1)


# ============================================================
# ARM JSON のフラット化・リソースタイプ検出
# ============================================================

def flatten_arm(obj, prefix="", result=None) -> dict:
    """ARM JSON を {dotted.path: value} のフラット辞書に変換する"""
    if result is None:
        result = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _SKIP_KEYS:
                continue
            flatten_arm(v, f"{prefix}.{k}" if prefix else k, result)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            flatten_arm(item, f"{prefix}[{i}]", result)
    else:
        if obj is not None:
            result[prefix] = obj
    return result


def load_arm_values(arm_json_path: Path) -> tuple[dict, str | None]:
    """
    ARM JSON を読み込み (フラット値辞書, リソースタイプキー) を返す。
    ARMテンプレートの場合は最初のメインリソース（サブリソースでないもの）を対象とする。
    """
    with open(arm_json_path, encoding="utf-8") as f:
        data = json.load(f)

    # リソースタイプ検出
    t = data.get("type", "")
    if t:
        resource_type = _RESOURCE_TYPE_MAP.get(t.lower())
    else:
        resources = data.get("resources", [])
        t = resources[0].get("type", "") if resources else ""
        resource_type = _RESOURCE_TYPE_MAP.get(t.lower())

    # ARMテンプレートの場合: resources配列から最初のメインリソースを抽出
    if "resources" in data:
        resources = data["resources"]
        main = next(
            (r for r in resources if r.get("type", "").count("/") == 1),
            resources[0] if resources else data,
        )
        flat = flatten_arm(main)
    else:
        flat = flatten_arm(data)

    return flat, resource_type


# ============================================================
# 画像解析
# ============================================================

def build_prompt(arm_values: dict) -> str:
    values_str = "\n".join(
        f"  {k} = {json.dumps(v, ensure_ascii=False)}"
        for k, v in arm_values.items()
    )
    return f"""このAzure Portalのスクリーンショットを解析してください。

以下は、このスクリーンショットと同じリソースのARMプロパティと実際の値です:
{values_str}

画面上に表示されているUI項目を読み取り、上記のARMの値と一致するものを紐づけてください。

照合ルール:
- ARMの値と画面の表示値が対応している項目のみを返す
  例: ARM "TLS1_2" ↔ 画面 "バージョン 1.2"、ARM false ↔ 画面 "無効"、ARM true ↔ 画面 "有効"
- 値が画面に表示されていない、または値の対応が確認できないプロパティは除外する
- arm_property はドット記法の末端キー名で記載する（例: "minimumTlsVersion"）

以下のJSON形式のみで返してください：

```json
{{
  "resource_type": "storage",
  "mappings": [
    {{
      "portal_path": "設定 / 構成",
      "ui_name": "TLS の最小バージョン",
      "arm_property": "minimumTlsVersion",
      "value_display": "バージョン 1.2",
      "value_arm": "TLS1_2"
    }}
  ]
}}
```

フィールドの説明:
- resource_type: storage / network / nsg / keyvault / caenv / ca / agw / firewall のいずれか
- portal_path: 左メニュー名 / ページ名 / セクション名 の組み合わせ
    例: "設定 / 構成", "概要 / プロパティ / Blob service",
        "セキュリティとネットワーク / ネットワーク / パブリック アクセス"
- ui_name: 画面上の日本語ラベル（正確に）
- arm_property: 対応するARMプロパティ名
- value_display: 画面上の表示値
- value_arm: ARMでの値

JSONのみ返してください。"""


def analyze(img_path: Path, client, backend: str, model: str, arm_values: dict) -> dict:
    with open(img_path, "rb") as f:
        img_b64 = base64.standard_b64encode(f.read()).decode()

    suffix = img_path.suffix.lower()
    media_type = "image/png" if suffix == ".png" else "image/jpeg"
    prompt = build_prompt(arm_values)

    if backend == "anthropic":
        resp = client.messages.create(
            model=model,
            max_tokens=3000,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": media_type, "data": img_b64,
                    }},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        text = resp.content[0].text

    else:  # openai / azure / github
        resp = client.chat.completions.create(
            model=model,
            max_tokens=3000,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {
                        "url": f"data:{media_type};base64,{img_b64}",
                        "detail": "high",
                    }},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        text = resp.choices[0].message.content

    m = re.search(r"```json\s*([\s\S]*?)\s*```", text)
    return json.loads(m.group(1) if m else text.strip())


# ============================================================
# キャッシュ更新
# ============================================================

def load_cache() -> dict:
    if CACHE_FILE.exists():
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(cache: dict):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def update_cache(cache: dict, resource_type: str, mappings: list[dict]) -> int:
    store = cache.setdefault(resource_type, {})
    updated = 0
    for item in mappings:
        arm_prop    = item.get("arm_property")
        ui_name     = item.get("ui_name", "").strip()
        portal_path = item.get("portal_path", "").strip()
        if not ui_name:
            continue

        if arm_prop:
            key = _arm_to_key(arm_prop)
        else:
            key = re.sub(r"\s+", "_", ui_name).lower()
        if not key:
            continue

        entry = store.setdefault(key, {
            "name": arm_prop or key,
            "portal_name": "",
            "portal_names": {},
            "description": "",
            "type_info": "",
        })

        old = entry.get("portal_name", "")
        entry["portal_name"] = ui_name
        if arm_prop:
            entry["name"] = arm_prop
        if portal_path:
            entry.setdefault("portal_names", {})[portal_path] = ui_name

        # 値照合済みフラグ
        entry["confirmed"] = True

        v_disp = item.get("value_display", "")
        v_arm  = item.get("value_arm", "")
        if v_disp and v_arm and v_disp != v_arm:
            entry.setdefault("value_map", {})[v_disp] = v_arm

        if old != ui_name:
            updated += 1

    return updated


# ============================================================
# メイン
# ============================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Azure Portal スクリーンショット + ARMからUI名マッピングをキャッシュに保存する"
    )
    parser.add_argument("folder", help="スクリーンショットが入ったフォルダパス")
    parser.add_argument(
        "--arm-json", required=True, metavar="FILE",
        help="ARMテンプレートまたは az resource show の出力JSON",
    )
    args = parser.parse_args()

    folder = Path(args.folder).expanduser()
    if not folder.exists():
        print(f"フォルダが見つかりません: {folder}")
        sys.exit(1)

    arm_json_path = Path(args.arm_json).expanduser()
    if not arm_json_path.exists():
        print(f"ARM JSONが見つかりません: {arm_json_path}")
        sys.exit(1)

    exts = {".png", ".jpg", ".jpeg", ".webp"}
    images = sorted(f for f in folder.iterdir() if f.suffix.lower() in exts)
    if not images:
        print(f"画像ファイルが見つかりません: {folder}")
        sys.exit(1)

    arm_values, arm_resource_type = load_arm_values(arm_json_path)
    print(f"ARM JSON: {arm_json_path.name}  ({len(arm_values)} プロパティ)")
    if arm_resource_type:
        print(f"リソースタイプ: {arm_resource_type}")
    else:
        print("警告: リソースタイプを自動検出できませんでした。AIが推定します。")
    print()

    client, backend, model = create_client()
    print(f"{len(images)} 枚の画像を処理します\n")

    cache = load_cache()
    total = 0

    for img_path in images:
        print(f"[{img_path.name}]")
        try:
            result = analyze(img_path, client, backend, model, arm_values)
            rt       = result.get("resource_type") or arm_resource_type or "unknown"
            mappings = result.get("mappings", [])
            paths    = {m.get("portal_path", "") for m in mappings if m.get("portal_path")}
            print(f"  → {rt}  ({len(mappings)} 件マッチ)  paths: {paths}")
            n = update_cache(cache, rt, mappings)
            total += n
            print(f"  → {n} 件更新")
        except Exception as e:
            print(f"  エラー: {e}")

    save_cache(cache)
    print(f"\n完了: 合計 {total} 件更新 → {CACHE_FILE}")


if __name__ == "__main__":
    main()

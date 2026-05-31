#!/usr/bin/env python3
"""
Azure リソースJSON → CSV変換スクリプト

使い方:
  python3 json_to_csv.py <input.json> [output.csv]
  python3 json_to_csv.py <input.json> [output.csv] --resource storage

オプション:
  --resource <key>   arm_docs_cache.json のリソースキーを指定すると
                     「ドキュメント説明」列を追加する
                     キー例: storage / network / nsg / keyvault / caenv / ca / agw / firewall

出力列: 生のJSON / JSONパス / 値 [/ ドキュメント説明 / 使用可能値]
- JSONの全行を出力
- リーフ値の行のみ JSONパス・値を付与
- 空オブジェクト{} / 空配列[] はパスのみ付与
- 構造行（{, }, [, ]）はパス・値ともに空
"""

import csv
import json
import sys
from pathlib import Path

INDENT = "    "
CACHE_FILE = Path(__file__).parent / "arm_docs_cache.json"


# ============================================================
# ドキュメントキャッシュのロード
# ============================================================

def load_docs_cache(resource_key: str) -> dict:
    """
    arm_docs_cache.json から指定リソースのプロパティ辞書を返す。
    {property_name_lower: {description, type_info}}
    """
    if not CACHE_FILE.exists():
        return {}
    with open(CACHE_FILE, encoding="utf-8") as f:
        cache = json.load(f)
    return cache.get(resource_key, {})


_GENERIC_KEYS = {
    "enabled", "disabled", "days", "name", "id", "type",
    "value", "key", "tier", "action", "choice", "state",
}


def lookup_doc(docs: dict, json_path: str) -> tuple[str, str, str]:
    """
    JSONパス（例: $.properties.minimumTlsVersion）から
    (ポータルUI名候補, ドキュメント説明, 使用可能値) を返す。
    末端キー名で前方一致ルックアップ。

    ポータルUI名候補は portal_names がある場合「パス > 名前」形式で全画面分を列挙する。
    例: "概要 / プロパティ / Blob service > BLOB 匿名アクセス | 設定 / 構成 > BLOB 匿名アクセスを許可する"
    """
    if not docs or not json_path:
        return "", "", ""

    import re
    # パスの末端2トークンを取り出す（$.properties.deleteRetentionPolicy.enabled → ["deleteRetentionPolicy", "enabled"]）
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", json_path)
    if not tokens:
        return "", "", ""
    last = tokens[-1].lower()
    parent_child = f"{tokens[-2].lower()}.{last}" if len(tokens) >= 2 else last

    # 汎用キーなら parent.child 形式を優先して検索
    if last in _GENERIC_KEYS:
        entry = docs.get(parent_child) or docs.get(last)
    else:
        entry = docs.get(last)
        if not entry:
            entry = next((v for k, v in docs.items() if k.startswith(last) or last.startswith(k)), None)
    if not entry:
        return "", "", ""

    portal_names = entry.get("portal_names", {})
    if portal_names:
        portal_name = " | ".join(f"{path} > {name}" for path, name in portal_names.items())
    else:
        portal_name = entry.get("portal_name", "")

    return (
        portal_name,
        entry.get("description", ""),
        entry.get("type_info", ""),
    )


# ============================================================
# JSON → 行リスト変換
# ============================================================

def _val(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _emit_kv(key, value, path, depth, is_last, rows):
    pad = INDENT * depth
    trail = "" if is_last else ","

    if isinstance(value, dict):
        if not value:
            rows.append((f'{pad}"{key}": {{}}{trail}', path, "", "", "", ""))
        else:
            rows.append((f'{pad}"{key}": {{', "", "", "", "", ""))
            _dict_inner(value, path, depth + 1, rows)
            rows.append((f'{pad}}}{trail}', "", "", "", "", ""))

    elif isinstance(value, list):
        if not value:
            rows.append((f'{pad}"{key}": []{trail}', path, "", "", "", ""))
        else:
            rows.append((f'{pad}"{key}": [', "", "", "", "", ""))
            _list_inner(value, path, depth + 1, rows)
            rows.append((f'{pad}]{trail}', "", "", "", "", ""))

    else:
        raw = json.dumps(value, ensure_ascii=False)
        rows.append((f'{pad}"{key}": {raw}{trail}', path, _val(value), "", "", ""))


def _emit_item(item, path, depth, is_last, rows):
    pad = INDENT * depth
    trail = "" if is_last else ","

    if isinstance(item, dict):
        if not item:
            rows.append((f"{pad}{{}}{trail}", path, "", "", "", ""))
        else:
            rows.append((f"{pad}{{", "", "", "", "", ""))
            _dict_inner(item, path, depth + 1, rows)
            rows.append((f"{pad}}}{trail}", "", "", "", "", ""))

    elif isinstance(item, list):
        if not item:
            rows.append((f"{pad}[]{trail}", path, "", "", "", ""))
        else:
            rows.append((f"{pad}[", "", "", "", "", ""))
            _list_inner(item, path, depth + 1, rows)
            rows.append((f"{pad}]{trail}", "", "", "", "", ""))

    else:
        raw = json.dumps(item, ensure_ascii=False)
        rows.append((f"{pad}{raw}{trail}", path, _val(item), "", "", ""))


def _dict_inner(d, path, depth, rows):
    items = list(d.items())
    for i, (key, value) in enumerate(items):
        _emit_kv(key, value, f"{path}.{key}", depth, i == len(items) - 1, rows)


def _list_inner(lst, path, depth, rows):
    for i, item in enumerate(lst):
        _emit_item(item, f"{path}[{i}]", depth, i == len(lst) - 1, rows)


def to_rows(obj) -> list[tuple]:
    rows = []
    if isinstance(obj, dict):
        rows.append(("{", "", "", "", "", ""))
        _dict_inner(obj, "$", 1, rows)
        rows.append(("}", "", "", "", "", ""))
    elif isinstance(obj, list):
        rows.append(("[", "", "", "", "", ""))
        _list_inner(obj, "$", 1, rows)
        rows.append(("]", "", "", "", "", ""))
    else:
        raw = json.dumps(obj, ensure_ascii=False)
        rows.append((raw, "$", _val(obj), "", "", ""))
    return rows


def apply_docs(rows: list[tuple], docs: dict) -> list[tuple]:
    """ポータルUI名候補・ドキュメント説明・使用可能値を各行に埋める"""
    result = []
    for raw_json, path, val, *_ in rows:
        portal_name, desc, type_info = lookup_doc(docs, path)
        result.append((raw_json, path, val, portal_name, desc, type_info))
    return result


# ============================================================
# メイン
# ============================================================

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    opts = sys.argv[1:]

    if not args:
        print("Usage: python3 json_to_csv.py <input.json> [output.csv] [--resource <key>]")
        print("  --resource: storage / network / nsg / keyvault / caenv / ca / agw / firewall")
        sys.exit(1)

    input_file = args[0]
    output_file = args[1] if len(args) > 1 else str(Path(input_file).with_suffix(".csv"))

    # --resource オプション
    resource_key = None
    for i, a in enumerate(opts):
        if a == "--resource" and i + 1 < len(opts):
            resource_key = opts[i + 1]

    with open(input_file, encoding="utf-8") as f:
        data = json.load(f)

    rows = to_rows(data)

    # ドキュメント説明を付与
    docs = load_docs_cache(resource_key) if resource_key else {}
    if resource_key and not docs:
        print(f"警告: キャッシュに '{resource_key}' のデータがありません。")
        print(f"  先に: python3 fetch_arm_docs.py {resource_key}")
    rows = apply_docs(rows, docs)

    # 出力列を決定
    has_docs = bool(docs)
    with open(output_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if has_docs:
            writer.writerow(["生のJSON", "JSONパス", "値", "ポータルUI名(候補)", "ドキュメント説明", "使用可能値"])
        else:
            writer.writerow(["生のJSON", "JSONパス", "値"])
            rows = [(r[0], r[1], r[2]) for r in rows]
        writer.writerows(rows)

    print(f"変換完了: {output_file}  ({len(rows)} 行)"
          + (f"  [ドキュメント説明: {resource_key}]" if has_docs else ""))


if __name__ == "__main__":
    main()

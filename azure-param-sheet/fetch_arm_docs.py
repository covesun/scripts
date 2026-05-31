#!/usr/bin/env python3
"""
Azure ARM ドキュメントからプロパティ定義を取得してキャッシュするスクリプト

使い方:
  python3 fetch_arm_docs.py                    # 全リソースを取得
  python3 fetch_arm_docs.py storage            # Storage のみ
  python3 fetch_arm_docs.py storage network    # 複数指定

キャッシュ先: arm_docs_cache.json
"""

import json
import sys
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

CACHE_FILE = Path(__file__).parent / "arm_docs_cache.json"

# リソース種別 → ドキュメントURL
RESOURCE_DOCS = {
    "storage":     "https://learn.microsoft.com/ja-jp/azure/templates/microsoft.storage/storageaccounts",
    "network":     "https://learn.microsoft.com/ja-jp/azure/templates/microsoft.network/virtualnetworks",
    "nsg":         "https://learn.microsoft.com/ja-jp/azure/templates/microsoft.network/networksecuritygroups",
    "keyvault":    "https://learn.microsoft.com/ja-jp/azure/templates/microsoft.keyvault/vaults",
    "caenv":       "https://learn.microsoft.com/ja-jp/azure/templates/microsoft.app/managedenvironments",
    "ca":          "https://learn.microsoft.com/ja-jp/azure/templates/microsoft.app/containerapps",
    "agw":         "https://learn.microsoft.com/ja-jp/azure/templates/microsoft.network/applicationgateways",
    "firewall":    "https://learn.microsoft.com/ja-jp/azure/templates/microsoft.network/azurefirewalls",
}

# ============================================================
# HTML パーサー
# ============================================================

class TableParser(HTMLParser):
    """HTML内の全テーブルを抽出する"""

    def __init__(self):
        super().__init__()
        self.tables = []          # [{header: [...], rows: [[cell, ...], ...]}, ...]
        self._cur_table = None
        self._cur_row = None
        self._in_cell = False
        self._cell_buf = []
        self._is_header = False

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._cur_table = {"header": [], "rows": []}
            self.tables.append(self._cur_table)
        elif tag == "tr" and self._cur_table is not None:
            self._cur_row = []
            self._is_header = False
        elif tag == "th" and self._cur_row is not None:
            self._in_cell = True
            self._is_header = True
            self._cell_buf = []
        elif tag == "td" and self._cur_row is not None:
            self._in_cell = True
            self._cell_buf = []
        elif tag == "br" and self._in_cell:
            self._cell_buf.append(" / ")

    def handle_endtag(self, tag):
        if tag == "table":
            self._cur_table = None
        elif tag == "tr" and self._cur_table is not None and self._cur_row is not None:
            if self._is_header:
                self._cur_table["header"] = self._cur_row
            else:
                if self._cur_row:
                    self._cur_table["rows"].append(self._cur_row)
            self._cur_row = None
        elif tag in ("td", "th"):
            if self._cur_row is not None:
                text = "".join(self._cell_buf).strip()
                # 連続スペース・改行を圧縮
                import re
                text = re.sub(r"\s+", " ", text)
                self._cur_row.append(text)
            self._in_cell = False

    def handle_data(self, data):
        if self._in_cell:
            self._cell_buf.append(data)

    def handle_entityref(self, name):
        if self._in_cell:
            mapping = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "nbsp": " "}
            self._cell_buf.append(mapping.get(name, ""))

    def handle_charref(self, name):
        if self._in_cell:
            try:
                c = chr(int(name[1:], 16) if name.startswith("x") else int(name))
                self._cell_buf.append(c)
            except Exception:
                pass


def fetch_html(url: str) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ja,en;q=0.9",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        charset = "utf-8"
        ct = resp.headers.get("Content-Type", "")
        for part in ct.split(";"):
            part = part.strip()
            if part.lower().startswith("charset="):
                charset = part.split("=", 1)[1].strip()
        return resp.read().decode(charset, errors="replace")


def extract_property_tables(html: str) -> dict:
    """
    HTML から「名前/説明/値」形式のプロパティテーブルを抽出する。
    戻り値: {property_name_lower: {name, description, type_info}}
    """
    parser = TableParser()
    parser.feed(html)

    props = {}
    for table in parser.tables:
        header = [h.strip() for h in table["header"]]
        # 「名前」列を含むテーブルのみ対象
        # ※ MSLearnの機械翻訳で「説明」→「形容」「価値」→「価値」になることがある
        header_lower = [h.lower() for h in header]
        if not any("名前" in h or "name" in h for h in header_lower):
            continue
        desc_kws = ["説明", "形容", "description", "desc"]
        if not any(any(kw in h for kw in desc_kws) for h in header_lower):
            continue

        # 列インデックスを特定
        name_idx = next((i for i, h in enumerate(header_lower)
                         if "名前" in h or "name" in h), 0)
        desc_idx = next((i for i, h in enumerate(header_lower)
                         if any(kw in h for kw in desc_kws)), 1)
        type_idx = next((i for i, h in enumerate(header_lower)
                         if any(kw in h for kw in ["値", "価値", "value", "type"])), 2)

        for row in table["rows"]:
            if len(row) <= name_idx:
                continue
            name_raw = row[name_idx].strip()
            desc = row[desc_idx].strip() if len(row) > desc_idx else ""
            type_info = row[type_idx].strip() if len(row) > type_idx else ""

            if not name_raw or not desc:
                continue

            import re
            # キー抽出: 英数字で始まる部分を ARM プロパティ名として使用
            # 例: "allowSharedKeyAccess (共有キーアクセスを許可)" → key="allowsharedkeyaccess"
            # 例: "BLOBの公開アクセスを許可"                      → key="blob..." (先頭英字部分)
            en_key_match = re.match(r"^([A-Za-z][A-Za-z0-9_\.]*)", name_raw)
            if en_key_match:
                key = en_key_match.group(1).lower()
            else:
                key = name_raw.lower()

            # 括弧内の日本語 → ポータルUIの表示名として抽出
            # 例: "allowSharedKeyAccess (共有キーアクセスを許可)" → "共有キーアクセスを許可"
            ja_match = re.search(r"[（(]([^\)）]+)[)）]", name_raw)
            portal_name = ja_match.group(1).strip() if ja_match else ""
            # 括弧がなく全体が日本語なら名前欄そのものをポータル名候補とする
            if not portal_name and not en_key_match:
                portal_name = name_raw

            props[key] = {
                "name": name_raw,
                "portal_name": portal_name,
                "description": desc,
                "type_info": type_info,
            }

    return props


def fetch_resource(resource_key: str) -> dict:
    url = RESOURCE_DOCS[resource_key]
    print(f"  フェッチ中: {url}")
    try:
        html = fetch_html(url)
        props = extract_property_tables(html)
        print(f"  → {len(props)} プロパティ取得")
        return props
    except Exception as e:
        print(f"  エラー: {e}")
        return {}


def main():
    targets = sys.argv[1:] if len(sys.argv) > 1 else list(RESOURCE_DOCS.keys())

    # 不明なキーを弾く
    unknown = [t for t in targets if t not in RESOURCE_DOCS]
    if unknown:
        print(f"不明なリソース: {unknown}")
        print(f"使用可能: {list(RESOURCE_DOCS.keys())}")
        sys.exit(1)

    # 既存キャッシュをロード
    cache = {}
    if CACHE_FILE.exists():
        with open(CACHE_FILE, encoding="utf-8") as f:
            cache = json.load(f)

    for key in targets:
        print(f"\n[{key}]")
        props = fetch_resource(key)
        if props:
            cache[key] = props
        time.sleep(1)  # サーバー負荷軽減

    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

    total = sum(len(v) for v in cache.values())
    print(f"\nキャッシュ保存: {CACHE_FILE}  (合計 {total} プロパティ)")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Azure Portal 画面キャプチャ自動化スクリプト（Mac専用）

Chrome DevTools Protocol (CDP) 経由で Edge に直接スクリーンショットを指示する。
スクリーン録画権限不要。Edge 自身がページをレンダリングして PNG を返す。

使い方:
  python3 capture_portal_ui.py --resource-type storage --arm-json mapping_templates/storage_profile_a.json

  ※ 初回は Edge をデバッグポート付きで再起動する（Azure へのログインは維持される）

デプロイ済みリソースがない場合:
  az deployment group create -g rg-mapping-test \\
    --template-file mapping_templates/storage_profile_a.json
"""

import argparse
import base64
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import websocket  # websocket-client

SCRIPT_DIR = Path(__file__).parent
CDP_PORT   = 9222

RESOURCE_TYPES = {
    "storage":  "Microsoft.Storage/storageAccounts",
    "network":  "Microsoft.Network/virtualNetworks",
    "nsg":      "Microsoft.Network/networkSecurityGroups",
    "keyvault": "Microsoft.KeyVault/vaults",
    "caenv":    "Microsoft.App/managedEnvironments",
    "ca":       "Microsoft.App/containerApps",
    "agw":      "Microsoft.Network/applicationGateways",
    "firewall": "Microsoft.Network/azureFirewalls",
}

PORTAL_PAGES = {
    "storage": [
        ("overview",          "概要 / プロパティ"),
        ("configuration",     "設定 / 構成"),
        ("networking",        "セキュリティとネットワーク / ネットワーク"),
        ("accountEncryption", "セキュリティとネットワーク / 暗号化"),
        ("RedundancyBlade",   "データ管理 / 冗長性"),
        ("dataProtection",    "データ管理 / データ保護"),
        ("staticWebsite",     "データ管理 / 静的な Web サイト"),
    ],
    "network": [
        ("overview",      "概要"),
        ("addressSpace",  "設定 / アドレス空間"),
        ("subnets",       "設定 / サブネット一覧"),
        ("dns",           "設定 / DNS サーバー"),
        ("dDoSProtection","設定 / DDoS 保護"),
        ("peerings",      "設定 / ピアリング"),
        ("serviceEndpoints", "設定 / サービス エンドポイント"),
    ],
    "nsg": [
        ("overview",              "概要"),
        ("inboundSecurityRules",  "設定 / 受信セキュリティ規則"),
        ("outboundSecurityRules", "設定 / 送信セキュリティ規則"),
        ("networkInterfaces",     "設定 / ネットワーク インターフェイス"),
        ("subnets",               "設定 / サブネット"),
    ],
    "keyvault": [
        ("overview",             "概要"),
        ("properties",           "設定 / プロパティ"),
        ("networking",           "設定 / ネットワーク"),
        ("access_configuration", "設定 / アクセス構成"),
    ],
    "caenv": [
        ("containerAppEnvironment", "概要"),
        ("Networking",              "設定 / ネットワーク"),
        ("LoggingOptions",          "設定 / ログ オプション"),
        ("msi",                     "設定 / ID"),
    ],
    "ca": [
        ("containerapp",      "概要"),
        ("containers",        "アプリケーション / コンテナー"),
        ("ingress",           "設定 / イングレス"),
        ("scale",             "設定 / スケーリング"),
        ("msi",               "設定 / ID"),
        ("revisionManagement","アプリケーション / リビジョン"),
        ("cors",              "設定 / CORS"),
        ("customDomains",     "設定 / カスタム ドメイン"),
    ],
    "agw": [
        ("overview",               "概要"),
        ("configuration",          "設定 / 構成"),
        ("backendPools",           "設定 / バックエンド プール"),
        ("backendSettings",        "設定 / バックエンド設定"),
        ("frontendIpConfigurations","設定 / フロントエンド IP 構成"),
        ("listeners",              "設定 / リスナー"),
        ("routingRulesV1",         "設定 / ルール"),
        ("probes",                 "設定 / 正常性プローブ"),
        ("sslSettings",            "設定 / SSL 設定"),
        ("webapplicationfirewall", "設定 / Web アプリケーション ファイアウォール"),
    ],
    "firewall": [
        ("overview",            "概要"),
        ("properties",         "設定 / プロパティ"),
        ("publicIpConfiguration", "設定 / パブリック IP 構成"),
    ],
}


# ============================================================
# az CLI
# ============================================================

def az(*args) -> str:
    r = subprocess.run(["az", *args], capture_output=True, text=True)
    return r.stdout.strip()


def get_tenant_id() -> str:
    return az("account", "show", "--query", "tenantId", "-o", "tsv")


def get_resource_id(resource_type_key: str, resource_name: str = None) -> str:
    rt = RESOURCE_TYPES[resource_type_key]
    if resource_name:
        return az("resource", "list", "--resource-type", rt,
                  "--query", f"[?name=='{resource_name}'].id | [0]", "-o", "tsv")
    return az("resource", "list", "--resource-type", rt,
              "--query", "[0].id", "-o", "tsv")


def extract_resource_name_from_template(arm_json_path: Path) -> str | None:
    try:
        with open(arm_json_path, encoding="utf-8") as f:
            data = json.load(f)
        params = data.get("parameters", {})
        for key in ("storageAccountName", "caeName", "caName", "kvName", "vnetName", "nsgName", "name"):
            if key in params and "defaultValue" in params[key]:
                return params[key]["defaultValue"]
    except Exception:
        pass
    return None


# ============================================================
# Edge CDP
# ============================================================

def restart_edge_with_cdp():
    """Edge をデバッグポート付きで再起動する"""
    print("Edge を CDP モードで再起動します...")
    subprocess.run(["osascript", "-e", 'quit app "Microsoft Edge"'],
                   capture_output=True)
    time.sleep(2)
    subprocess.Popen([
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        f"--remote-debugging-port={CDP_PORT}",
        "--remote-allow-origins=*",
        "--no-first-run",
    ])
    time.sleep(3)
    print("Edge 起動完了")


def get_cdp_ws_url(timeout: int = 15) -> str:
    """CDP の WebSocket URL を取得する"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://localhost:{CDP_PORT}/json", timeout=2
            ) as resp:
                tabs = json.loads(resp.read())
                for tab in tabs:
                    if tab.get("type") == "page":
                        return tab["webSocketDebuggerUrl"]
        except Exception:
            time.sleep(1)
    raise RuntimeError("Edge CDP に接続できませんでした")


class CDPClient:
    def __init__(self, ws_url: str):
        self.ws = websocket.create_connection(ws_url, timeout=30)
        self._id = 0

    def send(self, method: str, params: dict = None) -> dict:
        self._id += 1
        msg = {"id": self._id, "method": method, "params": params or {}}
        self.ws.send(json.dumps(msg))
        # 対応する id のレスポンスを待つ
        while True:
            raw = self.ws.recv()
            data = json.loads(raw)
            if data.get("id") == self._id:
                return data

    def navigate(self, url: str, wait_sec: int):
        self.send("Page.enable")
        self.send("Page.navigate", {"url": url})
        time.sleep(wait_sec)  # ページ読み込み待機

    def screenshot(self, path: Path):
        result = self.send("Page.captureScreenshot", {
            "format": "png",
            "captureBeyondViewport": False,
        })
        data = result.get("result", {}).get("data", "")
        if not data:
            raise RuntimeError("スクリーンショット取得失敗")
        path.write_bytes(base64.b64decode(data))

    def close(self):
        self.ws.close()


# ============================================================
# メイン
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Azure Portal を CDP 経由でキャプチャする"
    )
    parser.add_argument("--resource-type", required=True,
                        choices=list(PORTAL_PAGES.keys()))
    parser.add_argument("--arm-json", required=True, metavar="FILE")
    parser.add_argument("--resource-id", metavar="ID")
    parser.add_argument("--wait", type=int, default=10,
                        help="ページ読み込み待機秒数 (default: 10)")
    parser.add_argument("--pages", nargs="+")
    parser.add_argument("--no-restart", action="store_true",
                        help="Edge の再起動をスキップ（既に CDP モードで起動中の場合）")
    args = parser.parse_args()

    arm_json_path = Path(args.arm_json).expanduser()
    if not arm_json_path.exists():
        print(f"ARM JSON が見つかりません: {arm_json_path}")
        sys.exit(1)

    # リソース ID の解決
    if args.resource_id:
        resource_id = args.resource_id
    else:
        resource_name = extract_resource_name_from_template(arm_json_path)
        print(f"リソース名: {resource_name}")
        resource_id = get_resource_id(args.resource_type, resource_name)

    if not resource_id:
        print("リソースが見つかりません。--resource-id で指定してください。")
        sys.exit(1)
    print(f"対象リソース: {resource_id}")

    tenant_id = get_tenant_id()
    if not tenant_id:
        print("az login してください")
        sys.exit(1)

    # キャプチャ先
    profile_name = arm_json_path.stem
    output_dir = SCRIPT_DIR / "screenshots" / profile_name
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "capture.log"

    def log(msg: str):
        print(msg)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(msg + "\n")

    # Edge を CDP モードで起動
    if not args.no_restart:
        restart_edge_with_cdp()
        print("\n★ Edge が再起動されました。Azure Portal にログインしてください。")
        print("  ログイン完了後 Enter を押してください...")
        input()

    # CDP 接続
    print("CDP に接続中...")
    ws_url = get_cdp_ws_url()
    client = CDPClient(ws_url)
    print("接続完了\n")

    # ページをキャプチャ
    all_pages = PORTAL_PAGES[args.resource_type]
    pages = [(pid, desc) for pid, desc in all_pages
             if not args.pages or pid in args.pages]

    log(f"{len(pages)} ページをキャプチャします → {output_dir}\n")

    for page_id, desc in pages:
        url = f"https://portal.azure.com/#@{tenant_id}/resource{resource_id}/{page_id}"
        ss_path = output_dir / f"{page_id}.png"
        log(f"  [{page_id}] {desc}")
        try:
            client.navigate(url, wait_sec=args.wait)
            client.screenshot(ss_path)
            size = ss_path.stat().st_size // 1024
            log(f"    → 保存完了: {ss_path.name} ({size} KB)")
        except Exception as e:
            log(f"    エラー: {e}")

    client.close()
    log(f"\n完了: {len(pages)} 枚 → {output_dir}")

    # ARM JSON を自動保存
    arm_samples_dir = SCRIPT_DIR / "arm_samples"
    arm_samples_dir.mkdir(exist_ok=True)
    arm_out = arm_samples_dir / f"{args.resource_type}.json"
    log(f"\nARM JSON を保存中: {arm_out}")
    try:
        result = subprocess.run(
            ["az", "resource", "show", "--ids", resource_id, "-o", "json"],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            arm_out.write_text(result.stdout, encoding="utf-8")
            log(f"  → 保存完了: {arm_out.name} ({arm_out.stat().st_size // 1024} KB)")
        else:
            log(f"  警告: ARM JSON 取得失敗 ({result.stderr.strip()[:80]})")
    except Exception as e:
        log(f"  警告: ARM JSON 取得エラー: {e}")

    print(f"\nマッピングを実行するには:")
    print(f"  python3 import_screenshots.py {output_dir} --arm-json {arm_json_path}")


if __name__ == "__main__":
    main()

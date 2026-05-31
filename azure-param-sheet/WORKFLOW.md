# マッピング作業ワークフロー — 設計思想と手順

このドキュメントは `arm_docs_cache.json` を新規作成・拡張する際の
設計思想・落とし穴・正しい手順を記録したもの。

---

## なぜこのツールが必要か

Azure Portal の画面項目名（UI名）と ARM プロパティ名は全く異なる。

```
ARM: properties.minimumTlsVersion = "TLS1_2"
画面: 設定 / 構成 > TLS の最小バージョン = "バージョン 1.2"
```

パラメータシートは「画面に何と表示されているか」で設計されることが多いため、
ARM で取得した値を画面UI名に紐づける対応表が必要になる。

---

## マッピングの基本方針

### 値照合方式（必須）

ARMプロパティとポータルUI名の対応は **実際の値が一致することを確認してから** 登録する。

**NG（推測マッピング）：**
- ARM プロパティ名から UI名を推測して登録
- ドキュメントの説明文から推測して登録

**OK（値照合マッピング）：**
- 実リソースの ARM 値（例: `TLS1_2`）が画面に「バージョン 1.2」と表示されていることを確認してから登録

理由: ARM プロパティ名と UI名は対応していないことが多く、
推測マッピングは誤登録の原因になる。

---

## フィンガープリント方式

同じリソースに `enabled: true` が複数あったり、`days: 7` が複数あったりすると、
どの ARM プロパティがどの UI 項目に対応するか判別できない。

**解決策：** 各プロパティに意図的にユニークな値を設定してから画面を取る。

```json
{
  "deleteRetentionPolicy.days": 13,
  "containerDeleteRetentionPolicy.days": 17,
  "shareDeleteRetentionPolicy.days": 19
}
```

画面に「13日」が見えたら `deleteRetentionPolicy.days`、
「17日」なら `containerDeleteRetentionPolicy.days` と一意に確定できる。

**boolean プロパティの場合：** 典型的なデフォルトと逆の値を設定する。

```json
{
  "allowBlobPublicAccess": true,      // 通常は false
  "allowSharedKeyAccess": false,       // 通常は true
  "supportsHttpsTrafficOnly": false,   // 通常は true
  "defaultToOAuthAuthentication": true // 通常は false
}
```

これにより「有効」「無効」が入り乱れても、ARM値との照合で一意に確定できる。

---

## 相互排他プロパティへの対応

一方を設定するともう一方が画面に現れなくなる組み合わせがある。

**例（Storage Account）：**
- `publicNetworkAccess = Enabled` + `defaultAction = Allow` → ファイアウォールルール欄が非表示
- `publicNetworkAccess = Enabled` + `defaultAction = Deny` → ファイアウォールルール欄が表示

**対策：** 複数プロファイルのテンプレートを用意する。

```
storage_profile_a.json → Allow（全ネットワーク許可）状態をキャプチャ
storage_profile_b.json → Deny（特定ネットワーク）状態をキャプチャ
```

---

## 一方向トグル（作成時のみ設定可能なプロパティ）への対応

一度有効にすると無効に戻せないプロパティがある。

**例：**
- `requireInfrastructureEncryption` → 作成時のみ設定可
- `enablePurgeProtection` → 一度有効にすると無効化不可（ARM テンプレートに `false` を書くとエラー）
- `isHnsEnabled` → 作成後変更不可

**対策：** ARMテンプレート作成時に事前確認し、エラーになるプロパティは省略またはnullにする。

---

## ポータルURLパスの発見方法

`capture_portal_ui.py` で使うページIDは、ポータルの URL ハッシュの末尾部分。

**重要：** ポータルのURL末尾は直感的でないことが多い。

| 直感的な名前 | 実際のURLパス |
|---|---|
| `encryption` | `accountEncryption` |
| `redundancy` | `RedundancyBlade` |
| `dataprotection` | `dataProtection` |

**正しいURLパスの調べ方：** CDP で overview に遷移した後、JavaScriptでリンク一覧を取得する。

```python
# CDPクライアント接続後
result = send("Runtime.evaluate", {"expression": """
    Array.from(document.querySelectorAll('a[href]'))
        .filter(a => a.href.includes('/storageAccounts/'))
        .map(a => { const m = a.href.match(/storageAccounts\\/[^/]+\\/(.+)/); return m ? m[1] : null; })
        .filter(Boolean)
        .filter((v,i,arr) => arr.indexOf(v) === i)
        .sort()
        .join('\\n')
"""})
```

---

## キャプチャに CDP を使う理由

当初は macOS の `screencapture` コマンドを使っていたが以下の問題が発生した：

- ブラウザが前面にないと壁紙がキャプチャされる
- `screencapture -l <windowID>` は macOS 14+ でセキュリティ制限により動作しない
- クラムシェルモードでモニター判定が狂う
- 別アプリ（VSCode等）が前面にいると即座に乗っ取られる

**CDP (Chrome DevTools Protocol) の利点：**
- ブラウザが裏にいても、最小化されていても取れる
- ピクセル単位でブラウザの描画結果を取得
- 環境依存なし（モニター構成・フォーカス状態無関係）
- Edge は `--remote-debugging-port=9222 --remote-allow-origins=*` で起動すれば使える

---

## arm_key の設計（複合キー）

キャッシュのキーは ARMプロパティの末端キー名を小文字化したものだが、
`enabled`・`days`・`name` など汎用名は複数プロパティで衝突する。

```
deleteRetentionPolicy.enabled        → キー: "deleteretentionpolicy.enabled"
containerDeleteRetentionPolicy.enabled → キー: "containerdeleteretentionpolicy.enabled"
staticWebsite.enabled                → キー: "staticwebsite.enabled"
```

末端キーが汎用名の場合は **親プロパティと組み合わせる**。

```python
GENERIC_KEYS = {"enabled","disabled","days","name","id","type",
                "value","key","tier","action","choice","state"}

def arm_to_key(arm_prop: str) -> str:
    parts = [p for p in re.split(r"[.\[\*\]]+", arm_prop) if p]
    last = parts[-1].lower()
    if last in GENERIC_KEYS and len(parts) >= 2:
        return f"{parts[-2].lower()}.{last}"
    return last
```

---

## 正しいキャプチャ作業手順

**削除前にカバレッジ確認を必ず行うこと。**

```
1. ARMテンプレート作成
   - 各プロパティにユニーク値（フィンガープリント）を設定
   - 相互排他プロパティはプロファイルA/Bに分けて設計

2. デプロイ
   az deployment group create -g rg-mapping-test --template-file mapping_templates/xxx.json

3. ポータルURLパスを確認（初回のみ）
   CDPでoverview遷移後にJSでリンク一覧を取得

4. capture_portal_ui.py で全ページキャプチャ
   - ARM JSON も同時に自動保存される（arm_samples/に出力）
   - 設定ページだけでなく「編集パネル」が必要な場合は
     CDPクリックでパネルを開いてからキャプチャ

5. カバレッジ確認（削除前に必ず実施）
   python3 json_to_csv.py arm_samples/xxx.json /tmp/check.csv --resource xxx

6. 不足ページがあれば追加キャプチャ（--pages オプション）
   python3 capture_portal_ui.py --resource-type xxx --pages targetPage --no-restart

7. リソース削除
   az group delete --name rg-mapping-test --yes --no-wait

8. キャッシュ更新
   スクリーンショットと ARM 値を照合してマッピングをキャッシュに登録

9. カバレッジ再確認
   すべてのリソースタイプで80%以上を目標とする
```

---

## カバレッジ計算のルール

以下はカバレッジ計算から除外する（ポータルに対応するUI項目がないため）：

| 除外パターン | 理由 |
|---|---|
| `*.type` | ARM リソースタイプ文字列（ポータル非表示） |
| `*.etag` | バージョン管理タグ（ポータル非表示） |
| `$schema`, `contentVersion`, `apiVersion`, `dependsOn` | ARMテンプレートの構造フィールド |
| `provisioningState`, `resourceGuid` 等のシステムフィールド | 自動生成・非表示 |
| サブリソースのID（`backendAddressPools[0].id` 等） | ARM内部ID（ポータルには名前で表示） |

**注意：** 自動生成値（`fqdn`, `privateIPAddress`, `daprConfiguration.version` 等）は
ポータルに「参照情報」として表示されるため、カバレッジに含める。

---

## コスト管理

| リソース | 時間単価 | 備考 |
|---|---|---|
| Storage Account | ~$0.01/時 | ほぼ無視できる |
| VNet / NSG | 無料 | ネットワークリソース自体は無料 |
| Key Vault | ~$0.01/時 | ほぼ無視できる |
| Container Apps Environment | ~$0.01/時 | Consumption プランなら微量 |
| Application Gateway WAF v2 | ~$0.36/時 × キャパシティ | 注意が必要 |
| **Azure Firewall** | **~$1.25/時** | 最も高い・最小時間で削除 |

AGW と Firewall は同一テンプレートに同梱して同時デプロイ・同時削除すること。
**キャプチャ完了後すぐに削除リクエストを送ること（`--no-wait` でバックグラウンド削除）。**

---

## 注意事項

### `.portal_auth.json` を絶対に公開しないこと

`capture_portal_ui.py` の一部実装で Azure ポータルのセッション Cookie が
`.portal_auth.json` に保存される場合がある。
`.gitignore` に追加済みだが、`git add` 前に必ず確認すること。

### ARMテンプレートのサブスクリプションIDを除外すること

`arm_samples/` は `.gitignore` で除外済み。
実環境の ARM JSON（`az resource show` 出力）にはサブスクリプションID・
テナントIDが含まれるため、そのまま commit しないこと。

### フィンガープリントテンプレートは本番に使わないこと

`mapping_templates/` のテンプレートは意図的に非推奨値（TLS1_0 等）を
設定しているため、本番環境へのデプロイには使用しないこと。

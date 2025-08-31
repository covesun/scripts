const FOLDER_ID  = '1upaCuAzxg1MXZUarKGU7F1AOi4V1d-Yx';

function scanAndInsertNewImages() {
  const sh = SpreadsheetApp.getActiveSheet(); // アクティブシート取得

  // 既に処理済みのIDセットを作る（B列）
  const last = sh.getLastRow();
  const known = new Set();
  if (last >= 2) { // ヘッダ行を除外
    const ids = sh.getRange(2, 2, last - 1, 1).getValues().flat().filter(Boolean);
    ids.forEach(id => known.add(id));
  }

  // 対象フォルダ内の画像を新しい順に
  const folder = DriveApp.getFolderById(FOLDER_ID);
  const files = folder.getFiles();
  const newRows = [];

  while (files.hasNext()) {
    const f = files.next();
    if (!/^image\//.test(f.getMimeType())) continue; // 画像だけ
    const id = f.getId();
    if (known.has(id)) continue;

    const url = `https://drive.google.com/uc?export=view&id=${id}`;
    newRows.push([
      `=IMAGE("${url}")`,  // A: セル内画像
      id,                  // B: ファイルID
      f.getName(),         // C: ファイル名
      new Date(f.getDateCreated()) // D: 受信時刻
    ]);
  }

  // 追加（古い順で下に並べたい場合は逆順にしてから追加）
  if (newRows.length) {
    newRows.sort((a,b)=> a[3]-b[3]); // 受信時刻で昇順
    sh.getRange(sh.getLastRow() + 1, 1, newRows.length, newRows[0].length).setValues(newRows);
  }
}

// 初回だけ実行してから、トリガーを作成
function setupTriggerEveryMinute() {
  ScriptApp.newTrigger('scanAndInsertNewImages')
    .timeBased().everyMinutes(1).create();
}

// TODO
// セル内画像をコピー→値を貼り付けで永続化
// 永続化した画像をGoogle Driveから削除
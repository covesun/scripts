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

  // 追加行はファイル名で昇順に並べ、行の高さを120pxに設定
  if (newRows.length) {
    newRows.sort((a, b) => a[2].localeCompare(b[2]));
    const startRow = sh.getLastRow() + 1;
    sh.getRange(startRow, 1, newRows.length, newRows[0].length).setValues(newRows);
    sh.setRowHeights(startRow, newRows.length, 120);
    // 既存行を含めてファイル名で並び替え
    sh.getRange(2, 1, sh.getLastRow() - 1, sh.getLastColumn())
      .sort({ column: 3, ascending: true });
  }
}

// 行削除時に対応するファイルをGoogle Driveから削除
function onChange(e) {
  if (e.changeType !== 'REMOVE_ROW') return;

  const sh = e.source.getActiveSheet();
  const last = sh.getLastRow();
  const ids = last >= 2
    ? sh.getRange(2, 2, last - 1, 1).getValues().flat().filter(Boolean)
    : [];
  const existing = new Set(ids);
  const folder = DriveApp.getFolderById(FOLDER_ID);
  const files = folder.getFiles();
  while (files.hasNext()) {
    const f = files.next();
    if (!existing.has(f.getId())) {
      f.setTrashed(true);
    }
  }
}

// 初回だけ実行してトリガーを作成
function setupTriggers() {
  ScriptApp.newTrigger('scanAndInsertNewImages')
    .timeBased()
    .everyMinutes(1)
    .create();
  ScriptApp.newTrigger('onChange')
    .forSpreadsheet(SpreadsheetApp.getActive())
    .onChange()
    .create();
}

// TODO
// セル内画像をコピー→値を貼り付けで永続化
// 永続化した画像をGoogle Driveから削除

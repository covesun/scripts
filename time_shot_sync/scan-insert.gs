const FOLDER_ID  = '1EF4eFuW6MkOfTbQ4jCn0TG9BqjVSopFk';

const COL_IMAGE = 1;
const COL_ID    = 2;
const COL_NAME  = 3;
const COL_TIME  = 4;
const HEADER_ROW = 1;
const ROW_HEIGHT_PX = 120;

function scanAndInsertNewImages() {
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    const sh = SpreadsheetApp.getActiveSheet(); // アクティブシート取得

    // 既に処理済みのIDセットを作る（B列）
    const last = sh.getLastRow();
    const known = new Set();
    if (last >= HEADER_ROW + 1) { // ヘッダ行を除外
      const ids = sh.getRange(HEADER_ROW + 1, COL_ID, last - HEADER_ROW, 1).getValues().flat().filter(Boolean);
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
      const row = [];
      row[COL_IMAGE - 1] = `=IMAGE("${url}")`;
      row[COL_ID - 1] = id;
      row[COL_NAME - 1] = f.getName();
      row[COL_TIME - 1] = new Date(f.getDateCreated());
      newRows.push(row);
    }

    // 追加行はファイル名で昇順に並べ、行の高さを120pxに設定
    if (newRows.length) {
      newRows.sort((a, b) => a[COL_NAME - 1].localeCompare(b[COL_NAME - 1]));
      const startRow = sh.getLastRow() + 1;
      sh.getRange(startRow, COL_IMAGE, newRows.length, newRows[0].length).setValues(newRows);
      sh.setRowHeights(startRow, newRows.length, ROW_HEIGHT_PX);
      // 既存行を含めてファイル名で並び替え
      sh.getRange(HEADER_ROW + 1, COL_IMAGE, sh.getLastRow() - HEADER_ROW, sh.getLastColumn())
        .sort({ column: COL_NAME, ascending: true });
    }
  } finally {
    lock.releaseLock();
  }
}

// 行削除時に対応するファイルをGoogle Driveから削除
// installable trigger (not simple onChange)
function handleSheetChange(e) {
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    if (e.changeType !== 'REMOVE_ROW') return;

    const sh = e.source.getActiveSheet();
    const last = sh.getLastRow();
    const ids = last >= HEADER_ROW + 1
      ? sh.getRange(HEADER_ROW + 1, COL_ID, last - HEADER_ROW, 1).getValues().flat().filter(Boolean)
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
  } finally {
    lock.releaseLock();
  }
}

// 初回だけ実行してトリガーを作成
function setupTriggers() {
  ScriptApp.newTrigger('scanAndInsertNewImages')
    .timeBased()
    .everyMinutes(1)
    .create();
  ScriptApp.newTrigger('handleSheetChange')
    .forSpreadsheet(SpreadsheetApp.getActive())
    .onChange()
    .create();
}

// TODO
// セル内画像をコピー→値を貼り付けで永続化
// 永続化した画像をGoogle Driveから削除

#!/bin/bash
set -u

# ==== PATH を明示 ====
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# ==== 設定 ====
GAS_WEBHOOK_URL="https://script.google.com/macros/s/xxxxxxxxxxxxxxxx/exec"
SHARED_SECRET="書き換えて"
VIDEO_TITLE="MyMovie"
LOG="/tmp/fcp_ocr_uploader.log"
SAVE_DEBUG_IMAGES=true

# 4K 左上タイムコード帯（ピクセル）
CROP_W_PX=1020
CROP_H_PX=154
CROP_X_PX=0
CROP_Y_PX=0
THRESHOLD_PCT=60   # 50〜70で調整可

timestamp(){ date "+%Y-%m-%d %H:%M:%S"; }
lower(){ printf "%s" "$1" | tr '[:upper:]' '[:lower:]'; }

# ==== 主要コマンドの存在ログ ====
echo "$(timestamp) ==== START ====" >> "$LOG"
for cmd in magick tesseract sips base64; do
  which "$cmd" >/dev/null 2>&1 \
    && echo "$(timestamp) tool ok: $cmd -> $(which "$cmd")" >> "$LOG" \
    || echo "$(timestamp) tool NG: $cmd not found" >> "$LOG"
done

# デバッグ画像保存ディレクトリ
DBG_DIR="/tmp/fcp_ocr_samples"
[ "$SAVE_DEBUG_IMAGES" = true ] && mkdir -p "$DBG_DIR"

# ==== OCR（原寸で処理） ====
ocr_tc () {
  local in="$1"
  local tmpdir; tmpdir="$(mktemp -d)"
  local cw ch cx cy bin raw tc

  cw="$CROP_W_PX"
  ch="$CROP_H_PX"
  cx="$CROP_X_PX"
  cy="$CROP_Y_PX"

  bin="$tmpdir/bin.png"

  if ! magick "$in" -crop "${cw}x${ch}+${cx}+${cy}" +repage -colorspace Gray \
        -contrast-stretch 1%x1% -threshold ${THRESHOLD_PCT}% "$bin" >>"$LOG" 2>&1; then
    echo "$(timestamp) magick convert failed" >> "$LOG"
    [ "$SAVE_DEBUG_IMAGES" = true ] && cp "$in" "$DBG_DIR/$(date +%s)_orig.png"
    rm -rf "$tmpdir"; return 1
  fi
  [ "$SAVE_DEBUG_IMAGES" = true ] && cp "$bin" "$DBG_DIR/$(date +%s)_bin.png"

  raw=$(tesseract "$bin" stdout -l eng --psm 7 \
        -c tessedit_char_whitelist=0123456789: 2>>"$LOG" | tr -cd '0-9:.')

  # 正規化
  tc=""
  if [[ "$raw" =~ ^([0-9]{2}:[0-9]{2}:[0-9]{2})([:.][0-9]{1,3})?$ ]]; then
    tc="${BASH_REMATCH[1]}${BASH_REMATCH[2]}"
  elif [[ "$raw" =~ ^([0-9]{6})([0-9]{1,3})?$ ]]; then
    local hh="${raw:0:2}" mm="${raw:2:2}" ss="${raw:4:2}" ff="${raw:6}"
    tc="$hh:$mm:$ss"; [ -n "$ff" ] && tc="$tc:$ff"
  fi

  rm -rf "$tmpdir"
  [ -z "$tc" ] && return 2
  printf "%s" "$tc"
}

# ==== GAS送信（縮小コピーを送る） ====
send_to_gas () {
  local file="$1" mime="$2" tc="$3"
  local fname; fname="$(basename "$file")"
  local b64;  b64=$(base64 < "$file" | tr -d '\n')

  local body code
  body=$( /usr/bin/curl -s -w "\n%{http_code}" -X POST -H "Content-Type: application/json" \
    -d "{\"secret\":\"$SHARED_SECRET\",\"action\":\"added\",
         \"fileName\":\"$fname\",\"mimeType\":\"$mime\",\"dataBase64\":\"$b64\",
         \"video\":\"$VIDEO_TITLE\",\"tc\":\"$tc\"}" \
    "$GAS_WEBHOOK_URL" )
  code="${body##*$'\n'}"; body="${body%$'\n'*}"
  echo "$(timestamp) [POST] file=$fname http=$code resp=$body" >> "$LOG"
}

echo "$(timestamp) batch $# files" >> "$LOG"

for FILE in "$@"; do
  if [ ! -f "$FILE" ]; then
    echo "$(timestamp) skip(not file): $FILE" >> "$LOG"; continue
  fi
  ext="${FILE##*.}"; ext="$(lower "$ext")"
  case "$ext" in png|jpg|jpeg) ;; *)
    echo "$(timestamp) skip(ext): $FILE" >> "$LOG"; continue ;;
  esac
  echo "$(timestamp) proc: $FILE" >> "$LOG"

  # 1) まず原寸でOCR
  tc=""
  if tc="$(ocr_tc "$FILE")"; then
    echo "$(timestamp) OCR OK: $tc" >> "$LOG"
    # リネーム（※ベースの指定どおり：ファイル名を TC{tc} だけにする）
    dir="$(dirname "$FILE")"; base="$(basename "$FILE")"; name="${base%.*}"
    if [[ "$name" != *"_TC"* ]]; then
      safe_tc="${tc//:/-}"
      new="${dir}/TC${safe_tc}.${ext}"
      mv "$FILE" "$new" && FILE="$new"
      echo "$(timestamp) rename -> $(basename "$FILE")" >> "$LOG"
    fi
  else
    echo "$(timestamp) OCR FAIL (check $DBG_DIR)" >> "$LOG"
  fi

  # 2) 元ファイルをそのまま縮小（上書き）
  before_bytes=$(stat -f%z "$FILE" 2>/dev/null || echo 0)
  before_wh=$(magick identify -format "%wx%h" "$FILE" 2>/dev/null || echo "")
  if [ "$ext" = "jpg" ] || [ "$ext" = "jpeg" ]; then
    /usr/bin/sips --resampleWidth 400 "$FILE" >> "$LOG" 2>&1
    /usr/bin/sips --setProperty formatOptions 60 "$FILE" >> "$LOG" 2>&1
    mime="image/jpeg"
  else
    /usr/bin/sips --resampleWidth 400 "$FILE" >> "$LOG" 2>&1
    mime="image/png"
  fi
  after_bytes=$(stat -f%z "$FILE" 2>/dev/null || echo 0)
  after_wh=$(magick identify -format "%wx%h" "$FILE" 2>/dev/null || echo "")
  echo "$(timestamp) resized in-place: $before_wh($before_bytes B) -> $after_wh($after_bytes B)" >> "$LOG"

  # 3) GASへ送信（tcが空でも送る）
  #send_to_gas "$tmp_small" "$mime" "$tc"
  rm -f "$tmp_small"
done

echo "$(timestamp) ==== END ====" >> "$LOG"

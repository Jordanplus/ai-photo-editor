# ai-photo-editor

讓 Claude Code 判讀 DJI 空拍機的 DNG，把算好的修圖參數寫進 DNG 複本，再直接操作 Lightroom Classic 套用，並取回 Lightroom 實際算出的成品來驗證。

第一次實作用的是一批北海道空拍照（DJI Mini 5 Pro，30 張 DNG）：
- 修了夕陽地面太暗、白平衡偏灰、變焦構圖跑掉等問題。
- 整套流程整理成 3 個 Claude Code skill。

> 照片本身（DNG、匯出的 JPG）和工作輸出不在這個 repo 裡：檔案太大，而且含 GPS 拍攝位置。見 `.gitignore`。

## 為什麼需要這套流程

DJI 的 DNG 直接丟進 Lightroom，常常和遙控器上看到的不一樣：

| 現象 | 原因 | 處理方式 |
|---|---|---|
| 同一組照片亮度差 1.5 EV | DJI 對部分照片刻意少曝光保護亮部，再在 DNG 寫入開檔提亮值（BaselineExposure） | 判讀時把這個值加回去，才不會誤判成曝光不一致 |
| 夕陽在 Lightroom 變得灰灰的 | 自動白平衡把暖色「校正」掉了；遙控器上的 JPEG 另外加了暖色風格 | 改成自訂白平衡，色溫比拍攝值高 1000～1500K |
| 視角比構圖時寬 | 用 2 倍、3 倍變焦拍照時，RAW 仍存全幅畫面，EXIF 也不記錄變焦 | 拿相機內嵌 JPEG 和 RAW 比對，找出變焦倍率，再用裁切還原構圖 |
| 夕陽逆光地面一片黑 | 天空和地面差 4～5 EV | 從地平線往下加線性漸層遮罩補光 |

## 3 個 skill

放在 `.claude/skills/`，在這個專案目錄裡開 Claude Code 就會自動載入。直接用口語下指令即可，例如「幫我判讀這批 DNG」「幫我修圖」「讓 Lightroom 讀取」。

### `raw-assess`：判讀 RAW

```bash
python3 .claude/skills/raw-assess/scripts/assess.py <DNG 檔或資料夾> --out <輸出資料夾>
```

- 量測每張的實際曝光（已加回 BaselineExposure）、全黑區域比例、過曝比例、地平線位置與天空地面亮度差。
- 推算拍攝色溫。
- 偵測變焦拍攝：掃描 1.0～4.0 倍，用兩種影像比對方法交叉確認。
- 輸出 `report.md`（給人看）、`report.json`（給下一步的程式）、`contact.jpg`（索引圖）。
- 速度：30 張 50MP DNG 約 15 秒。

### `lightroom-dng-settings`：把修圖參數寫進 DNG 複本

```bash
S=.claude/skills/lightroom-dng-settings/scripts
python3 $S/suggest_params.py <report.json> --out params/round1.json             # 依判讀結果產生第一版參數
python3 $S/write_lr_settings.py params/round1.json <原檔資料夾> --dst lr_import --keyword claude-round1
```

- Lightroom 的每個調整都是 DNG 內部 XMP 的 `crs:` 欄位。寫進**複本**後，在 Lightroom 裡每個滑桿都還能再調；原檔不動。
- 支援：
  - 基本面板、HSL、色彩分級、自訂白平衡
  - 裁切
  - 地面漸層補光（線性漸層遮罩）
- 寫完會逐張讀回核對。exiftool 寫錯時只出警告、結束代碼仍是 0，所以腳本把警告當作失敗。
- 參數檔格式：`.claude/skills/lightroom-dng-settings/references/params-format.md`
- 欄位名稱與容易寫錯的地方：`references/crs-reference.md`
- 這次的實際參數：`params/round1.json` ～ `round6.json`

### `lightroom-classic-control`：操作 Lightroom 並取回成品

```bash
L=.claude/skills/lightroom-classic-control/scripts
python3 $L/lr_catalog.py backup --out work/catalog-backups      # 先備份目錄
python3 $L/lr_ui.py library                                      # 切到圖庫模組
python3 $L/lr_ui.py status --out work/ui                         # 截圖，親眼確認選取張數
python3 $L/lr_ui.py read-metadata --expect 29 --out work/ui      # 中繼資料 > 從檔案讀取中繼資料
python3 $L/lr_catalog.py history 'DJI_2026%' --since "2026-11-08 22:00"   # 確認每張都讀到了
python3 $L/lr_ui.py build-previews --expect 29                   # 重建預覽
python3 $L/lr_catalog.py previews 'DJI_2026%' --out work/lr_round6         # 取出 Lightroom 成品（轉成 sRGB）
```

- `lr_ui.py` 用 AppleScript 點 Lightroom 選單，**會真的改動使用者的 Lightroom**。
- `lr_catalog.py` 只讀不寫：先複製目錄再查詢，Lightroom 開著也能用。
- **「從檔案讀取中繼資料」會用檔案設定取代所選照片的調整**。選錯範圍，其他照片的修圖會被重設，所以腳本有以下保護：
  - 不在圖庫模組就拒絕執行（在「編輯相片」模組只會套用到目前那一張）。
  - 選取張數和預期不符就拒絕執行。
  - 每次執行前都要重新截圖確認。

## 一輪完整流程

1. `raw-assess` 判讀，依場景分組（例如夕陽、陰天湖泊、紅葉林）。
2. `suggest_params.py` 產生有客觀依據的部分：曝光、裁切、漸層補光、白平衡、降噪。風格參數依場景經驗值補上。
3. `write_lr_settings.py` 寫入 `lr_import/` 的複本。
4. 在 Lightroom 匯入（第一次），或讓 Lightroom 從檔案讀取中繼資料（之後每一輪）。
5. 取回 Lightroom 實際成品，和上一輪並排比對，決定下一輪怎麼調。

## 做不到的事

- **AI 遮罩**（選取天空、主體）和 **AI 降噪**：要由 Lightroom 自己計算，無法預先寫進檔案，需要在 Lightroom 手動操作。
- **Luminar**：調整存在 Luminar 自己的目錄裡，沒有可寫入的檔案格式。

## 其他目錄

| 路徑 | 內容 |
|---|---|
| `tools/` | 第一版的工具：判讀、近似預覽渲染、寫入設定、色溫換算。之後都已整理進 skill |
| `params/` | 每一輪的修圖參數、地平線位置、量測數據 |
| `bridge/ClaudeBridge.lrplugin/` | 曾嘗試的 Lightroom 外掛，**沒有安裝也不建議安裝**：背景輪詢會一直留在 Lightroom 裡。改用 `lr_ui.py` 操作選單 |
| `.claude/skills/*/evals/` | skill 的測試案例：題目與評分標準 |

## 需要的環境

- macOS、Lightroom Classic（在 15.2 上測試）
- `exiftool`、ImageMagick（`magick`）、`tesseract`
- Python 3，套件 `rawpy numpy scipy opencv-python pillow`
- 用 `lr_ui.py` 時，終端機需要「輔助使用」權限
- 照片資料夾一律從參數傳入，程式裡沒有寫死的路徑：
  - `tools/analyze.py <DNG 資料夾>`
  - skill 測試題目用環境變數 `$DJI_DNG_DIR`，跑測試前先 `export DJI_DNG_DIR=<照片資料夾>`

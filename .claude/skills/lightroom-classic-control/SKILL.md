---
name: lightroom-classic-control
description: 在 macOS 上直接操作 Lightroom Classic，並取回它實際算出的成品：用 AppleScript 點選單（從檔案讀取中繼資料、建立標準預覽、切換圖庫模組、叫出匯入對話框）、截圖並讀取狀態列確認選取範圍，以及唯讀查詢目錄（目前調整值、調整記錄、預覽快取轉 sRGB、前後對照圖、目錄備份）。使用者說「你直接操作 Lightroom」「幫我讀取」「讓 Lightroom 套用」「看看 Lightroom 開出來長怎樣」「確認設定有沒有生效」「匯入 Lightroom」，或寫完 DNG 設定之後要驗證時都用這個 skill。
---

# 操作 Lightroom Classic 與取回成品

兩支腳本，分工明確：

- `scripts/lr_catalog.py`：**唯讀**。先把目錄和預覽資料庫複製一份再查，不會鎖住或修改 Lightroom 的檔案，Lightroom 開著也能用。
- `scripts/lr_ui.py`：**會操作使用者正在用的 Lightroom**，所有動作都會真的生效。

## 最重要的風險：「從檔案讀取中繼資料」

這個動作會用檔案裡的設定**取代**目錄裡的調整，而且套用到**所有選取的照片**。如果當時選到的是「所有相片」，使用者另外幾百張照片的修圖會被重設成預設值（那些照片的檔案裡沒有設定）。所以：

1. 先 `lr_catalog.py backup --out <資料夾>` 備份目錄。
2. 用 `lr_ui.py status` 截圖，**用 Read 打開 `lr_bottom.png` 親眼看狀態列**，例如「上次讀入　30 張相片 / 選取了 30 張相片」。來源要是這批照片，例如「上次讀入」、指定資料夾或收藏集；選取數量也要等於預期。
3. `lr_ui.py read-metadata --expect 30`：腳本會再用 OCR 核對一次；不在圖庫模組或數量不符就拒絕執行。OCR 讀不到時，只有在你已經親眼確認截圖之後，才能加 `--visually-confirmed`。
4. 執行後用 `lr_catalog.py history <樣式> --since "<執行前的時間>"` 確認「N/N 張讀取過中繼資料」。曾經發生使用者在「編輯相片」模組執行，結果只套用到目前那一張，所以要用記錄驗證，不要假設成功。

使用者明確要求你操作時才用 `lr_ui.py`；每一次讀取都要重新截圖確認，不能沿用之前的確認。

## 典型流程（寫完 DNG 設定之後）

```bash
L=<本 skill>/scripts
python3 $L/lr_catalog.py backup --out work/catalog-backups
# 第一次：還沒匯入
python3 $L/lr_ui.py import-dialog <複本資料夾>     # 請使用者選「新增」再按「匯入」（對話框是自訂介面，點不到）
# 之後每一輪：重寫複本後
python3 $L/lr_ui.py library
python3 $L/lr_ui.py status --out work/ui           # 然後 Read work/ui/lr_bottom.png
python3 $L/lr_ui.py read-metadata --expect 30 --out work/ui
python3 $L/lr_catalog.py history 'DJI_2026%' --since "2026-11-08 18:30"
python3 $L/lr_catalog.py settings 'DJI_2026%'      # 核對 Lightroom 實際套用的值
python3 $L/lr_ui.py build-previews --expect 30     # 讀取後大預覽會被清掉，要重建
python3 $L/lr_catalog.py previews 'DJI_2026%' --out work/lr_round2
python3 $L/lr_catalog.py compare work/lr_round1 work/lr_round2 --out work/compare_r1_r2.jpg
```

然後用 Read 看對照圖，再決定下一輪怎麼調。預覽要幾秒到幾十秒才會產生；`previews` 回報缺幾張時，稍等再取。

## 已知狀況與處理

| 狀況 | 原因 | 處理 |
|---|---|---|
| AppleScript 卡住、逾時 | Lightroom 正在啟動，或有對話框擋住（例如 macOS「想要取用其他 App 的資料」權限詢問） | `lr_ui.py screen` 看畫面；**系統權限對話框由使用者決定，不要代按** |
| `status` 顯示視窗、模組空白 | Lightroom 開了很多沒有名稱的小視窗，`front window` 抓到其中一個（腳本已改找標題含「Lightroom Classic」的主視窗） | 仍空白時用 `screen` 看全螢幕 |
| 只有一張讀到新設定 | 在「編輯相片」模組執行 | 先 `lr_ui.py library` 再讀取 |
| 預覽顏色偏淡 | 預覽快取是 Adobe RGB（Lightroom 15.2 的預覽檔內嵌 Adobe RGB (1998) 描述檔，已實測） | `previews` 指令已自動轉成 sRGB；不支援色彩管理的檢視器直接看原檔會偏淡 |
| `previews` 缺檔 | 讀取設定後大預覽被清除 | `build-previews` 後再取 |
| 目錄張數變少 | 使用者在 Lightroom 移除或刪除了照片 | 照實回報，不要自行重建或刪檔；問使用者 |

## 做不到 / 不要做的事

- **不要安裝 Lightroom 外掛來自動化**：開機自動執行、在背景一直輪詢資料夾的外掛，會被安全規則判定為「植入常駐程式」而擋下；而且它會一直留在使用者的 Lightroom 裡。真的需要時，先徵求使用者同意，並改成只在使用者點選單時才執行的版本。
- 匯入對話框、AI 遮罩、AI 降噪這些 Lightroom 自訂介面點不到，請使用者操作。
- 不要直接寫入 `.lrcat`。所有修改都透過 DNG 檔案和 Lightroom 自己的「讀取中繼資料」完成。

## 回報

先講結果（例如「30/30 張已套用」），附上 Lightroom 實際成品的對照圖路徑，再說明過程中確認了什麼（截圖上的選取數量、記錄筆數）。看到使用者自己做的變更（例如匯出、刪除照片）照實說明，不要假設是錯誤。

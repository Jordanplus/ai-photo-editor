---
name: lightroom-dng-settings
description: 把修圖參數寫成 Lightroom Classic 的調整設定（XMP 的 crs 欄位），放進 DNG 複本，讓使用者匯入或「從檔案讀取中繼資料」後就帶著調整：曝光、反差、HSL、色彩分級、自訂白平衡、裁切、地平線漸層補光（線性漸層遮罩）。使用者說「幫我修圖」「調一下這批 DNG」「幫我設定 Lightroom 參數」「把地面補亮」「裁回原本構圖」「夕陽暖一點」，或任何要讓 Lightroom 照片帶著預先算好的調整時都用這個 skill；即使使用者只說「修圖」沒提到 XMP 或 crs 也一樣。Luminar 沒有可寫入的設定格式，這個 skill 只適用 Lightroom（Classic / Camera Raw）。
---

# 把調整寫進 DNG（Lightroom 會讀）

核心想法：Lightroom 的調整就是 DNG 內部 XMP 裡的 `crs:` 欄位。直接把算好的值寫進**複本**，使用者在 Lightroom 裡看到的就是有調整、而且每個滑桿都還能再改的照片。原檔永遠不動。

## 流程

1. **先判讀**：用 `raw-assess` 產生 `report.json`（曝光、變焦、地平線、色溫）。沒有判讀就下參數，等於用猜的。
2. **產生骨架**：
   ```bash
   python3 <本 skill>/scripts/suggest_params.py <report.json> --out params/round1.json
   ```
   會自動處理有客觀依據的部分：曝光補償、變焦裁切、地面漸層補光、夕陽白平衡、暗部降噪。每項都附 `_why` 說明依據。
3. **補上風格**：看 `contact.jpg` 依場景分組，在 `groups` 填風格參數（見下方「場景經驗值」）。這是審美判斷，向使用者說明你的取向。
4. **寫入複本**：
   ```bash
   python3 <本 skill>/scripts/write_lr_settings.py params/round1.json <原檔資料夾> --dst <複本資料夾> --keyword claude-round1
   ```
   - 每張都從原檔重新複製再寫，寫完讀回核對；任何一張 `FAIL` 都要先查清楚，不要回報成功。
   - `--dst` 與原檔資料夾相同時腳本會拒絕執行。
   - `--only 0045,0057` 只處理指定照片；`--dry` 只印出要寫的值。
   - 複本約與原檔同大小（50MP DNG 一張約 65MB），先確認磁碟空間。
5. **讓 Lightroom 讀取並驗證**：用 `lightroom-classic-control`（匯入、讀取中繼資料、取回 Lightroom 實際成品）。**一定要看 Lightroom 算出來的成品**，不要只看自己的近似渲染。這次就發現自己的預覽工具在「陰影」一項比 Lightroom 弱很多，夕陽地面的判斷因此差了一大截。
6. **迭代**：每輪存成新的 `params/roundN.json`（不要覆蓋舊的），寫入時換 `--keyword`，方便使用者在 Lightroom 篩選、也方便回溯。

參數檔格式見 `references/params-format.md`；欄位名稱、數值格式和容易寫錯的地方見 `references/crs-reference.md`。**第一次用、或要寫新欄位之前一定要讀 crs-reference.md**，裡面的錯誤都是實際踩過的，而且都是「只出警告、看起來成功」的類型。

## 場景經驗值（DJI Mini 5 Pro DNG、Lightroom 15.2 實測）

起點而已，實際仍要看 Lightroom 成品再調。完整範例見 `assets/example-hokkaido-drone.json`。

| 場景 | 重點 | 參考值 |
|---|---|---|
| 白天、晴到多雲 | 相機 JPEG 已經不錯，別過頭 | 曝光 +0.15、亮部 -40、陰影 +20、紋理 +15、去朦朧 +8、鮮豔度 +18、橘/水藍飽和 +10～15 |
| 夕陽、逆光 | 白平衡要主動加暖；地面靠漸層補，不要只拉全域陰影（天空會變平） | 色溫 = 拍攝值 +1000～1500K、**色調 +3**（+8 天空會偏粉紫）、曝光 +0.6～1.0、亮部 -70、全域陰影 +60、漸層補 +0.35～1.0 EV、降噪 30～35 |
| 斜光紅葉林 | 天空易被壓成灰白 | 亮部 -60、藍色明度 -25／飽和 +20、綠色色相 -15（往黃綠）、漸層補 +0.2～0.7 EV |
| 陰天山景、湖泊 | 偏灰平 | 反差 +12～15、亮部 -45～-60、去朦朧 +15～20、白色 +12～15、黑色 -12、中間調微暖（色相 40、飽和 8） |

## 這個方法做不到的事（要請使用者在 Lightroom 手動做）

- **AI 遮罩**（選取天空、主體、人物）：遮罩影像要 Lightroom 自己算，XMP 無法預先寫好。
- **AI 降噪**（增強 > 降噪）：會產生新的 DNG，不是設定值。暗部拉很多的照片（raw-assess 暗部 > 10%）建議使用者做。
- **Luminar**（Neo / AI / 4）：調整存在 Luminar 自己的目錄，沒有可寫入的檔案格式。可以建議工具（Relight AI 依景深補前景光、Sky AI 換天空、Noiseless AI 降噪），但無法代為設定。

## 回報

先講結論：寫了幾張、每組做了什麼、哪些是推算（例如白平衡、地平線位置）需要在 Lightroom 確認。若有 FAIL，照實附上錯誤訊息。

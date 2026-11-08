---
name: raw-assess
description: 判讀一批 RAW／DNG 照片（特別是 DJI 空拍機的 DNG）：量出實際曝光（含 BaselineExposure）、過曝與暗部比例、地平線位置與天空地面亮度差、拍攝色溫，並自動偵測哪些照片是用變焦拍的（RAW 存全幅、構圖其實只有中央一塊）。只要使用者想「看看這批照片有什麼問題」「幫我判讀 / 分析 RAW」「哪些照片需要修」「為什麼 Lightroom 開出來跟遙控器上看到的不一樣」，或要開始修一批 DNG/RAW 之前，都應該先用這個 skill，即使沒有明說「判讀」。
---

# RAW 判讀

修圖前先用數據看清楚每張照片的狀況，後面的參數才有依據。這個 skill 的產出（`report.json`）會直接餵給 `lightroom-dng-settings` 產生第一版參數。

## 執行

```bash
python3 <本 skill 目錄>/scripts/assess.py <DNG 檔或資料夾>... --out <輸出資料夾> [--save-lin]
```

- 30 張 50MP DNG 約 15 秒（6 個平行程序）。
- `--save-lin` 會另存 1600px 線性影像，只有要自己做近似預覽渲染時才需要。
- 輸出：`report.md`（給人看）、`report.json`（給程式用）、`contact.jpg`（相機內嵌 JPEG 索引圖）、`previews/`。
- 需要 `exiftool`、ImageMagick，以及 Python 套件 `rawpy numpy scipy opencv-python pillow`。

跑完先看 `contact.jpg` 了解拍了什麼（依 GPS／時間把照片分組，例如「夕陽」「陰天湖泊」），再讀 `report.md`。

## 各欄位怎麼解讀

| 欄位 | 意思 | 怎麼用 |
|---|---|---|
| 亮度(EV) | 相對 18% 中灰的幾何平均，**已加上 DNG 的 BaselineExposure** | 一般風景 -0.5～-1 正常；< -1.5 偏暗 |
| 暗部% | 幾乎全黑（線性亮度 < 0.5%）的面積比例 | > 10% 代表提亮後雜訊會明顯，要配降噪 |
| RAW 過曝% | 感光元件真的飽和的比例 | DJI 很少過曝；有過曝的部分怎麼調都救不回 |
| 天空-地面 | 地平線上下中位亮度差（EV） | ≥ 3 EV 建議漸層補光；`horizon_y` 是漸層起點 |
| 變焦 | 相機 JPEG 只占 RAW 畫面的幾分之一 | > 1 代表要裁切還原構圖 |
| 拍攝色溫/色調 | 依 DNG 規格從 ColorMatrix 與 AsShotNeutral 換算 | 與 Lightroom「原始設定」差 < 50K、色調差 < 1.5（已實測） |
| jpeg_warmer_than_raw | 相機 JPEG 比 RAW 暖多少（粗略指標） | > 0.25 時 Lightroom 開檔會顯得灰，要主動提高色溫 |

## 為什麼要注意這些（實際踩過的坑）

- **BaselineExposure**：DJI 會對部分照片刻意少曝光保護亮部，再在 DNG 寫入 +0.8～+1.5 EV 的開檔提亮值。不加回去，同一組照片會看起來亮度差 1.5 EV，誤判成曝光不一致。這批照片中，帶 BaselineExposure 的看起來都是「單張」拍攝模式，「智能拍照」模式為 0（測試時觀察到，尚未從 EXIF 欄位直接確認）；同一點位混用兩種模式時，批次同步調整後要逐張檢查。
- **變焦拍攝**：DJI Mini 5 Pro 用 2 倍、3 倍變焦拍照時，RAW 仍存整個感光元件的畫面，EXIF 的 `DigitalZoomRatio` 還是 1，只有內嵌 JPEG 是裁切後的構圖。使用者在 Lightroom 會看到「視角怎麼變寬了」。偵測方式是把內嵌預覽和 RAW 畫面比對：
  - 兩種方法交叉確認：SIFT 特徵點比對，加上中央裁切相關係數（**掃描 1.0～4.0 倍，每 0.1 倍一格**）。
  - 曾經只比較「全幅 vs 2 倍」就下結論，結果 3 倍變焦的照片被誤判成 2 倍。兩種方法不一致時，`notes` 會提示，要打開 `previews/` 目視確認。
- **白平衡**：DJI 的自動白平衡會把夕陽的暖色「校正」掉。遙控器上看到的 JPEG 另外加了暖色風格，所以很橘；Lightroom 開 RAW 用拍攝白平衡，就顯得灰。`jpeg_warmer_than_raw` 是用來抓這種情況的粗略指標，結果仍要配合目視判斷。
- **DJI 自己記錄的 `WhiteBalanceCCT`**（XMP drone-dji）和 Lightroom 換算的值差約 350K，不要拿它當 Lightroom 的色溫基準；用本腳本的 `as_shot_temp`。
- **俯拍**（雲台俯角 < -35°）通常沒有天空，地平線數據沒有意義，`has_horizon` 會是 false。

## 向使用者回報

用白話分組回報，不要直接貼欄位名稱。先講結論（哪幾組有什麼問題、建議怎麼修），再附表格。例如：

> 夕陽組（0045–0057）地面比天空暗 4～5 EV，建議加漸層補光；0046、0056 是 2 倍、0057 是 3 倍變焦拍的，Lightroom 開出來視角會比你構圖時寬，要裁回去。

數據背後是推算（例如色溫換算、變焦偵測），說明時要標明是「量測」還是「推算」，不確定的地方請使用者目視確認。

## 接下來

- 要產生 Lightroom 參數：用 `lightroom-dng-settings` 的 `suggest_params.py` 讀這份 `report.json`。
- 要在 Lightroom 裡套用和驗證：用 `lightroom-classic-control`。

# 參數檔格式（params.json）

`write_lr_settings.py` 讀的 JSON。合併順序：`default` → 所屬 `groups[*].base` → 符合的 `overrides`（後者覆蓋前者）。

```json
{
  "default": { "Contrast": 5, "Highlights": -30 },
  "groups": {
    "B_sunset": {
      "files": ["0045", "0046", "0047"],
      "base": { "Temperature": 6500, "Tint": 3, "Exposure": 0.7, "Shadows": 60 }
    }
  },
  "overrides": {
    "0057": {
      "Crop": [0.333333, 0.333333, 0.666667, 0.666667],
      "Exposure": 0.8,
      "GroundGradient": { "horizon": 0.562, "exposure": 0.9, "shadows": 20 },
      "_why": "3 倍變焦；地面比天空暗 5 EV"
    }
  },
  "group_detail": { "B_sunset": { "LuminanceSmoothing": 30 } },
  "detail_overrides": { "0057": { "LuminanceSmoothing": 35 } }
}
```

## 照片代號

`files` 與 `overrides` 的鍵可以是完整檔名（不含副檔名）、底線分隔的任一段，或檔名結尾。
DJI 檔名 `DJI_20261025154228_0057_D` 用 `0057` 即可。注意代號不要短到同時對到多張。

## 可用參數（值的單位同 Lightroom 滑桿）

| 參數 | 對應 Lightroom | 範圍 |
|---|---|---|
| Exposure | 曝光 | -5～+5（EV，兩位小數） |
| Contrast, Highlights, Shadows, Whites, Blacks | 基本面板 | -100～+100 |
| Texture, Clarity, Dehaze | 紋理、清晰度、去朦朧 | -100～+100 |
| Vibrance, Saturation | 鮮豔度、飽和度 | -100～+100 |
| Temperature, Tint | 白平衡（設了 Temperature 就變自訂白平衡） | K 值；-150～+150 |
| Sat/Lum/Hue + Red, Orange, Yellow, Green, Aqua, Blue, Purple, Magenta | HSL | -100～+100 |
| ColorGrade{Highlight,Midtone,Shadow,Global}{Hue,Sat} | 色彩分級 | 色相 0～359、飽和 0～100 |
| Vignette | 裁切後暗角 | -100～+100 |
| LuminanceNR, ColorNR, Sharpness | 細節 | 0～100 |
| Crop | 裁切 `[左, 上, 右, 下]`，0～1，以未裁切畫面為準 | |
| GroundGradient | 地面漸層補光，見下 | |
| Detail | 直接指定其他 crs 標籤，例如 `{"SharpenEdgeMasking": 40}` | |
| `_` 開頭 | 說明欄位，寫入時忽略 | |

## GroundGradient

```json
{ "horizon": 0.49, "exposure": 0.6, "shadows": 20, "above": 0.02, "below": 0.10,
  "highlights": 0, "temperature": 0, "dehaze": 0, "name": "地面補光" }
```

- `horizon`：地平線的 y（0 = 頂端、1 = 底部，未裁切畫面座標），用 raw-assess 的 `horizon_y`。
- 漸層從 `horizon - above` 開始（效果 0），到 `horizon + below` 達到 100%，以下全滿。
- `exposure` 單位是 EV，`shadows` 等是滑桿值；寫入時自動換算成 XMP 的正規化值。
- **有裁切時，`above`／`below` 要除以變焦倍率**：座標以未裁切畫面為準，預設的 0.10 在 3 倍裁切後會占可見畫面 30% 以上，地面只補到一半。`suggest_params.py` 已自動處理。
- 在 Lightroom 的遮罩面板裡會顯示為「地面補光（Claude）」，使用者可以直接拖曳調整。

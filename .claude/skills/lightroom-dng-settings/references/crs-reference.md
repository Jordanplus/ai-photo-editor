# Lightroom 設定（crs:）速查與已驗證事實

環境：Lightroom Classic 15.2（Camera Raw 18.2）、exiftool 13.31。版本不同時，先用
`lightroom-classic-control` 的 `lr_catalog.py settings` 讀一張現有照片，核對 ProcessVersion 與欄位名稱。

## 寫入位置

- **DNG：設定寫在檔案內部的 XMP。** Lightroom 不讀 DNG 旁邊的 `.xmp` sidecar。
- ARW、NEF、CR3 等專有格式才用 sidecar `.xmp`（同檔名）。
- 匯入時 Lightroom 會讀取檔案裡的設定。已經在目錄裡的照片，要用「中繼資料 > 從檔案讀取中繼資料」才會更新，**這個動作會用檔案內容取代目錄裡的調整**。

## 必要欄位

| 欄位 | 值 | 說明 |
|---|---|---|
| crs:HasSettings | True | 沒有它 Lightroom 會忽略整組設定 |
| crs:ProcessVersion | 15.4 | 目前的處理版本 |
| crs:Version | 18.2 | 對應 Camera Raw 版本；DJI 原檔帶的是 7.0，要覆寫 |
| crs:WhiteBalance | As Shot / Custom | 設色溫時必須是 Custom |

## 容易寫錯的地方（全部實際踩過）

1. **色溫的 exiftool 標籤名是 `ColorTemperature`**，寫進 XMP 後才是 `crs:Temperature`。寫成 `-XMP-crs:Temperature=` 只會出警告、**結束代碼仍是 0**，看起來像成功。
2. **有文字選項的欄位要加 `#` 寫數值**，例如 `-XMP-crs:PostCropVignetteStyle#=1`。不加的話 exiftool 只警告「not in PrintConv」，該欄位不寫入。
3. 因為 1、2 都是「只警告」，**寫入工具必須把任何警告當失敗**；只有 DJI 原檔本來就有的 `Maker notes could not be parsed` 可以忽略。
4. **色彩分級的亮部、暗部用舊名 `SplitToning*`**（`SplitToningHighlightHue`、`SplitToningHighlightSaturation`），中間調和全域才是 `ColorGradeMidtoneHue`、`ColorGradeGlobalSat`。
5. **數值格式**照 Lightroom 自己寫出的樣子：雙向滑桿帶正負號（`+20`、`-15`），單向數值與色相角度是純數字（`Sharpness` 40、`SplitToningHighlightHue` 35），曝光兩位小數（`+0.35`）。
6. **裁切座標**：`CropLeft/Top/Right/Bottom` 是 0～1，以未裁切、已做鏡頭校正的畫面為準，`HasCrop=True`。
7. **每次都從原檔重新複製再寫**，不要在上一輪的複本上疊加，否則上一輪的遮罩等結構會殘留。

## 漸層遮罩（MaskGroupBasedCorrections）

exiftool 可以用結構語法寫入：

```
-XMP-crs:MaskGroupBasedCorrections=[{What=Correction,CorrectionActive=true,CorrectionAmount=1,
  CorrectionName=地面補光（Claude）,CorrectionSyncID=<32位hex>,
  LocalExposure2012=0.152500,LocalShadows2012=0.200000,
  CorrectionMasks=[{What=Mask/Gradient,MaskActive=true,MaskBlendMode=0,MaskInverted=false,
    MaskName=地面補光,MaskSyncID=<32位hex>,MaskValue=1,MaskVersion=1,
    ZeroX=0.5,ZeroY=0.466,FullX=0.5,FullY=0.586}]}]
```

- **區域調整值是正規化的**：`LocalExposure2012` = EV ÷ 4，其他（陰影、亮部、去朦朧……）= 滑桿值 ÷ 100。量級已用 Lightroom 實際成品驗證：補 +0.6 EV 的地面，亮度變化與預期一致；如果誤用 1:1，地面會過曝。
- `ZeroX/ZeroY` 是效果 0% 的點，`FullX/FullY` 是 100% 的點，座標同裁切（未裁切畫面 0～1）。上下方向的漸層讓兩個 X 都是 0.5。
- 不需要 `MaskID`（exiftool 也不支援寫入），Lightroom 讀入後會自己補。
- AI 遮罩（選取天空、主體）需要 Lightroom 自己算遮罩影像，**無法用 XMP 預先寫好**，只能請使用者在 Lightroom 裡點選。

## 從使用者的目錄學格式

使用者過去做過的調整都存在目錄的 `Adobe_imageDevelopSettings.text`（Lua table 格式），欄位名稱和 XMP 一一對應。不確定某個欄位怎麼寫時，先找一張用過該功能的照片照抄格式，比憑記憶寫可靠。

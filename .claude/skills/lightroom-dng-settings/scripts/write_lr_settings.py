#!/usr/bin/env python3
"""把參數檔（JSON）轉成 Lightroom Classic 的 crs: 設定，寫進 DNG「複本」。

用法：
  python3 write_lr_settings.py <params.json> <來源 DNG 資料夾或檔案>... --dst <輸出資料夾>
                               [--only 0045,0057] [--keyword claude-round1] [--dry]

- 原檔不動：先複製到 --dst，再用 exiftool 把 XMP-crs 寫進複本。Lightroom 匯入（或「從檔案讀取中繼資料」）時會讀取。
- DNG 的設定必須寫在檔案「內部」；Lightroom 不讀 DNG 旁邊的 .xmp。ARW/NEF 等其他 RAW 才用 sidecar（本腳本不處理）。
- 寫完會讀回關鍵欄位核對，任何警告（例如標籤名稱寫錯）都判定失敗。

參數檔格式見 ../references/params-format.md。
"""
import argparse, glob, json, os, re, shutil, subprocess, sys, uuid

# 參數名 → crs 標籤（exiftool 的名稱）
MAP = {
    "Exposure": "Exposure2012", "Contrast": "Contrast2012", "Highlights": "Highlights2012",
    "Shadows": "Shadows2012", "Whites": "Whites2012", "Blacks": "Blacks2012",
    "Texture": "Texture", "Clarity": "Clarity2012", "Dehaze": "Dehaze",
    "Vibrance": "Vibrance", "Saturation": "Saturation",
    # Lightroom 色彩分級：亮部 / 暗部沿用舊的 SplitToning 名稱，中間調 / 全域才是 ColorGrade
    "ColorGradeHighlightHue": "SplitToningHighlightHue", "ColorGradeHighlightSat": "SplitToningHighlightSaturation",
    "ColorGradeShadowHue": "SplitToningShadowHue", "ColorGradeShadowSat": "SplitToningShadowSaturation",
    "ColorGradeMidtoneHue": "ColorGradeMidtoneHue", "ColorGradeMidtoneSat": "ColorGradeMidtoneSat",
    "ColorGradeGlobalHue": "ColorGradeGlobalHue", "ColorGradeGlobalSat": "ColorGradeGlobalSat",
    "Vignette": "PostCropVignetteAmount",
    "LuminanceNR": "LuminanceSmoothing", "ColorNR": "ColorNoiseReduction", "Sharpness": "Sharpness",
}
for band in ["Red", "Orange", "Yellow", "Green", "Aqua", "Blue", "Purple", "Magenta"]:
    MAP["Sat" + band] = "SaturationAdjustment" + band
    MAP["Lum" + band] = "LuminanceAdjustment" + band
    MAP["Hue" + band] = "HueAdjustment" + band

# 所有照片共用。Version/ProcessVersion 對應 Lightroom Classic 15.x（Camera Raw 18.x）；
# 不同版本可從目錄裡現有照片的設定確認（見 lightroom-classic-control 的 lr_catalog.py settings）。
COMMON = {
    "Version": "18.2", "ProcessVersion": "15.4", "HasSettings": "True", "WhiteBalance": "As Shot",
    "LensProfileEnable": 1, "AutoLateralCA": 1,
    "Sharpness": 40, "SharpenRadius": "+1.0", "SharpenDetail": 25, "SharpenEdgeMasking": 25,
    "LuminanceSmoothing": 8, "ColorNoiseReduction": 25, "ColorNoiseReductionDetail": 50,
}

# Lightroom 寫出的格式：雙向滑桿帶正負號（+20 / -15），單向數值與色相角度是純數字
UNSIGNED = ("LensProfileEnable", "AutoLateralCA", "Sharpness", "SharpenDetail", "SharpenEdgeMasking",
            "LuminanceSmoothing", "ColorNoiseReduction", "PostCropVignetteStyle", "PostCropVignetteMidpoint",
            "PostCropVignetteFeather", "SplitToningHighlightHue", "SplitToningShadowHue", "ColorGradeMidtoneHue",
            "ColorGradeGlobalHue", "SplitToningHighlightSaturation", "SplitToningShadowSaturation",
            "ColorGradeMidtoneSat", "ColorGradeGlobalSat", "ColorTemperature")


# 這些欄位在 exiftool 有文字選項轉換（PrintConv），數值要用 TAG#=值 寫入
NUMERIC_RAW = {"PostCropVignetteStyle"}
# 來源檔本身就有、與寫入無關的警告（DJI DNG 的 MakerNotes 格式 exiftool 解析不了）
BENIGN = ("Maker notes could not be parsed", "Possibly incorrect maker notes offsets")


def fmt(tag, v):
    if tag == "Exposure2012":
        return f"{float(v):+.2f}"
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        v = int(round(v))
        if tag.startswith(UNSIGNED):
            return str(v)
        return f"+{v}" if v > 0 else str(v)
    return str(v)


def key_matches(key, stem):
    """參數檔用短代號指照片：完整檔名、底線分段之一、或檔名結尾都算（例如 0045 對到 DJI_..._0045_D）。"""
    return key == stem or key in stem.split("_") or stem.endswith(key)


def merged_params(stem, params):
    group = next((g for g, v in params.get("groups", {}).items()
                  if any(key_matches(k, stem) for k in v.get("files", []))), None)
    p = dict(params.get("default", {}))
    if group:
        p.update(params["groups"][group].get("base", {}))
    for k, v in params.get("overrides", {}).items():
        if key_matches(k, stem):
            p.update(v)
    return group or "default", p


def to_crs(p, stem):
    crs = dict(COMMON)
    for k, v in p.items():
        if k.startswith("_") or k == "GroundGradient":  # _why 等說明欄位；漸層另外處理
            continue
        if k in MAP:
            crs[MAP[k]] = v
        elif k == "Crop":  # [left, top, right, bottom]，正規化到未裁切畫面
            l, t, r, b = v
            crs.update(HasCrop="True", CropLeft=f"{l:.6f}", CropTop=f"{t:.6f}", CropRight=f"{r:.6f}",
                       CropBottom=f"{b:.6f}", CropAngle="0")
        elif k == "Temperature":
            # 注意：exiftool 的標籤名是 ColorTemperature（寫進 XMP 才是 crs:Temperature）。
            # 寫成 Temperature 只會出警告、結束代碼仍是 0，曾因此誤報成功。
            crs.update(WhiteBalance="Custom", ColorTemperature=int(v))
        elif k == "Tint":
            crs["Tint"] = v
        elif k == "Detail":  # 直接指定其他 crs 標籤，例如 {"SharpenEdgeMasking": 40}
            crs.update(v)
        else:
            raise KeyError(f"{stem}：未知參數 {k}")
    if "PostCropVignetteAmount" in crs:
        crs.update(PostCropVignetteStyle=1, PostCropVignetteMidpoint=50, PostCropVignetteFeather=50,
                   PostCropVignetteRoundness=0)
    return crs


def gradient_arg(gg):
    """線性漸層遮罩（地面補光）。座標是未裁切畫面的 0~1；ZeroY 處效果 0，FullY 以下 100%。

    區域調整在 XMP 內是正規化值：曝光 = EV / 4，其他滑桿 = 值 / 100（已用 Lightroom 實際成品驗證量級）。
    """
    h = gg["horizon"]
    zero_y = max(0.0, h - gg.get("above", 0.02))
    full_y = min(1.0, h + gg.get("below", 0.10))
    name = gg.get("name", "地面補光")
    mask = (f"{{What=Mask/Gradient,MaskActive=true,MaskBlendMode=0,MaskInverted=false,"
            f"MaskName={name},MaskSyncID={uuid.uuid4().hex.upper()},MaskValue=1,MaskVersion=1,"
            f"ZeroX=0.5,ZeroY={zero_y:.6f},FullX=0.5,FullY={full_y:.6f}}}")
    local = f"LocalExposure2012={gg.get('exposure', 0) / 4:.6f},LocalShadows2012={gg.get('shadows', 0) / 100:.6f}"
    for k, scale in (("highlights", "LocalHighlights2012"), ("temperature", "LocalTemperature"),
                     ("dehaze", "LocalDehaze"), ("clarity", "LocalClarity2012")):
        if k in gg:
            local += f",{scale}={gg[k] / 100:.6f}"
    corr = (f"{{What=Correction,CorrectionActive=true,CorrectionAmount=1,CorrectionName={name}（Claude）,"
            f"CorrectionSyncID={uuid.uuid4().hex.upper()},{local},CorrectionMasks=[{mask}]}}")
    return f"-XMP-crs:MaskGroupBasedCorrections=[{corr}]"


def verify(path, crs, has_grad):
    tags = ["HasSettings", "Exposure2012", "WhiteBalance", "ColorTemperature", "HasCrop", "CropLeft"]
    out = subprocess.run(["exiftool", "-j", "-q", "-q", *[f"-XMP-crs:{t}" for t in tags],
                          "-XMP-crs:MaskGroupBasedCorrExposure2012", path], capture_output=True, text=True).stdout
    got = json.loads(out)[0]
    problems = []
    if str(got.get("HasSettings")).lower() != "true":
        problems.append("HasSettings 不是 True")
    if "Exposure2012" in crs and abs(float(got.get("Exposure2012", 99)) - float(crs["Exposure2012"])) > 0.006:
        problems.append(f"Exposure2012 讀回 {got.get('Exposure2012')}")
    if "ColorTemperature" in crs and int(got.get("ColorTemperature", -1)) != int(crs["ColorTemperature"]):
        problems.append("色溫沒有寫入")
    if "CropLeft" in crs and abs(float(got.get("CropLeft", -1)) - float(crs["CropLeft"])) > 1e-4:
        problems.append("裁切沒有寫入")
    if has_grad and "MaskGroupBasedCorrExposure2012" not in got:
        problems.append("漸層遮罩沒有寫入")
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("params")
    ap.add_argument("sources", nargs="+")
    ap.add_argument("--dst", required=True)
    ap.add_argument("--only", default="")
    ap.add_argument("--keyword", default="")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    params = json.load(open(a.params))
    files = []
    for s in a.sources:
        files += sorted(glob.glob(os.path.join(s, "*.[dD][nN][gG]"))) if os.path.isdir(s) else [s]
    dst_real = os.path.realpath(a.dst)
    if any(os.path.realpath(os.path.dirname(f)) == dst_real for f in files):
        sys.exit("拒絕執行：--dst 與來源相同，會覆寫原檔。")
    only = [k for k in a.only.split(",") if k]
    os.makedirs(a.dst, exist_ok=True)
    fails = 0
    for src in files:
        stem = os.path.splitext(os.path.basename(src))[0]
        if only and not any(key_matches(k, stem) for k in only):
            continue
        group, p = merged_params(stem, params)
        crs = to_crs(p, stem)
        # 相容舊格式：group_detail / detail_overrides 直接指定 crs 標籤（例如 LuminanceSmoothing）
        crs.update(params.get("group_detail", {}).get(group, {}))
        for k, v in params.get("detail_overrides", {}).items():
            if key_matches(k, stem):
                crs.update(v)
        # 有 PrintConv（文字選項）的欄位要加 # 直接寫數值，否則 exiftool 只警告、不寫入
        args = [f"-XMP-crs:{k}{'#' if k in NUMERIC_RAW else ''}={fmt(k, v)}" for k, v in crs.items()]
        gg = p.get("GroundGradient")
        if gg:
            args.append(gradient_arg(gg))
        if a.keyword:
            args.append(f"-XMP-dc:Subject+={a.keyword}")
        if a.dry:
            print(stem, group, " ".join(args))
            continue
        dst = os.path.join(a.dst, os.path.basename(src))
        shutil.copy2(src, dst)  # 每次都從原檔重新複製，避免上一輪的設定殘留
        r = subprocess.run(["exiftool", "-q", "-overwrite_original", "-m", *args, dst], capture_output=True, text=True)
        problems = []
        noise = [l for l in r.stderr.splitlines() if l.strip() and not any(b in l for b in BENIGN)]
        if r.returncode != 0 or noise:
            problems += noise or [r.stderr.strip()]
        problems += verify(dst, crs, bool(gg))
        fails += bool(problems)
        print(f"{stem}\t{group}\t" + ("OK" if not problems else "FAIL " + "；".join(problems)))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()

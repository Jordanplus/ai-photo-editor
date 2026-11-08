"""把 params/*.json 的調整值轉成 Lightroom (crs:) 設定，寫進 DNG 複本。

用法：python3 write_lr_settings.py <params.json> <src_dir> <dst_dir> [--only 0037,0038] [--dry]
原檔不動；先複製到 dst_dir，再用 exiftool 把 XMP-crs 寫進複本（Lightroom 匯入時會讀取）。
"""
import argparse, glob, json, os, re, shutil, subprocess, uuid

# 預覽參數名 → crs 標籤
MAP = {
    "Exposure": "Exposure2012", "Contrast": "Contrast2012", "Highlights": "Highlights2012",
    "Shadows": "Shadows2012", "Whites": "Whites2012", "Blacks": "Blacks2012",
    "Texture": "Texture", "Clarity": "Clarity2012", "Dehaze": "Dehaze",
    "Vibrance": "Vibrance", "Saturation": "Saturation",
    # Lightroom 的色彩分級：亮部 / 暗部沿用 SplitToning 名稱，中間調 / 全域用 ColorGrade
    "ColorGradeHighlightHue": "SplitToningHighlightHue", "ColorGradeHighlightSat": "SplitToningHighlightSaturation",
    "ColorGradeShadowHue": "SplitToningShadowHue", "ColorGradeShadowSat": "SplitToningShadowSaturation",
    "ColorGradeMidtoneHue": "ColorGradeMidtoneHue", "ColorGradeMidtoneSat": "ColorGradeMidtoneSat",
    "ColorGradeGlobalHue": "ColorGradeGlobalHue", "ColorGradeGlobalSat": "ColorGradeGlobalSat",
    "Vignette": "PostCropVignetteAmount",
}
for band in ["Red", "Orange", "Yellow", "Green", "Aqua", "Blue", "Purple", "Magenta"]:
    MAP["Sat" + band] = "SaturationAdjustment" + band
    MAP["Lum" + band] = "LuminanceAdjustment" + band
    MAP["Hue" + band] = "HueAdjustment" + band

# 所有照片共用：鏡頭校正與細節
COMMON = {
    "Version": "18.2", "ProcessVersion": "15.4", "HasSettings": "True", "WhiteBalance": "As Shot",
    "LensProfileEnable": 1, "AutoLateralCA": 1,
    "Sharpness": 40, "SharpenRadius": "+1.0", "SharpenDetail": 25, "SharpenEdgeMasking": 25,
    "LuminanceSmoothing": 8, "ColorNoiseReduction": 25, "ColorNoiseReductionDetail": 50,
}


UNSIGNED_PREFIXES = ("LensProfileEnable", "AutoLateralCA", "Sharpness", "SharpenDetail", "SharpenEdgeMasking",
                     "LuminanceSmoothing", "ColorNoiseReduction", "PostCropVignetteStyle",
                     "PostCropVignetteMidpoint", "PostCropVignetteFeather",
                     "SplitToningHighlightHue", "SplitToningShadowHue", "ColorGradeMidtoneHue",
                     "ColorGradeGlobalHue", "SplitToningHighlightSaturation", "SplitToningShadowSaturation",
                     "ColorGradeMidtoneSat", "ColorGradeGlobalSat")


def fmt(tag, v):
    """依 Lightroom 寫出的格式：雙向滑桿帶正負號，其餘為純數字。"""
    if tag == "Exposure2012":
        return f"{v:+.2f}"
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        v = int(round(v))
        if tag.startswith(UNSIGNED_PREFIXES):
            return str(v)
        return f"+{v}" if v > 0 else str(v)
    return str(v)


def settings_for(short, params):
    g = next(k for k, v in params["groups"].items() if short in v["files"])
    p = dict(params["groups"][g]["base"])
    p.update(params.get("overrides", {}).get(short, {}))
    crs = dict(COMMON)
    crs.update(params.get("group_detail", {}).get(g, {}))
    crs.update(params.get("detail_overrides", {}).get(short, {}))
    for k, v in p.items():
        if k in MAP:
            crs[MAP[k]] = v
        elif k == "Crop":  # [left, top, right, bottom]，0~1 正規化座標
            crs.update(HasCrop="True", CropLeft=f"{v[0]:.6f}", CropTop=f"{v[1]:.6f}",
                       CropRight=f"{v[2]:.6f}", CropBottom=f"{v[3]:.6f}", CropAngle="0")
        elif k == "Temperature":  # 絕對色溫，改用自訂白平衡
            crs.update(WhiteBalance="Custom", ColorTemperature=str(int(v)))  # exiftool 名稱；XMP 內為 crs:Temperature
        elif k == "Tint":
            crs["Tint"] = v
        elif k == "GroundGradient":  # 地平線下方的漸層補光，另由 gradient_arg() 產生
            pass
        elif k != "Warm":
            raise KeyError(f"未知參數 {k}（{short}）")
    if "PostCropVignetteAmount" in crs:
        crs.update(PostCropVignetteStyle=1, PostCropVignetteMidpoint=50,
                   PostCropVignetteFeather=50, PostCropVignetteRoundness=0)
    return g, crs


def gradient_arg(short, params):
    """產生 exiftool 的 MaskGroupBasedCorrections 結構參數（線性漸層，往下逐漸生效）。

    座標為未裁切畫面的 0~1 正規化座標；ZeroY 處效果為 0，FullY 以下為 100%。
    區域調整值在 XMP 內為正規化數值：曝光 = EV / 4，其餘 = 滑桿值 / 100。
    """
    gg = params.get("overrides", {}).get(short, {}).get("GroundGradient")
    if not gg:
        return None
    h = gg["horizon"]
    zero_y = max(0.0, h - gg.get("above", 0.02))
    full_y = min(1.0, h + gg.get("below", 0.10))
    sync_c = uuid.uuid4().hex.upper()
    sync_m = uuid.uuid4().hex.upper()
    mask = (f"{{What=Mask/Gradient,MaskActive=true,MaskBlendMode=0,MaskInverted=false,"
            f"MaskName=地面補光,MaskSyncID={sync_m},MaskValue=1,MaskVersion=1,"
            f"ZeroX=0.5,ZeroY={zero_y:.6f},FullX=0.5,FullY={full_y:.6f}}}")
    corr = (f"{{What=Correction,CorrectionActive=true,CorrectionAmount=1,"
            f"CorrectionName=地面補光（Claude）,CorrectionSyncID={sync_c},"
            f"LocalExposure2012={gg['exposure'] / 4:.6f},"
            f"LocalShadows2012={gg.get('shadows', 0) / 100:.6f},"
            f"CorrectionMasks=[{mask}]}}")
    return f"-XMP-crs:MaskGroupBasedCorrections=[{corr}]"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("params"); ap.add_argument("src"); ap.add_argument("dst")
    ap.add_argument("--only", default=""); ap.add_argument("--dry", action="store_true")
    ap.add_argument("--keyword", default="")
    a = ap.parse_args()
    params = json.load(open(a.params))
    only = set(filter(None, a.only.split(",")))
    os.makedirs(a.dst, exist_ok=True)
    for src in sorted(glob.glob(os.path.join(a.src, "*.DNG"))):
        short = os.path.basename(src).split("_")[2]
        if only and short not in only:
            continue
        g, crs = settings_for(short, params)
        args = [f"-XMP-crs:{k}={fmt(k, v)}" for k, v in crs.items()]
        grad = gradient_arg(short, params)
        if grad:
            args.append(grad)
        if a.keyword:
            args.append(f"-XMP-dc:Subject+={a.keyword}")
        if a.dry:
            print(short, g, " ".join(args)); continue
        dst = os.path.join(a.dst, os.path.basename(src))
        shutil.copy2(src, dst)
        r = subprocess.run(["exiftool", "-q", "-overwrite_original", "-m", *args, dst],
                           capture_output=True, text=True)
        bad = r.returncode != 0 or re.search(r"doesn't exist|isn't writable|Error", r.stderr)
        print(short, g, "FAIL " + r.stderr.strip() if bad else "OK")


if __name__ == "__main__":
    main()

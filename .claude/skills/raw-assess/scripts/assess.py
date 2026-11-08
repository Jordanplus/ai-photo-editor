#!/usr/bin/env python3
"""判讀一批 RAW/DNG：曝光、過曝、暗部、地平線、天空與地面亮度差、拍攝色溫、變焦偵測。

用法：
  python3 assess.py <DNG 檔或資料夾>... --out <輸出資料夾> [--workers 6] [--save-lin]

輸出：
  <out>/report.json   每張的完整數據（給 lightroom-dng-settings 的 suggest_params.py 使用）
  <out>/report.md     給人看的判讀表
  <out>/contact.jpg   內嵌預覽索引圖（相機 JPEG 的樣子）
  <out>/previews/     各張內嵌預覽 JPEG
  <out>/lin/          （--save-lin）1600px 寬線性 sRGB float16，可供近似預覽渲染

需要：exiftool、python3 套件 rawpy numpy scipy opencv-python pillow；ImageMagick（索引圖）。
"""
import argparse, glob, json, os, subprocess, sys
from multiprocessing import Pool

import numpy as np

ILLUM_CCT = {17: 2856.0, 21: 6504.0, 23: 5003.0, 20: 5503.0, 22: 7504.0, 24: 5500.0, 10: 6500.0}
RAW_EXT = (".dng", ".DNG", ".arw", ".ARW", ".nef", ".NEF", ".cr3", ".CR3", ".raf", ".RAF")


# ---------- 色溫換算（DNG 規格：ColorMatrix 依 1/CCT 內插、迭代求白點） ----------
def planck_uv(T):
    u = (0.860117757 + 1.54118254e-4 * T + 1.28641212e-7 * T * T) / (1 + 8.42420235e-4 * T + 7.08145163e-7 * T * T)
    v = (0.317398726 + 4.22806245e-5 * T + 4.20481691e-8 * T * T) / (1 - 2.89741816e-5 * T + 1.61456053e-7 * T * T)
    return u, v


def uv_to_temp_tint(u, v):
    Ts = np.exp(np.linspace(np.log(1500), np.log(15000), 20000))
    pu, pv = planck_uv(Ts)
    i = int(np.argmin((pu - u) ** 2 + (pv - v) ** 2))
    j = min(i + 1, len(Ts) - 1)
    tx, ty = pu[j] - pu[i], pv[j] - pv[i]
    d = np.hypot(u - pu[i], v - pv[i])
    sign = np.sign((u - pu[i]) * -ty + (v - pv[i]) * tx)
    return float(Ts[i]), float(d * sign * -3000.0)  # Tint 與 Lightroom 同方向：負 = 偏綠


def xy_to_uv(x, y):
    d = -2 * x + 12 * y + 3
    return 4 * x / d, 6 * y / d


def as_shot_temp_tint(t):
    try:
        cm1 = np.array(t["ColorMatrix1"], float).reshape(3, 3)
        cm2 = np.array(t.get("ColorMatrix2", t["ColorMatrix1"]), float).reshape(3, 3)
        t1 = ILLUM_CCT.get(int(t.get("CalibrationIlluminant1", 17)), 2856.0)
        t2 = ILLUM_CCT.get(int(t.get("CalibrationIlluminant2", 21)), 6504.0)
        neutral = np.array(t["AsShotNeutral"], float)
    except (KeyError, ValueError):
        return None, None
    x, y = 0.3457, 0.3585
    for _ in range(60):
        T = uv_to_temp_tint(*xy_to_uv(x, y))[0]
        g = np.clip((1 / T - 1 / t2) / (1 / t1 - 1 / t2), 0, 1) if t1 != t2 else 1.0
        XYZ = np.linalg.solve(g * cm1 + (1 - g) * cm2, neutral)
        nx, ny = XYZ[0] / XYZ.sum(), XYZ[1] / XYZ.sum()
        if abs(nx - x) + abs(ny - y) < 1e-8:
            break
        x, y = nx, ny
    return uv_to_temp_tint(*xy_to_uv(x, y))


# ---------- exiftool ----------
TAGS = ["Model", "DateTimeOriginal", "ExposureTime", "FNumber", "ISO", "BaselineExposure", "AsShotNeutral",
        "ColorMatrix1", "ColorMatrix2", "CalibrationIlluminant1", "CalibrationIlluminant2", "GPSLatitude",
        "GPSLongitude", "RelativeAltitude", "GimbalPitchDegree", "WhiteBalanceCCT", "DigitalZoomRatio",
        "FocalLengthIn35mmFormat"]


def read_tags(path):
    out = subprocess.run(["exiftool", "-j", "-n", "-q", "-q", *[f"-{t}" for t in TAGS], path],
                         capture_output=True, text=True).stdout
    t = json.loads(out)[0]
    for k in ["ColorMatrix1", "ColorMatrix2", "AsShotNeutral"]:
        if k in t:
            t[k] = [float(v) for v in str(t[k]).split()]
    return t


def extract_largest_preview(path, dst):
    """抽出最大的內嵌 JPEG（DJI 的 IFD0 只有 160px 縮圖，大的在 SubIFD1）。"""
    listing = subprocess.run(["exiftool", "-a", "-G1", "-s", "-n", "-q", "-q", "-PreviewImageLength",
                              "-JpgFromRawLength", "-ThumbnailLength", path], capture_output=True, text=True).stdout
    best = None
    for line in listing.splitlines():
        grp, rest = line.split("]", 1)
        name, val = [s.strip() for s in rest.split(":", 1)]
        tag = {"PreviewImageLength": "PreviewImage", "JpgFromRawLength": "JpgFromRaw",
               "ThumbnailLength": "ThumbnailImage"}.get(name)
        if tag and val.isdigit() and (best is None or int(val) > best[0]):
            best = (int(val), f"{grp.strip('[')}:{tag}")
    if not best:
        return False
    data = subprocess.run(["exiftool", "-b", "-q", "-q", f"-{best[1]}", path], capture_output=True).stdout
    if not data:
        return False
    open(dst, "wb").write(data)
    return True


# ---------- 變焦偵測：內嵌預覽 vs RAW 全幅 ----------
def detect_zoom(preview_path, render_u8):
    """用兩種獨立方法估計「內嵌預覽是 RAW 畫面的幾分之幾」，交叉確認。

    方法 A：SIFT 特徵點 + 相似變換，可得任意位置與倍率（暗部多的照片特徵點少、可能不準）。
    方法 B：中央裁切相關係數，掃描 1.0～4.0 倍（每 0.1 倍），取最高者（只適用中央裁切）。
    只比兩個候選倍率會漏掉 3 倍這類情況（實際踩過），所以一律掃描整個範圍。
    回傳 dict(zoom, rect[l,t,r,b], method, agree, sift, corr)。
    """
    import cv2
    pv = cv2.imread(preview_path, cv2.IMREAD_GRAYSCALE)
    if pv is None:
        return None
    rg = cv2.cvtColor(render_u8, cv2.COLOR_RGB2GRAY)
    H, W = rg.shape

    sift_res = None
    sift = cv2.SIFT_create(4000)
    k1, d1 = sift.detectAndCompute(pv, None)
    k2, d2 = sift.detectAndCompute(rg, None)
    if d1 is not None and d2 is not None and len(k1) > 20 and len(k2) > 20:
        m = cv2.BFMatcher().knnMatch(d1, d2, k=2)
        good = [a for a, b in (p for p in m if len(p) == 2) if a.distance < 0.7 * b.distance]
        if len(good) >= 20:
            src = np.float32([k1[g.queryIdx].pt for g in good])
            dst = np.float32([k2[g.trainIdx].pt for g in good])
            M, inl = cv2.estimateAffinePartial2D(src, dst, ransacReprojThreshold=4)
            if M is not None and inl.sum() >= 15:
                s = float(np.hypot(M[0, 0], M[1, 0]))
                frac = pv.shape[1] * s / W
                l, t = M[0, 2] / W, M[1, 2] / H
                sift_res = dict(zoom=1 / frac, rect=[l, t, l + frac, t + pv.shape[0] * s / H], inliers=int(inl.sum()))

    pvf = pv.astype(np.float32)
    b = (pvf - pvf.mean()) / (pvf.std() + 1e-6)
    scores = []
    for z in np.round(np.arange(1.0, 4.01, 0.1), 2):
        w, h = int(W / z), int(H / z)
        x0, y0 = (W - w) // 2, (H - h) // 2
        c = cv2.resize(rg[y0:y0 + h, x0:x0 + w].astype(np.float32), (pv.shape[1], pv.shape[0]))
        a = (c - c.mean()) / (c.std() + 1e-6)
        scores.append((float(z), float((a * b).mean())))
    zc, rc = max(scores, key=lambda x: x[1])
    corr_res = dict(zoom=zc, score=round(rc, 3), scan={f"{z:g}": round(r, 3) for z, r in scores if z in (1, 1.5, 2, 2.5, 3, 3.5, 4)})

    if sift_res and abs(sift_res["zoom"] - zc) / zc < 0.08:
        z, rect, method, agree = sift_res["zoom"], sift_res["rect"], "特徵點與相關係數一致", True
    elif sift_res and sift_res["inliers"] >= 150:
        z, rect, method, agree = sift_res["zoom"], sift_res["rect"], "特徵點（相關係數不同意，請目視確認）", False
    else:
        z = zc
        rect = [0.5 - 0.5 / z, 0.5 - 0.5 / z, 0.5 + 0.5 / z, 0.5 + 0.5 / z]
        method, agree = ("中央裁切相關係數" if not sift_res else "相關係數（特徵點不同意，請目視確認）"), sift_res is None
    return dict(zoom=float(z), rect=[float(v) for v in rect], method=method, agree=agree,
                sift=sift_res, corr=corr_res)


def warmth_gap(preview_path, render_u8, rect):
    """相機 JPEG 比 RAW（拍攝白平衡）暖多少：log2 of (R/B)_jpeg / (R/B)_raw，只看中間亮度的像素。

    > 0.25 代表 JPEG 明顯較暖（DJI 夕陽常見），Lightroom 開 RAW 會顯得灰，要主動提高色溫。
    """
    import cv2
    pv = cv2.cvtColor(cv2.imread(preview_path), cv2.COLOR_BGR2RGB).astype(np.float32)
    H, W = render_u8.shape[:2]
    l, t, r, b = rect
    crop = render_u8[max(int(t * H), 0):int(b * H), max(int(l * W), 0):int(r * W)]
    crop = cv2.resize(crop, (pv.shape[1], pv.shape[0])).astype(np.float32)

    def rb(img):
        Y = img.mean(axis=2)
        m = (Y > 40) & (Y < 240)
        return img[..., 0][m].mean() / (img[..., 2][m].mean() + 1e-6) if m.sum() > 1000 else np.nan
    return round(float(np.log2(rb(pv) / rb(crop))), 2)


def snap_zoom(z):
    for cand in (1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0):
        if abs(z - cand) / cand < 0.04:
            return cand
    return round(z, 2)


# ---------- 單張分析 ----------
def analyze(args):
    path, out, save_lin = args
    import rawpy
    from scipy.ndimage import gaussian_filter1d
    name = os.path.splitext(os.path.basename(path))[0]
    t = read_tags(path)
    baseline = float(t.get("BaselineExposure", 0) or 0)
    res = dict(file=os.path.basename(path), name=name, model=t.get("Model"), time=t.get("DateTimeOriginal"),
               iso=t.get("ISO"), shutter=t.get("ExposureTime"), aperture=t.get("FNumber"),
               baseline_exposure=baseline, gps=[t.get("GPSLatitude"), t.get("GPSLongitude")],
               rel_altitude=t.get("RelativeAltitude"), gimbal_pitch=t.get("GimbalPitchDegree"),
               exif_zoom=t.get("DigitalZoomRatio"), camera_wb_cct=t.get("WhiteBalanceCCT"))
    T, tint = as_shot_temp_tint(t)
    res["as_shot_temp"], res["as_shot_tint"] = (round(T), round(tint, 1)) if T else (None, None)

    with rawpy.imread(path) as r:
        raw = r.raw_image_visible
        cols = r.raw_colors_visible
        wl = r.white_level
        res["raw_clip_pct"] = {lab: round(float((raw[cols == c] >= wl * 0.985).mean() * 100), 3)
                               for c, lab in [(0, "R"), (1, "G"), (2, "B")] if (cols == c).any()}
        lin = r.postprocess(half_size=True, use_camera_wb=True, no_auto_bright=True, output_bps=16,
                            gamma=(1, 1), output_color=rawpy.ColorSpace.sRGB).astype(np.float32) / 65535.0
        disp = r.postprocess(half_size=True, use_camera_wb=True, output_bps=8)
    lin *= 2 ** baseline  # Lightroom 會套用 BaselineExposure，判讀要用套用後的亮度
    H, W = lin.shape[:2]
    if save_lin:
        step = max(1, W // 1600)
        np.save(os.path.join(out, "lin", name + ".npy"), lin[::step, ::step].astype(np.float16))

    # 內嵌預覽與變焦
    pv_path = os.path.join(out, "previews", name + ".jpg")
    zoom = None
    if extract_largest_preview(path, pv_path):
        import cv2
        scale = 2048 / disp.shape[1]
        small = cv2.resize(disp, (2048, int(disp.shape[0] * scale)))
        zoom = detect_zoom(pv_path, small)
    if zoom:
        zoom["zoom_snapped"] = snap_zoom(zoom["zoom"])
        res["jpeg_warmer_than_raw"] = warmth_gap(pv_path, small, zoom["rect"])
    res["zoom"] = zoom
    z = zoom["zoom_snapped"] if zoom else 1.0
    if z > 1.01:
        l, tp, rr, b = zoom["rect"] if zoom["method"] == "特徵點比對" else zoom["rect"]
        ys, ye, xs, xe = int(tp * H), int(b * H), int(l * W), int(rr * W)
        ys, xs = max(ys, 0), max(xs, 0)
        region = lin[ys:ye, xs:xe]
    else:
        ys, ye = 0, H
        region = lin

    Y = 0.2126 * region[..., 0] + 0.7152 * region[..., 1] + 0.0722 * region[..., 2]
    res["ev_vs_midgray"] = round(float(np.log2(np.exp(np.log(Y + 1e-5).mean()) / 0.18)), 2)
    res["deep_shadow_pct"] = round(float((Y < 0.005).mean() * 100), 2)
    res["p99_5"] = round(float(np.percentile(Y, 99.5)), 3)

    # 地平線：逐列中位亮度（對數）下降最陡的位置；座標換回全幅畫面
    h = Y.shape[0]
    prof = gaussian_filter1d(np.log2(np.median(Y, axis=1) + 1e-5), max(h / 150, 1))
    der = np.gradient(prof)
    lo, hi = int(h * 0.1), int(h * 0.9)
    k = lo + int(np.argmin(der[lo:hi]))
    sky, gnd = np.median(Y[:k]), np.median(Y[k:])
    res["horizon_y"] = round((ys + k) / H, 3)
    res["sky_minus_ground_ev"] = round(float(np.log2((sky + 1e-5) / (gnd + 1e-5))), 2)
    pitch = res.get("gimbal_pitch")
    # 雲台俯角超過 35° 時畫面通常沒有天空，地平線數據無意義
    res["has_horizon"] = not (pitch is not None and float(pitch) < -35) and res["sky_minus_ground_ev"] > 0.5
    res["notes"] = notes_for(res)
    return res


def notes_for(r):
    n = []
    if r["ev_vs_midgray"] < -1.5:
        n.append(f"整體偏暗（{r['ev_vs_midgray']:+.1f} EV）")
    clip = max(r["raw_clip_pct"].values()) if r["raw_clip_pct"] else 0
    if clip > 0.5:
        n.append(f"RAW 有 {clip:.1f}% 像素過曝，亮部救不回")
    if r["deep_shadow_pct"] > 10:
        n.append(f"{r['deep_shadow_pct']:.0f}% 面積幾乎全黑，提亮後雜訊會明顯")
    d = r["sky_minus_ground_ev"]
    if d >= 3 and r["has_horizon"]:
        n.append(f"地面比天空暗 {d:.1f} EV，建議在 y≈{r['horizon_y']:.2f} 以下加漸層補光")
    z = r["zoom"]
    if z and z["zoom_snapped"] > 1.01:
        n.append(f"用 {z['zoom_snapped']:g} 倍變焦拍攝：RAW 是全幅，構圖要裁回中央（{z['method']}）")
    if r["baseline_exposure"]:
        n.append(f"DNG 帶 BaselineExposure {r['baseline_exposure']:+.2f} EV（開檔軟體會自動提亮）")
    w = r.get("jpeg_warmer_than_raw")
    if w is not None and w == w and w > 0.25:
        n.append(f"相機 JPEG 比 RAW 暖很多（+{w:.2f}），Lightroom 開檔會顯得灰，建議色溫拉到拍攝值 +1000～1500K")
    if z and not z.get("agree", True):
        n.append("變焦倍率兩種方法結果不一致，請目視比對內嵌預覽")
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--save-lin", action="store_true")
    a = ap.parse_args()
    files = []
    for p in a.inputs:
        files += sorted(f for f in glob.glob(os.path.join(p, "*")) if f.endswith(RAW_EXT)) if os.path.isdir(p) else [p]
    if not files:
        sys.exit("找不到 RAW 檔")
    os.makedirs(os.path.join(a.out, "previews"), exist_ok=True)
    if a.save_lin:
        os.makedirs(os.path.join(a.out, "lin"), exist_ok=True)
    with Pool(a.workers) as pool:
        res = pool.map(analyze, [(f, a.out, a.save_lin) for f in files])
    res.sort(key=lambda r: (r["time"] or "", r["file"]))
    json.dump(res, open(os.path.join(a.out, "report.json"), "w"), ensure_ascii=False, indent=1)

    lines = ["| 檔案 | 時間 | 亮度(EV) | 暗部% | 天空-地面 | 地平線 y | 變焦 | 拍攝色溫/色調 | 判讀 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in res:
        z = r["zoom"]["zoom_snapped"] if r["zoom"] else "?"
        wb = f"{r['as_shot_temp']}K / {r['as_shot_tint']:+.0f}" if r["as_shot_temp"] else "-"
        lines.append(f"| {r['file']} | {(r['time'] or '')[5:16]} | {r['ev_vs_midgray']:+.2f} | {r['deep_shadow_pct']:.1f} | "
                     f"{r['sky_minus_ground_ev']:+.1f} | {r['horizon_y']:.2f} | {z}× | {wb} | {'；'.join(r['notes']) or '正常'} |")
    open(os.path.join(a.out, "report.md"), "w").write(
        "# RAW 判讀\n\n亮度(EV)：相對 18% 中灰的幾何平均，已含 BaselineExposure；一般風景約 -0.5～-1 屬正常。\n"
        "變焦 >1 的照片，亮度與暗部數據只算裁切範圍內。\n\n" + "\n".join(lines) + "\n")
    pvs = sorted(glob.glob(os.path.join(a.out, "previews", "*.jpg")))
    if pvs:
        subprocess.run(["magick", "montage", *pvs, "-resize", "480x360", "-geometry", "+4+4",
                        "-tile", "4x", os.path.join(a.out, "contact.jpg")],
                       capture_output=True)
    print(f"完成 {len(res)} 張 → {a.out}/report.md")


if __name__ == "__main__":
    main()

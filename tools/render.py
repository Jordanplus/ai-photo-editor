"""近似 Lightroom 基本面板的預覽渲染器（只用來比較方向，非 LR 的實際算法）。

輸入：analyze.py 存的線性 sRGB（相機白平衡）縮圖。
參數名稱沿用 Lightroom（Exposure / Highlights / Shadows ...），方便直接轉成 crs: 設定。
"""
import json, os, sys
import numpy as np
from scipy.ndimage import gaussian_filter
from PIL import Image

HERE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "work")
LIN = os.path.join(HERE, "lin")
TC = np.load(os.path.join(os.path.dirname(HERE), "params", "tonecurve.npy"))


def srgb_encode(x):
    x = np.clip(x, 0, 1)
    return np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1 / 2.4) - 0.055)


def lum(x):
    return 0.2126 * x[..., 0] + 0.7152 * x[..., 1] + 0.0722 * x[..., 2]


def rgb_to_hsv(x):
    mx = x.max(-1); mn = x.min(-1); d = mx - mn + 1e-8
    r, g, b = x[..., 0], x[..., 1], x[..., 2]
    h = np.where(mx == r, ((g - b) / d) % 6, np.where(mx == g, (b - r) / d + 2, (r - g) / d + 4)) * 60
    s = np.where(mx > 0, (mx - mn) / (mx + 1e-8), 0)
    return h, s, mx


def hsv_to_rgb(h, s, v):
    c = v * s; hp = (h % 360) / 60; xx = c * (1 - np.abs(hp % 2 - 1)); m = v - c
    z = np.zeros_like(h)
    conds = [(hp < 1), (hp < 2), (hp < 3), (hp < 4), (hp < 5), (hp >= 5)]
    rs = [c, xx, z, z, xx, c]; gs = [xx, c, c, xx, z, z]; bs = [z, z, xx, c, c, xx]
    r = np.select(conds, rs); g = np.select(conds, gs); b = np.select(conds, bs)
    return np.stack([r + m, g + m, b + m], -1)


# Lightroom HSL 八個色帶的中心色相（度）
HUES = dict(Red=0, Orange=30, Yellow=60, Green=120, Aqua=180, Blue=225, Purple=275, Magenta=320)


def render(name, p, baseline=0.0, width=None):
    x = np.load(os.path.join(LIN, name + ".npy")).astype(np.float32)
    if width and x.shape[1] > width:
        s = x.shape[1] // width
        x = x[::s, ::s]
    W = x.shape[1]
    # 白平衡微調（相對 As Shot；warm>0 偏暖、tint>0 偏洋紅）
    warm = p.get("Warm", 0) / 100.0
    tint = p.get("TintShift", 0) / 100.0
    x = x * np.array([1 + 0.25 * warm, 1 - 0.15 * tint, 1 - 0.25 * warm], np.float32)
    # 曝光（含 DNG BaselineExposure）
    x = x * 2 ** (baseline + p.get("Exposure", 0))
    Y = lum(x) + 1e-6
    L = np.log2(Y / 0.18)
    # 亮部 / 陰影：以大範圍模糊後的亮度決定區域權重
    Lb = gaussian_filter(L, W / 40)
    ws = 1 / (1 + np.exp((Lb + 1.5) * 2.0))
    wh = 1 / (1 + np.exp(-(Lb - 0.8) * 2.0))
    dL = p.get("Shadows", 0) / 100 * 1.8 * ws + p.get("Highlights", 0) / 100 * 1.6 * wh
    # 對比（以中灰為支點）
    c = p.get("Contrast", 0) / 100
    Lc = L * (1 + 0.35 * c)
    # 清晰度 / 紋理：局部對比
    mid = np.exp(-(Lb / 2.5) ** 2)
    det_c = L - gaussian_filter(L, W / 60)
    det_t = L - gaussian_filter(L, W / 400)
    Lc = Lc + p.get("Clarity", 0) / 100 * 0.6 * det_c * mid + p.get("Texture", 0) / 100 * 0.6 * det_t
    Ln = Lc + dL
    gain = 2 ** (Ln - L)
    x = x * gain[..., None]
    # 去朦朧：扣掉以暗通道估計的霧氣
    dh = p.get("Dehaze", 0) / 100
    if dh:
        dc = gaussian_filter(x.min(-1), W / 80)
        A = np.percentile(lum(x), 99.5)
        t = np.clip(1 - 0.7 * dh * dc / (A + 1e-6), 0.3, 1.5)
        x = (x - A * (1 - t)[..., None] * 0.9 * np.sign(dh)) / t[..., None]
        x = np.clip(x, 0, None)
    # 色調映射：DNG 內嵌的 ProfileToneCurve（亮部先做柔性壓縮避免硬切）
    xs = np.where(x > 0.8, 0.8 + 0.2 * np.tanh((x - 0.8) / 0.2), x)
    y = np.interp(xs, TC[:, 0], TC[:, 1])
    # 色彩分級（Color Grading）：暗部 / 中間調 / 亮部 / 全域，hue 單位為度
    Yd = lum(y)
    regions = dict(Shadow=np.clip(1 - Yd / 0.4, 0, 1) ** 1.5,
                   Midtone=np.exp(-((Yd - 0.5) / 0.25) ** 2),
                   Highlight=np.clip((Yd - 0.5) / 0.5, 0, 1) ** 1.2,
                   Global=np.ones_like(Yd))
    for reg, w in regions.items():
        sat_cg = p.get(f"ColorGrade{reg}Sat", 0) / 100
        if sat_cg:
            hue_cg = p.get(f"ColorGrade{reg}Hue", 0)
            col = hsv_to_rgb(np.array(hue_cg, float), np.array(1.0), np.array(1.0))
            col = col / lum(col)
            y = y * (1 + sat_cg * 0.35 * w[..., None] * (col - 1))
    # 白色 / 黑色
    bl = -p.get("Blacks", 0) / 100 * 0.03
    y = (y - bl) / (1 - bl)
    y = y * (1 + p.get("Whites", 0) / 100 * 0.15)
    y = np.clip(y, 0, 1)
    # 色彩：HSL、鮮豔度、飽和度
    h, s, v = rgb_to_hsv(y)
    sat_mul = np.ones_like(s); lum_mul = np.ones_like(s); hue_add = np.zeros_like(s)
    for band, hc in HUES.items():
        d = np.abs(((h - hc + 180) % 360) - 180)
        w = np.exp(-(d / 22) ** 2)
        sat_mul += w * p.get("Sat" + band, 0) / 100
        lum_mul += w * p.get("Lum" + band, 0) / 100 * 0.5 * s
        hue_add += w * p.get("Hue" + band, 0) / 100 * 25
    vib = p.get("Vibrance", 0) / 100
    sat = p.get("Saturation", 0) / 100
    s = s * sat_mul * (1 + sat) * (1 + vib * (1 - s) * 1.2)
    y = hsv_to_rgb(h + hue_add, np.clip(s, 0, 1), np.clip(v * lum_mul, 0, 1))
    # 暗角
    vg = p.get("Vignette", 0) / 100
    if vg:
        H_, W_ = y.shape[:2]
        yy, xx = np.mgrid[0:H_, 0:W_]
        r2 = ((xx / W_ - 0.5) ** 2 + (yy / H_ - 0.5) ** 2) / 0.5
        y = y * (1 + vg * 0.6 * r2 ** 1.5)[..., None]
    return srgb_encode(y)


def save(arr, path):
    Image.fromarray((np.clip(arr, 0, 1) * 255 + 0.5).astype(np.uint8)).save(path, quality=90)


if __name__ == "__main__":
    params = json.load(open(sys.argv[1]))
    metrics = {d["name"]: d for d in json.load(open(os.path.join(os.path.dirname(HERE), "params", "metrics.json")))}
    outdir = sys.argv[2]
    os.makedirs(outdir, exist_ok=True)
    groups = params["groups"]
    for name, m in metrics.items():
        short = name.split("_")[2]
        g = next(k for k, v in groups.items() if short in v["files"])
        p = dict(groups[g]["base"])
        p.update(params.get("overrides", {}).get(short, {}))
        before = render(name, {}, m["baseline_exp"], width=800)
        after = render(name, p, m["baseline_exp"], width=800)
        both = np.concatenate([before, np.ones((before.shape[0], 8, 3)), after], 1)
        save(both, os.path.join(outdir, f"{g}_{short}.jpg"))
    print("done")

"""讀每張 DNG 的 RAW 數據，量測曝光指標，並存一份縮小的線性影像供後續預覽用。

用法：python3 tools/analyze.py <DNG 資料夾>
輸出：params/metrics.json，以及 work/lin/*.npy（render.py 會讀）
"""
import argparse, json, glob, os
from multiprocessing import Pool
import numpy as np
import rawpy

HERE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "work")  # 與 render.py 相同
OUT = os.path.join(HERE, "lin")


def analyze(path):
    name = os.path.basename(path)[:-4]
    with rawpy.imread(path) as r:
        raw = r.raw_image_visible
        wl = r.white_level
        bl = np.array(r.black_level_per_channel).mean()
        cols = r.raw_colors_visible
        clip = {}
        for c, lab in [(0, "R"), (1, "G"), (3, "G2"), (2, "B")]:
            m = cols == c
            if m.any():
                clip[lab] = float((raw[m] >= wl * 0.985).mean() * 100)
        # 線性 sRGB（相機白平衡、不自動提亮）
        rgb = r.postprocess(half_size=True, use_camera_wb=True, no_auto_bright=True,
                            output_bps=16, gamma=(1, 1), output_color=rawpy.ColorSpace.sRGB)
        wb = r.camera_whitebalance
    lin = rgb.astype(np.float32) / 65535.0
    # 存 1600px 寬的線性 float16
    h, w = lin.shape[:2]
    step = max(1, w // 1600)
    small = lin[::step, ::step]
    np.save(os.path.join(OUT, name + ".npy"), small.astype(np.float16))
    Y = 0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2]
    p = np.percentile(Y, [0.5, 5, 25, 50, 75, 95, 99.5])
    geo = float(np.exp(np.log(Y + 1e-5).mean()))
    # 上 1/3 與下 2/3 分區（空拍常見：天空 vs 地面）
    top = Y[: h // 3]
    bot = Y[h // 3:]
    return dict(
        name=name,
        raw_clip_pct=clip,
        render_clip_pct=float((lin.max(axis=2) >= 0.999).mean() * 100),
        lum_pct={k: float(v) for k, v in zip(["p0.5", "p5", "p25", "p50", "p75", "p95", "p99.5"], p)},
        ev_vs_midgray=float(np.log2(geo / 0.18)),
        top_median=float(np.median(top)),
        bottom_median=float(np.median(bot)),
        sky_ground_ev=float(np.log2((np.median(top) + 1e-5) / (np.median(bot) + 1e-5))),
        deep_shadow_pct=float((Y < 0.005).mean() * 100),
        cam_wb=[float(x) for x in wb[:3]],
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="量測一個資料夾裡所有 DNG 的曝光指標")
    ap.add_argument("src", help="DNG 所在資料夾")
    src = ap.parse_args().src
    files = sorted(glob.glob(os.path.join(src, "*.DNG")) + glob.glob(os.path.join(src, "*.dng")))
    if not files:
        raise SystemExit(f"{src} 裡沒有 DNG")
    os.makedirs(OUT, exist_ok=True)
    with Pool(6) as pool:
        res = pool.map(analyze, files)
    out = os.path.join(os.path.dirname(HERE), "params", "metrics.json")
    json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    for d in res:
        c = d["raw_clip_pct"]
        print(f"{d['name'][-9:]}  clipRGB={c.get('R',0):5.2f}/{c.get('G',0):5.2f}/{c.get('B',0):5.2f}%  "
              f"EV={d['ev_vs_midgray']:+5.2f}  p50={d['lum_pct']['p50']:.3f} p99.5={d['lum_pct']['p99.5']:.3f}  "
              f"sky-ground={d['sky_ground_ev']:+5.2f}EV  deep={d['deep_shadow_pct']:5.2f}%")

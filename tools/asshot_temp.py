"""依 DNG 規格，從 ColorMatrix1/2 與 AsShotNeutral 換算拍攝白平衡的色溫 (K) 與色調 (Tint)。

Tint 使用 DNG SDK 的尺度（uv 距離 × -3000），與 Lightroom 的 Tint 滑桿同單位（近似）。
用法：python3 asshot_temp.py <dng> [...]
"""
import json, subprocess, sys
import numpy as np

ILLUM_CCT = {17: 2856.0, 21: 6504.0, 23: 5003.0, 20: 5503.0, 22: 7504.0}


def planck_uv(T):
    # Krystek (1985) Planck 軌跡在 CIE 1960 uv 的有理近似，1000–15000K
    u = (0.860117757 + 1.54118254e-4 * T + 1.28641212e-7 * T * T) / (1 + 8.42420235e-4 * T + 7.08145163e-7 * T * T)
    v = (0.317398726 + 4.22806245e-5 * T + 4.20481691e-8 * T * T) / (1 - 2.89741816e-5 * T + 1.61456053e-7 * T * T)
    return u, v


def xy_to_uv(x, y):
    d = -2 * x + 12 * y + 3
    return 4 * x / d, 6 * y / d


def uv_to_temp_tint(u, v):
    Ts = np.exp(np.linspace(np.log(1500), np.log(15000), 20000))
    pu, pv = planck_uv(Ts)
    i = int(np.argmin((pu - u) ** 2 + (pv - v) ** 2))
    T = Ts[i]
    # 以軌跡切線判斷偏綠 / 偏洋紅的方向
    j = min(i + 1, len(Ts) - 1)
    tx, ty = pu[j] - pu[i], pv[j] - pv[i]
    nx, ny = -ty, tx
    d = np.hypot(u - pu[i], v - pv[i])
    sign = np.sign((u - pu[i]) * nx + (v - pv[i]) * ny)
    duv = d * sign
    return float(T), float(duv * -3000.0)


def cct_from_xy(x, y):
    return uv_to_temp_tint(*xy_to_uv(x, y))[0]


def as_shot(tags):
    cm1 = np.array(tags["ColorMatrix1"], float).reshape(3, 3)
    cm2 = np.array(tags["ColorMatrix2"], float).reshape(3, 3)
    t1 = ILLUM_CCT[tags["CalibrationIlluminant1"]]
    t2 = ILLUM_CCT[tags["CalibrationIlluminant2"]]
    neutral = np.array(tags["AsShotNeutral"], float)
    x, y = 0.3457, 0.3585  # 起始猜測 D50
    for _ in range(50):
        T = cct_from_xy(x, y)
        g = np.clip((1 / T - 1 / t2) / (1 / t1 - 1 / t2), 0, 1)
        cm = g * cm1 + (1 - g) * cm2
        XYZ = np.linalg.solve(cm, neutral)
        nx, ny = XYZ[0] / XYZ.sum(), XYZ[1] / XYZ.sum()
        if abs(nx - x) + abs(ny - y) < 1e-7:
            break
        x, y = nx, ny
    return uv_to_temp_tint(*xy_to_uv(x, y))


def read_tags(path):
    out = subprocess.run(["exiftool", "-j", "-n", "-ColorMatrix1", "-ColorMatrix2", "-CalibrationIlluminant1",
                          "-CalibrationIlluminant2", "-AsShotNeutral", path], capture_output=True, text=True).stdout
    t = json.loads(out)[0]
    for k in ["ColorMatrix1", "ColorMatrix2", "AsShotNeutral"]:
        t[k] = [float(v) for v in str(t[k]).split()]
    return t


if __name__ == "__main__":
    for p in sys.argv[1:]:
        T, tint = as_shot(read_tags(p))
        print(f"{p.split('/')[-1]}\t{T:.0f}\t{tint:+.1f}")

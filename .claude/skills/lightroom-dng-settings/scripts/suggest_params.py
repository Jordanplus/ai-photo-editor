#!/usr/bin/env python3
"""讀 raw-assess 的 report.json，產生「第一版」參數檔骨架。

用法：python3 suggest_params.py <report.json> --out <params.json>

只處理有客觀依據的部分：
  - 曝光：亮度 < -0.5 EV 才補（補差距的一半，剩下交給陰影 / 漸層）；> +0.3 EV 才壓
  - 變焦：裁切回中央構圖
  - 地面比天空暗 ≥ 3 EV：地平線下方漸層補光
  - 相機 JPEG 比 RAW 暖很多：自訂白平衡，色溫 = 拍攝值 + 1200K，色調 +3
風格（色彩、反差、去朦朧、HSL）屬於審美判斷，放在 groups 由人或 Claude 依場景補上。
每個 override 都附 _why 說明依據，寫入工具會忽略底線開頭的欄位。
"""
import argparse, json


def short_key(name):
    parts = name.split("_")
    # DJI_YYYYMMDDhhmmss_NNNN_D → NNNN；其他命名用完整檔名
    return parts[2] if len(parts) >= 4 and parts[0] == "DJI" else name


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("report")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    report = json.load(open(a.report))
    overrides = {}
    for r in report:
        o, why = {}, []
        ev = r["ev_vs_midgray"]
        # 介於 -0.5～+0.3 EV 視為正常不動（白天風景實測如此）；更暗補一半、更亮壓一半
        exp = round(min(1.0, (-0.5 - ev) * 0.5), 2) if ev < -0.5 else (round(max(-0.5, -(ev - 0.3) * 0.5), 2) if ev > 0.3 else 0)
        if abs(exp) >= 0.1:
            o["Exposure"] = exp
            why.append(f"亮度 {ev:+.1f} EV → 曝光 {exp:+.2f}")
        z = r.get("zoom") or {}
        zs = z.get("zoom_snapped", 1)
        if zs and zs > 1.01:
            h = 0.5 / zs
            o["Crop"] = [round(0.5 - h, 6), round(0.5 - h, 6), round(0.5 + h, 6), round(0.5 + h, 6)]
            why.append(f"{zs:g} 倍變焦拍攝，裁回中央" + ("" if z.get("agree", True) else "（兩種偵測不一致，請目視確認）"))
        d = r["sky_minus_ground_ev"]
        if r.get("has_horizon") and d >= 3:
            g = round(min(1.0, max(0.2, 0.35 * (d - 2.5))), 2)
            gg = {"horizon": r["horizon_y"], "exposure": g, "shadows": 20}
            if zs and zs > 1.01:
                # 漸層座標以未裁切畫面為準；裁切後要按倍率縮小過渡帶，否則 3 倍裁切時過渡帶會占可見畫面 36%
                gg.update(above=round(0.02 / zs, 4), below=round(0.10 / zs, 4))
            o["GroundGradient"] = gg
            why.append(f"地面比天空暗 {d:.1f} EV → 地平線 y={r['horizon_y']:.2f} 以下補 {g:+.2f} EV")
        w = r.get("jpeg_warmer_than_raw")
        if w is not None and w == w and w > 0.25 and r.get("as_shot_temp"):
            o["Temperature"] = int(round((r["as_shot_temp"] + 1200) / 50) * 50)
            o["Tint"] = 3
            why.append(f"相機 JPEG 比 RAW 暖 {w:+.2f} → 色溫 {r['as_shot_temp']}K+1200")
        if r["deep_shadow_pct"] > 10:
            o["LuminanceNR"] = 30
            why.append(f"{r['deep_shadow_pct']:.0f}% 幾乎全黑 → 亮度降噪 30")
        if o:
            o["_why"] = "；".join(why)
            overrides[short_key(r["name"])] = o
    params = {
        "_note": "第一版骨架。groups 依場景分組後填入風格參數；default 套用於未分組的照片。",
        "default": {"Contrast": 5, "Highlights": -30, "Shadows": 20, "Whites": 5, "Blacks": -5,
                    "Texture": 10, "Clarity": 5, "Vibrance": 12},
        "groups": {},
        "overrides": overrides,
    }
    json.dump(params, open(a.out, "w"), ensure_ascii=False, indent=1)
    print(f"{len(overrides)} 張有個別建議 → {a.out}")


if __name__ == "__main__":
    main()

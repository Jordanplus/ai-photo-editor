#!/usr/bin/env python3
"""唯讀查詢 Lightroom Classic 目錄與預覽快取（先複製資料庫再讀，不鎖定、不修改 Lightroom 的檔案）。

用法：
  python3 lr_catalog.py backup --out DIR                          備份目錄檔（.lrcat）
  python3 lr_catalog.py find PATTERN                              列出檔名符合的照片與所在資料夾
  python3 lr_catalog.py settings PATTERN [--keys Exposure2012,...] 讀目前調整值（Lightroom 實際套用的）
  python3 lr_catalog.py history PATTERN [--since "2026-11-08 18:30"] 調整記錄（確認「從中繼資料」有沒有讀到）
  python3 lr_catalog.py previews PATTERN --out DIR [--min 1024]   取出 Lightroom 算好的預覽，轉成 sRGB JPEG
  python3 lr_catalog.py compare DIR_A DIR_B --out sheet.jpg        兩組同名圖並排（左 A 右 B）

PATTERN 是 SQL LIKE 樣式，套用在檔名（不含副檔名），例如 'DJI_2026%'。
--catalog 指定目錄檔，預設 ~/Pictures/Lightroom/Lightroom Catalog.lrcat。
"""
import argparse, glob, os, re, shutil, sqlite3, subprocess, sys, tempfile, time

DEFAULT_CAT = os.path.expanduser("~/Pictures/Lightroom/Lightroom Catalog.lrcat")
ADOBE_RGB = "/System/Library/ColorSync/Profiles/AdobeRGB1998.icc"
SRGB = "/System/Library/ColorSync/Profiles/sRGB Profile.icc"
APPLE_EPOCH = 978307200  # Lightroom 時間戳是 2001-01-01 起的秒數


def snapshot(db):
    """複製資料庫（含 -wal，Lightroom 開著時最新變更在 wal 裡）到暫存資料夾。"""
    tmp = tempfile.mkdtemp(prefix="lrcat_")
    dst = os.path.join(tmp, os.path.basename(db))
    shutil.copy2(db, dst)
    if os.path.exists(db + "-wal"):
        shutil.copy2(db + "-wal", dst + "-wal")
    return sqlite3.connect(dst)


def photos(cat, pattern):
    return cat.execute("""
        select i.id_local, f.baseName, f.extension, r.absolutePath || fo.pathFromRoot
        from AgLibraryFile f join Adobe_images i on i.rootFile = f.id_local
        join AgLibraryFolder fo on f.folder = fo.id_local join AgLibraryRootFolder r on fo.rootFolder = r.id_local
        where f.baseName like ? order by f.baseName""", (pattern,)).fetchall()


def lua_value(text, key):
    m = re.search(rf"(?m)^{re.escape(key)} = (.+?),?$", text)
    # 最後一個欄位後面接著關閉 table 的「}」，要一併去掉
    return m.group(1).strip().rstrip(",").rstrip("}").strip().rstrip(",") if m else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--catalog", default=DEFAULT_CAT)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("backup"); s.add_argument("--out", required=True)
    s = sub.add_parser("find"); s.add_argument("pattern")
    s = sub.add_parser("settings"); s.add_argument("pattern")
    s.add_argument("--keys", default="WhiteBalance,Temperature,Tint,Exposure2012,Highlights2012,Shadows2012,Dehaze,CropLeft,CropRight")
    s = sub.add_parser("history"); s.add_argument("pattern"); s.add_argument("--since")
    s = sub.add_parser("previews"); s.add_argument("pattern"); s.add_argument("--out", required=True)
    s.add_argument("--min", type=int, default=1024)
    s = sub.add_parser("compare"); s.add_argument("a"); s.add_argument("b"); s.add_argument("--out", required=True)
    a = ap.parse_args()

    if a.cmd == "backup":
        os.makedirs(a.out, exist_ok=True)
        dst = os.path.join(a.out, f"{os.path.splitext(os.path.basename(a.catalog))[0]}_{time.strftime('%Y%m%d-%H%M%S')}.lrcat")
        con = snapshot(a.catalog)
        con.execute(f"vacuum into '{dst}'")  # 把 wal 內容一併併入成單一檔案
        print(dst)
        return
    if a.cmd == "compare":
        tmp = tempfile.mkdtemp()
        pairs = []
        for fa in sorted(glob.glob(os.path.join(a.a, "*.jpg"))):
            fb = os.path.join(a.b, os.path.basename(fa))
            if os.path.exists(fb):
                o = os.path.join(tmp, os.path.basename(fa))
                subprocess.run(["magick", "(", fa, "-resize", "800x600!", ")", "(", fb, "-resize", "800x600!", ")",
                                "+append", "-bordercolor", "white", "-border", "3", o], check=True)
                pairs.append(o)
        if not pairs:
            sys.exit("兩個資料夾沒有同名的 .jpg")
        subprocess.run(["magick", "montage", *pairs, "-tile", "2x", "-geometry", "+0+0", a.out], capture_output=True)
        print(f"{len(pairs)} 組 → {a.out}（每組左 = {a.a}，右 = {a.b}）")
        return

    cat = snapshot(a.catalog)
    rows = photos(cat, a.pattern)
    if not rows:
        sys.exit(f"目錄裡沒有符合 {a.pattern} 的照片")
    if a.cmd == "find":
        for iid, name, ext, folder in rows:
            print(f"{iid}\t{name}.{ext}\t{folder}")
        print(f"共 {len(rows)} 張")
    elif a.cmd == "settings":
        keys = a.keys.split(",")
        print("檔名\t" + "\t".join(keys))
        for iid, name, *_ in rows:
            t = cat.execute("select text from Adobe_imageDevelopSettings where image = ?", (iid,)).fetchone()
            t = t[0] if t else ""
            print(name + "\t" + "\t".join(str(lua_value(t, k)) for k in keys))
    elif a.cmd == "history":
        since = (time.mktime(time.strptime(a.since, "%Y-%m-%d %H:%M")) - APPLE_EPOCH) if a.since else 0
        n_meta = 0
        for iid, name, *_ in rows:
            steps = cat.execute("""select name, dateCreated from Adobe_libraryImageDevelopHistoryStep
                                   where image = ? and dateCreated >= ? order by dateCreated""", (iid, since)).fetchall()
            last = steps[-1] if steps else None
            meta = any(("中繼資料" in s or "Metadata" in s or "元数据" in s) for s, _ in steps)
            n_meta += meta
            when = time.strftime("%H:%M:%S", time.localtime(last[1] + APPLE_EPOCH)) if last else "-"
            print(f"{name}\t{'已從檔案讀取' if meta else '-'}\t最後一步：{last[0] if last else '-'} {when}")
        print(f"{n_meta}/{len(rows)} 張在指定時間後讀取過中繼資料")
    elif a.cmd == "previews":
        pv_dir = os.path.splitext(a.catalog)[0] + " Previews.lrdata"
        pv = snapshot(os.path.join(pv_dir, "previews.db"))
        os.makedirs(a.out, exist_ok=True)
        got, missing = 0, []
        for iid, name, *_ in rows:
            r = pv.execute("select uuid, digest from ImageCacheEntry where imageId = ?", (iid,)).fetchone()
            files = []
            if r:
                u, d = r
                files = [f for f in glob.glob(os.path.join(pv_dir, u[0], u[:4], f"{u}-{d}_*"))
                         if f.rsplit("_", 1)[1].isdigit() and int(f.rsplit("_", 1)[1]) >= a.min]
            if not files:
                missing.append(name)
                continue
            src = min(files, key=lambda f: int(f.rsplit("_", 1)[1]))
            # 預覽快取是無 ICC 的 JPEG，內容為 Adobe RGB；直接當 sRGB 看會偏淡
            subprocess.run(["magick", src, "-profile", ADOBE_RGB, "-profile", SRGB, "-strip",
                            os.path.join(a.out, name + ".jpg")], check=True)
            got += 1
        print(f"取出 {got}/{len(rows)} 張 → {a.out}")
        if missing:
            print(f"缺 {len(missing)} 張（大尺寸預覽還沒產生；用 lr_ui.py build-previews 後再取）：{', '.join(missing[:8])}…")


if __name__ == "__main__":
    main()

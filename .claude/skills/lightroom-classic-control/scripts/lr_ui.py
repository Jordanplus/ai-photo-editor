#!/usr/bin/env python3
"""用 AppleScript（System Events）操作 Lightroom Classic 的選單。

用法：
  python3 lr_ui.py status [--out DIR]           截圖 + 讀狀態列（模組、共幾張、選了幾張）
  python3 lr_ui.py screen [--out DIR]           全螢幕截圖（Lightroom 沒回應時先看畫面）
  python3 lr_ui.py library                      切到「圖庫」模組（⌘⌥1）
  python3 lr_ui.py read-metadata --expect N     從檔案讀取中繼資料（安全檢查通過才執行）
  python3 lr_ui.py build-previews [--expect N]  建立標準尺寸預視
  python3 lr_ui.py import-dialog <資料夾>        叫出匯入對話框（之後要人按「新增」與「匯入」）
  python3 lr_ui.py menus                        列出選單列與相關子選單（確認介面語言 / 名稱）

需要：macOS「輔助使用」權限（給執行這支腳本的終端機 / Claude）、screencapture、tesseract（eng 即可）。
每個 AppleScript 都有逾時：Lightroom 啟動中或被對話框擋住時會卡住，逾時後先用 `screen` 看畫面。
"""
import argparse, os, re, subprocess, sys, tempfile, time

APP = "Adobe Lightroom Classic"
NAMES = {  # 介面語言：繁中 / 英文 / 簡中
    "metadata_menu": ["中繼資料", "Metadata", "元数据"],
    "read_metadata": ["從檔案讀取中繼資料", "Read Metadata from Files", "从文件读取元数据"],
    "library_menu": ["圖庫", "Library", "图库"],
    "previews_sub": ["預視", "Previews", "预览"],
    "build_standard": ["建立標準尺寸預視", "Build Standard-Sized Previews", "构建标准大小预览"],
}
LIBRARY_TITLES = ("圖庫", "Library", "图库")


def osa(script, timeout=20):
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        sys.exit(f"Lightroom 沒有在 {timeout} 秒內回應：可能正在啟動、正在處理大量工作，"
                 "或有對話框擋住（例如 macOS 權限詢問）。先執行 `lr_ui.py screen` 看畫面；系統權限對話框要由使用者決定。")
    if r.returncode != 0:
        sys.exit("AppleScript 錯誤：" + r.stderr.strip())
    return r.stdout.strip()


def activate():
    osa(f'tell application "{APP}" to activate', timeout=15)
    time.sleep(0.6)
    osa(f'tell application "System Events" to tell process "{APP}" to set frontmost to true', timeout=10)
    time.sleep(0.3)


def menu_bar_names():
    out = osa(f'tell application "System Events" to tell process "{APP}" to get name of every menu bar item of menu bar 1')
    return [s.strip() for s in out.split(",")]


def pick(key, available):
    for n in NAMES[key]:
        if n in available:
            return n
    sys.exit(f"找不到選單「{NAMES[key][0]}」，目前選單列：{available}。可能不在圖庫模組或介面語言不同，先跑 `lr_ui.py menus`。")


def menu_items(menu):
    out = osa(f'tell application "System Events" to tell process "{APP}" to get name of every menu item of menu 1 of menu bar item "{menu}" of menu bar 1')
    return [s.strip() for s in out.split(",")]


# Lightroom 會開很多沒有名稱的小視窗（面板、提示），front window 常常是其中之一；
# 主視窗的標題是「<目錄>.lrcat - Adobe Photoshop Lightroom Classic - <模組>」
MAIN_WIN = 'first window whose name contains "Lightroom Classic"'


def window_title():
    return osa(f'tell application "System Events" to tell process "{APP}" to get name of {MAIN_WIN}')


def status(out_dir):
    activate()
    title = window_title()
    pos = osa(f'tell application "System Events" to tell process "{APP}" to tell ({MAIN_WIN}) to get {{position, size}}')
    x, y, w, h = [int(float(v)) for v in pos.split(",")]
    os.makedirs(out_dir, exist_ok=True)
    win = os.path.join(out_dir, "lr_window.png")
    subprocess.run(["screencapture", "-x", "-o", f"-R{x},{y},{w},{h}", win], check=True)
    band = os.path.join(out_dir, "lr_bottom.png")
    # 狀態列（「上次讀入 30 張相片 / 選取了 30 張相片 / 檔名」）在膠卷列上方，位於視窗下半部
    subprocess.run(["magick", win, "-gravity", "South", "-crop", "100%x40%+0+0", "+repage", band], check=True)
    ocr = subprocess.run(["tesseract", band, "-", "--psm", "6"], capture_output=True, text=True).stdout
    total = selected = None
    for line in ocr.splitlines():
        if "/" in line:
            # 「… 30 張相片 / 選取了 30 張相片 / 檔名」：取最後一個 / 之前的獨立數字（中文被 OCR 成亂碼，數字通常讀得出來）
            nums = [int(n) for n in re.findall(r"\b\d{1,5}\b", line.rsplit("/", 1)[0])]
            if len(nums) >= 2:  # 前面常有 OCR 雜訊數字，取最後兩個（共幾張、選取幾張）
                total, selected = nums[-2], nums[-1]
                break
    module = "圖庫" if title.endswith(LIBRARY_TITLES) else title.rsplit("-", 1)[-1].strip()
    print(f"視窗：{title}\n模組：{module}\nOCR 讀到：共 {total} 張、選取 {selected} 張（OCR 只供參考，請用 Read 看截圖確認）")
    print(f"截圖：{win}\n下半部：{band}")
    return module, total, selected, band


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("status", "screen"):
        s = sub.add_parser(c); s.add_argument("--out", default=tempfile.gettempdir())
    sub.add_parser("library"); sub.add_parser("menus")
    s = sub.add_parser("read-metadata"); s.add_argument("--expect", type=int, required=True)
    s.add_argument("--out", default=tempfile.gettempdir())
    s.add_argument("--visually-confirmed", action="store_true",
                   help="OCR 讀不到時，已用 Read 親眼確認截圖上的選取數量等於 --expect")
    s = sub.add_parser("build-previews"); s.add_argument("--expect", type=int)
    s.add_argument("--out", default=tempfile.gettempdir())
    s = sub.add_parser("import-dialog"); s.add_argument("folder")
    a = ap.parse_args()

    if a.cmd == "screen":
        os.makedirs(a.out, exist_ok=True)
        p = os.path.join(a.out, "screen.png")
        subprocess.run(["screencapture", "-x", "-o", p], check=True)
        print(p)
    elif a.cmd == "status":
        status(a.out)
    elif a.cmd == "menus":
        activate()
        bar = menu_bar_names()
        print("選單列：", bar)
        for key in ("metadata_menu", "library_menu"):
            m = next((n for n in NAMES[key] if n in bar), None)
            if m:
                print(f"{m}：", menu_items(m))
    elif a.cmd == "library":
        activate()
        osa(f'tell application "System Events" to tell process "{APP}" to keystroke "1" using {{command down, option down}}')
        time.sleep(1.5)
        print("目前視窗：", window_title())
    elif a.cmd == "read-metadata":
        # 這個動作會用檔案內容取代目錄裡的調整；選到其他照片（例如「所有相片」）會把它們的修圖重設。
        module, total, selected, band = status(a.out)
        if module != "圖庫":
            sys.exit("拒絕執行：不在圖庫模組。在「編輯相片」模組只會套用到目前這一張。先跑 `lr_ui.py library`。")
        ok_ocr = total == a.expect and selected == a.expect
        if not ok_ocr and not a.visually_confirmed:
            sys.exit(f"拒絕執行：OCR 讀到共 {total}、選取 {selected}，不等於預期 {a.expect}。"
                     f"用 Read 看 {band}；確認狀態列確實是「共 {a.expect} 張、選取 {a.expect} 張」再加 --visually-confirmed。")
        activate()
        menu = pick("metadata_menu", menu_bar_names())
        item = next(n for n in NAMES["read_metadata"] if n in menu_items(menu))
        osa(f'tell application "System Events" to tell process "{APP}" to click menu item "{item}" of menu 1 of menu bar item "{menu}" of menu bar 1')
        print(f"已點選「{menu} > {item}」。用 lr_catalog.py history 確認每張都讀到了。")
    elif a.cmd == "build-previews":
        if a.expect:
            module, total, selected, _ = status(a.out)
            if selected not in (None, a.expect):
                print(f"注意：選取 {selected} 張，與預期 {a.expect} 不同（建立預覽不會改調整，只是多花時間）")
        activate()
        menu = pick("library_menu", menu_bar_names())
        sub_name = next(n for n in NAMES["previews_sub"] if n in menu_items(menu))
        items = osa(f'tell application "System Events" to tell process "{APP}" to get name of every menu item of menu 1 of menu item "{sub_name}" of menu 1 of menu bar item "{menu}" of menu bar 1')
        item = next(n for n in NAMES["build_standard"] if n in items)
        osa(f'tell application "System Events" to tell process "{APP}" to click menu item "{item}" of menu 1 of menu item "{sub_name}" of menu 1 of menu bar item "{menu}" of menu bar 1')
        print(f"已點選「{menu} > {sub_name} > {item}」。")
    elif a.cmd == "import-dialog":
        subprocess.run(["open", "-a", APP, a.folder], check=True)
        print("已叫出匯入對話框。請使用者在上方選「新增」（不要選複製，否則會多一份檔案），再按「匯入」。")


if __name__ == "__main__":
    main()

"""會員護照／會員皮夾 PDF 解析：取出每件優惠商品的編號、原價、折扣、優惠後售價、通路、期間。

解析方式依「文字在頁面上的位置」：PDF 是多欄排版，直接取文字會混在一起。
每個商品區塊的結構（2026-11 以 fy27p3-mvm2 實際檔案驗證）：
    ITEM 157741            ← 欄位左上，商品編號（可能有多個，以 / 分隔）
          4,389            ← 原價（大字，靠右對齊）
        -   900            ← 折扣
    $     3,489            ← 優惠後售價（前面有 $）
       【賣場售價】          ← 賣場售價；若是「僅限好市多線上購物」則為線上限定
頁面上方有「優惠期間 2026/10/26 ▲ 2026/11/22」或「平日限定優惠 …」。
所有取出的價格都會檢查「原價 − 折扣 = 售價」，不符合就丟棄。
"""
from __future__ import annotations

import io
import json
import re
import sys

NUM = re.compile(r"^\d{1,3}(,\d{3})*$|^\d+$")

# 欄位索引：text, x0, x1, top, bottom, size
T, X0, X1, TOP, BOT, SIZE = range(6)


def _num(s: str) -> int:
    return int(s.replace(",", ""))


def _is_big_num(w) -> bool:
    return w[SIZE] >= 15 and bool(NUM.match(w[T]))


def _headers(words: list) -> list[dict]:
    """找出優惠期間標題，回傳 [{x0, top, start, end, weekday}]。"""
    out = []
    for w in words:
        if not (w[T].startswith("優惠期間") or w[T].startswith("平日限定優惠")):
            continue
        line = sorted((v for v in words if abs(v[TOP] - w[TOP]) < 6 and 0 <= v[X0] - w[X0] < 520),
                      key=lambda v: v[X0])
        nums = [v[T] for v in line if v[T].isdigit()]
        # 找出 年 月 日 年 月 日
        for i in range(len(nums) - 5):
            y1, m1, d1, y2, m2, d2 = nums[i:i + 6]
            if len(y1) == 4 and len(y2) == 4:
                out.append({
                    "x0": w[X0], "top": w[TOP],
                    "start": f"{y1}-{int(m1):02d}-{int(d1):02d}",
                    "end": f"{y2}-{int(m2):02d}-{int(d2):02d}",
                    "weekday": w[T].startswith("平日限定"),
                })
                break
    return out


def _pick_header(headers: list[dict], x: float, page_w: float) -> dict | None:
    if not headers:
        return None
    half = x >= page_w / 2
    same = [h for h in headers if (h["x0"] >= page_w / 2) == half]
    pool = same or headers
    return min(pool, key=lambda h: abs(h["x0"] - x))


def _name(words: list, item_w, col_left: float, col_right: float) -> str:
    """商品名稱：ITEM 上方、同一欄的大字（中文品名，可能多行），加上第一行上方的品牌。"""
    in_col = [w for w in words if col_left - 3 <= w[X0] < col_right]
    names = [w for w in in_col if 11 <= w[SIZE] <= 13.5 and item_w[TOP] - 70 <= w[TOP] <= item_w[TOP] - 5]
    if not names:
        return ""
    first_top = min(w[TOP] for w in names)
    line = sorted(names, key=lambda w: (round(w[TOP] / 4), w[X0]))
    brand = sorted((w for w in in_col if 7 <= w[SIZE] <= 8 and first_top - 14 <= w[TOP] <= first_top - 1
                    and not any(k in w[T] for k in ("線上", "僅限", "好市多"))), key=lambda w: w[X0])
    text = " ".join(w[T] for w in brand) + " " + "".join(w[T] for w in line)
    return text.strip()


def parse_pages(pages: list[dict]) -> list[dict]:
    items = []
    for pi, page in enumerate(pages):
        words = page["words"]
        pw = page["w"]
        headers = _headers(words)
        page_online_only = any(w[T] == "專屬" for w in words) and any(w[T] == "線上" for w in words)
        # 以「-」號為錨點：右邊是折扣，同一欄（靠右對齊）上方是原價、下方是優惠後售價
        for minus in (w for w in words if w[T] == "-" and 12 <= w[SIZE] <= 24):
            discs = [w for w in words if _is_big_num(w) and abs(w[X0] - minus[X1]) < 4 and abs(w[TOP] - minus[TOP]) < 8]
            if not discs:
                continue
            disc_w = discs[0]
            same_col = [w for w in words if _is_big_num(w) and abs(w[X1] - disc_w[X1]) < 4 and w is not disc_w]
            above = [w for w in same_col if disc_w[TOP] - 40 < w[TOP] < disc_w[TOP] - 5]
            below_n = [w for w in same_col if disc_w[TOP] + 5 < w[TOP] < disc_w[TOP] + 45]
            if not above or not below_n:
                continue
            orig_w = max(above, key=lambda w: w[TOP])
            final = min(below_n, key=lambda w: w[TOP])
            original, discount, price = _num(orig_w[T]), _num(disc_w[T]), _num(final[T])
            if original - discount != price:
                continue
            # ITEM 必須在價格欄左邊，且兩者之間沒有別的價格欄（避免配到隔壁商品）
            other_cols = {round(w[X1]) for w in words if _is_big_num(w) and abs(w[X1] - final[X1]) >= 4
                          and abs(w[TOP] - disc_w[TOP]) < 60}
            cands = [w for w in words if w[T] == "ITEM" and 0 < final[X0] - w[X0] < 240
                     and not any(w[X0] < c < final[X0] - 10 for c in other_cols)
                     and orig_w[TOP] - 140 < w[TOP] < final[BOT] + 40]
            if not cands:
                continue
            mid = (orig_w[TOP] + final[TOP]) / 2
            item_w = min(cands, key=lambda w: abs(w[TOP] - mid))
            after = sorted((w for w in words if abs(w[TOP] - item_w[TOP]) < 2 and w[X0] > item_w[X0]),
                           key=lambda w: w[X0])
            codes = []
            for w in after:
                if w[T].isdigit() and w[SIZE] < 10:  # 編號是小字；大字是價格
                    codes.append(w[T])
                elif w[T] != "/":
                    break
            if not codes:
                continue
            # 通路標示：先看價格正下方；沒有的話，再看區塊上方有沒有「僅限好市多（線上購物）」
            in_x = lambda w: item_w[X0] - 5 <= w[X0] <= final[X1] + 10  # noqa: E731
            below = [w[T] for w in words if in_x(w) and final[TOP] < w[TOP] < final[BOT] + 45]
            above = [w[T] for w in words if in_x(w) and item_w[TOP] - 100 < w[TOP] < final[TOP]]
            if any("賣場售價" in t for t in below):
                channel = "store"
            elif any("僅限好市多" in t for t in below + above) or page_online_only:
                channel = "online"
            else:
                continue  # 判斷不出通路就不收錄，避免標錯
            online_also = channel == "online" or any("線上購物亦有優惠" in t for t in below)
            h = _pick_header(headers, item_w[X0], pw)
            items.append({
                "codes": codes,
                "name": _name(words, item_w, item_w[X0], final[X1] + 4),
                "original": original,
                "discount": discount,
                "price": price,
                "channel": channel,
                "online_also": online_also,
                "start": h["start"] if h else None,
                "end": h["end"] if h else None,
                "weekday_only": bool(h and h["weekday"]),
                "page": pi + 1,
            })
    # 同一商品可能因頁首頁尾重複而出現兩次：以（編號, 售價, 期間）去重
    seen, uniq = set(), []
    for it in items:
        key = (tuple(it["codes"]), it["price"], it["start"], it["end"], it["channel"])
        if key not in seen:
            seen.add(key)
            uniq.append(it)
    return uniq


def extract_words(pdf_bytes: bytes) -> list[dict]:
    import pdfplumber  # 只有在 GitHub Actions 上下載 PDF 時需要

    pages = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for p in pdf.pages:
            ws = p.extract_words(extra_attrs=["size"])
            pages.append({"w": p.width, "h": p.height,
                          "words": [[w["text"], w["x0"], w["x1"], w["top"], w["bottom"], w["size"]] for w in ws]})
    return pages


def main() -> int:
    """下載官網目前的會員護照 PDF → data/store_deals.json。

    PDF 約 18MB：每天只用 HEAD 檢查大小，檔案沒變就沿用上次的解析結果。
    任何失敗都保留舊資料、不中斷每日流程。
    """
    import os

    import requests

    out = "data/store_deals.json"
    site = "https://www.costco.com.tw"
    ua = "Mozilla/5.0 (compatible; tw-deal-tracker/1.0; coupon book, once per day)"
    s = requests.Session()
    s.headers.update({"User-Agent": ua})
    old = {}
    if os.path.exists(out):
        with open(out, encoding="utf-8") as f:
            old = json.load(f)
    old_by_src = {}
    for it in old.get("items", []):
        old_by_src.setdefault(it.get("source"), []).append(it)
    old_len = old.get("lengths", {})

    html = s.get(site + "/savings", timeout=60).text
    pdfs = sorted(set(re.findall(r"/mediapermalink/fy\d+p\d+-[\w-]*pdf", html)))
    if not pdfs:
        print("找不到會員護照 PDF 連結，保留舊資料。", file=sys.stderr)
        return 0
    all_items, lengths = [], {}
    for path in pdfs:
        url = site + path
        size = s.head(url, timeout=60, allow_redirects=True).headers.get("content-length")
        lengths[url] = size
        if size and old_len.get(url) == size and old_by_src.get(url):
            print(path, "檔案沒變，沿用上次結果", len(old_by_src[url]), "件")
            all_items.extend(old_by_src[url])
            continue
        r = s.get(url, timeout=180)
        r.raise_for_status()
        its = parse_pages(extract_words(r.content))
        for it in its:
            it["source"] = url
        print(path, "解析到", len(its), "件")
        all_items.extend(its)
    if not all_items:
        print("PDF 解析結果為 0 件，保留舊資料。", file=sys.stderr)
        return 0
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"sources": [site + p for p in pdfs], "lengths": lengths, "items": all_items},
                  f, ensure_ascii=False, separators=(",", ":"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""抓取好市多台灣線上商品價格（未登入可見的公開資料）。

只在 GitHub Actions 上執行（Claude 開發環境連不到官網）。
每頁 100 筆、頁與頁之間間隔數秒，降低對官網的負擔。
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

SITE = "https://www.costco.com.tw"
SEARCH_API = SITE + "/rest/v2/taiwan/products/search"
UA = "Mozilla/5.0 (compatible; tw-deal-tracker/1.0; daily price tracking, 1 run per day)"
PAGE_SIZE = 100


def _val(d) -> float | None:
    """從 {"value": 123.0, ...} 取出數值。"""
    if isinstance(d, dict) and d.get("value") is not None:
        try:
            return float(d["value"])
        except (TypeError, ValueError):
            return None
    return None


def _image(p: dict) -> str | None:
    for img in p.get("images") or []:
        if img.get("format") == "results-webp" and img.get("url"):
            return SITE + img["url"]
    for img in p.get("images") or []:
        if img.get("url"):
            return SITE + img["url"]
    return None


def normalize(p: dict) -> dict | None:
    """把官網的商品資料整理成本站使用的格式。

    官網欄位意義（2026-11-26 以實際資料驗證）：
    - basePrice：原價
    - price：目前售價（已扣除官方折扣）
    - discountPrice：官方折扣「金額」（不是折扣後價格）
    - discountStartDate / discountEndDate：官方折扣期間（UTC）
    """
    code = p.get("code")
    if not code:
        return None
    price = _val(p.get("price"))
    base = _val(p.get("basePrice"))
    if p.get("hidePriceValue"):
        price = None
    cd = p.get("couponDiscount") or {}
    promo = _val(p.get("discountPrice"))
    if promo is None and cd.get("discountValue") is not None:
        try:
            promo = float(cd["discountValue"])
        except (TypeError, ValueError):
            promo = None
    url = p.get("url") or ""
    full_url = SITE + url if url.startswith("/") else f"{SITE}/p/{code}"
    return {
        "code": str(code),
        "name": (p.get("name") or p.get("englishName") or "").strip(),
        "price": price,
        "base_price": base,
        "promo_amount": promo if promo and promo > 0 else None,
        "promo_start": p.get("discountStartDate") or cd.get("discountStartDate"),
        "promo_end": p.get("discountEndDate") or cd.get("discountEndDate"),
        "url": full_url,
        "image": _image(p),
        "rating": p.get("averageRating"),
        "reviews": p.get("numberOfReviews"),
        "group": p.get("deliveryName") or "",
        "available": bool(p.get("purchasable", True)),
    }


def fetch_page(session: requests.Session, page: int, retries: int = 3) -> dict:
    params = {"query": "", "pageSize": PAGE_SIZE, "currentPage": page, "lang": "zh_TW", "curr": "TWD"}
    last = None
    for attempt in range(retries):
        try:
            r = session.get(SEARCH_API, params=params, timeout=60)
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}"
        except (requests.RequestException, ValueError) as e:
            last = repr(e)
        time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"第 {page} 頁抓取失敗：{last}")


def fetch_all(delay: float = 2.0, max_pages: int | None = None) -> tuple[list[dict], dict]:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/json"})
    first = fetch_page(s, 0)
    pag = first.get("pagination") or {}
    total_pages = int(pag.get("totalPages") or 1)
    if max_pages:
        total_pages = min(total_pages, max_pages)
    raw = list(first.get("products") or [])
    for page in range(1, total_pages):
        time.sleep(delay)
        raw.extend(fetch_page(s, page).get("products") or [])
    seen: dict[str, dict] = {}
    for p in raw:
        n = normalize(p)
        if n and n["code"] not in seen:
            seen[n["code"]] = n
    meta = {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_results": pag.get("totalResults"),
        "pages": total_pages,
        "raw_count": len(raw),
        "unique_count": len(seen),
    }
    return list(seen.values()), meta


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else "data/snapshot.json"
    max_pages = int(os.environ["MAX_PAGES"]) if os.environ.get("MAX_PAGES") else None
    products, meta = fetch_all(max_pages=max_pages)
    print(json.dumps(meta, ensure_ascii=False))
    # 安全檢查：抓到的數量明顯不足時視為失敗，避免把不完整的資料寫進歷史
    expected = meta.get("total_results") or 0
    if not max_pages and expected and len(products) < expected * 0.8:
        print(f"抓到 {len(products)} 筆，少於官網總數 {expected} 的 80%，中止。", file=sys.stderr)
        return 1
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "products": products}, f, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())

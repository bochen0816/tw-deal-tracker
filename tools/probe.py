"""可行性探測（第二輪）：了解商品搜尋 API 的資料結構與分頁。

結果寫到 probe_out/ 並由 workflow 上傳成 artifact，方便離線分析。
"""
import json
import os
import sys
import time

import requests

UA = "Mozilla/5.0 (compatible; tw-deal-tracker/0.1; personal price research)"
BASE = "https://www.costco.com.tw/rest/v2/taiwan"
OUT = "probe_out"


def get(path: str, **params):
    r = requests.get(
        BASE + path,
        params=params,
        headers={"User-Agent": UA, "Accept": "application/json"},
        timeout=60,
    )
    print(r.status_code, r.url, len(r.content))
    return r


def main() -> int:
    os.makedirs(OUT, exist_ok=True)

    tests = {
        "search_p0_size100_full": dict(query="", pageSize=100, currentPage=0, fields="FULL", lang="zh_TW", curr="TWD"),
        "search_p1_size100_default": dict(query="", pageSize=100, currentPage=1, lang="zh_TW", curr="TWD"),
        "search_size500_default": dict(query="", pageSize=500, currentPage=0, lang="zh_TW", curr="TWD"),
    }
    for name, params in tests.items():
        r = get("/products/search", **params)
        with open(f"{OUT}/{name}.json", "wb") as f:
            f.write(r.content)
        try:
            data = r.json()
            print(name, "pagination:", json.dumps(data.get("pagination"), ensure_ascii=False))
            prods = data.get("products") or []
            print(name, "products in page:", len(prods))
            if prods:
                print(name, "product keys:", sorted(prods[0].keys()))
                if name == "search_p1_size100_default":
                    for p in prods[:2]:
                        print("SAMPLE_PRODUCT_BEGIN")
                        print(json.dumps(p, ensure_ascii=False, indent=1)[:6000])
                        print("SAMPLE_PRODUCT_END")
                    # 統計價格相關欄位出現情況
                    from collections import Counter
                    c = Counter()
                    for p in prods:
                        for k, v in p.items():
                            if any(s in k.lower() for s in ("price", "discount", "promo", "coupon", "saving")):
                                c[k] += 1
                    print("PRICE_FIELD_COUNTS", dict(c))
                    # 印出有折扣欄位的一個例子
                    for p in prods:
                        if any(k for k in p if "discount" in k.lower() or "coupon" in k.lower() or "promo" in k.lower()):
                            print("DISCOUNT_EXAMPLE_BEGIN")
                            print(json.dumps(p, ensure_ascii=False, indent=1)[:6000])
                            print("DISCOUNT_EXAMPLE_END")
                            break
            if name == "search_p0_size100_full":
                for f in data.get("facets", []):
                    print("FACET", f.get("name"), f.get("displayName"), [(v.get("name"), v.get("count")) for v in f.get("values", [])][:30])
                if prods:
                    print("FULL_SAMPLE_BEGIN")
                    print(json.dumps(prods[0], ensure_ascii=False, indent=1)[:8000])
                    print("FULL_SAMPLE_END")
        except Exception as e:  # noqa: BLE001
            print(name, "not json", repr(e))
        time.sleep(3)
    return 0


if __name__ == "__main__":
    sys.exit(main())

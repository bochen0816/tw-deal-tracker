"""可行性探測：在 GitHub Actions 主機上測試能否取得好市多台灣線上商品資料。

只做少量、低頻率的請求，並印出狀態碼與內容開頭，供判斷要用哪一種方式抓資料。
"""
import json
import sys
import time

import requests

UA = "Mozilla/5.0 (compatible; tw-deal-tracker/0.1; personal price research)"
BASE = "https://www.costco.com.tw"

CANDIDATES = [
    "/robots.txt",
    "/sitemap.xml",
    "/rest/v2/basesites?fields=FULL",
    "/rest/v2/taiwan/products/search?query=&pageSize=5&currentPage=0&fields=FULL",
    "/rest/v2/taiwan/catalogs?fields=BASIC",
    "/Costco/c/costco?page=0",
]


def probe(path: str) -> dict:
    url = BASE + path
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Accept": "*/*"}, timeout=30)
        body = r.text
        return {
            "url": url,
            "status": r.status_code,
            "content_type": r.headers.get("content-type"),
            "length": len(body),
            "server": r.headers.get("server"),
            "head": body[:1500],
        }
    except Exception as e:  # noqa: BLE001
        return {"url": url, "error": repr(e)}


def main() -> int:
    for p in CANDIDATES:
        res = probe(p)
        print("=" * 80)
        print(json.dumps({k: v for k, v in res.items() if k != "head"}, ensure_ascii=False))
        if "head" in res:
            print("--- body head ---")
            print(res["head"])
        time.sleep(3)
    return 0


if __name__ == "__main__":
    sys.exit(main())

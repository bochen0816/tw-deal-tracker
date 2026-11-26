"""探測會員護照（賣場優惠）資料來源：/savings 頁面、各檔期頁、PDF 文字。結果印在執行紀錄。"""
import io
import re
import sys
import time

import pdfplumber
import requests

SITE = "https://www.costco.com.tw"
UA = "Mozilla/5.0 (compatible; tw-deal-tracker/1.0; monthly coupon book check)"
S = requests.Session()
S.headers.update({"User-Agent": UA})


def get(path):
    r = S.get(SITE + path, timeout=60)
    print(f"GET {path} -> {r.status_code} {r.headers.get('content-type')} {len(r.content)}")
    time.sleep(2)
    return r


def main():
    r = get("/savings")
    links = sorted(set(re.findall(r'(/(?:content|mediapermalink)/fy\d+p\d+-[\w-]+)', r.text)))
    print("SAVINGS_LINKS", links)
    for path in links:
        if "pdf" not in path:
            pr = get(path)
            codes = sorted(set(re.findall(r'/p/(\d+)', pr.text)))
            print("PAGE", path, "codes:", len(codes), codes[:50])
    cands = [l for l in links if "pdf" in l]
    for p in ("fy27p3", "fy27p4"):
        for m in ("mvm1", "mvm2"):
            c = f"/mediapermalink/{p}-{m}-pdf"
            if c not in cands:
                cands.append(c)
    for path in cands:
        pr = get(path)
        if pr.status_code != 200 or b"%PDF" not in pr.content[:1024]:
            continue
        with pdfplumber.open(io.BytesIO(pr.content)) as pdf:
            print("PDF", path, "pages", len(pdf.pages))
            alltext = []
            for i, page in enumerate(pdf.pages):
                t = page.extract_text() or ""
                alltext.append(t)
            joined = "\n".join(alltext)
            print("PDF_ITEMS", path, "ITEM count:", len(re.findall(r"ITEM\s*\d+", joined)),
                  "賣場售價 count:", joined.count("賣場售價"), "線上 count:", joined.count("線上"))
            print("PDF_TEXT_BEGIN", path)
            print(joined[:6000])
            print("PDF_TEXT_END")
            # 第 3 頁的文字（含座標）以了解版面
            if len(pdf.pages) > 2:
                words = pdf.pages[2].extract_words()[:120]
                print("WORDS_P3", [(w["text"], round(w["x0"]), round(w["top"])) for w in words])
    return 0


if __name__ == "__main__":
    sys.exit(main())

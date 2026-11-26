"""把會員護照 PDF 每個字的位置存成 JSON（開發解析程式與測試用）。"""
import io
import json
import sys

import pdfplumber
import requests

UA = "Mozilla/5.0 (compatible; tw-deal-tracker/1.0; monthly coupon book check)"


def main():
    path = sys.argv[1]
    out = sys.argv[2]
    r = requests.get("https://www.costco.com.tw" + path, headers={"User-Agent": UA}, timeout=120)
    r.raise_for_status()
    pages = []
    with pdfplumber.open(io.BytesIO(r.content)) as pdf:
        for p in pdf.pages:
            words = p.extract_words(keep_blank_chars=False, use_text_flow=False, extra_attrs=["size"])
            pages.append({"w": round(p.width, 1), "h": round(p.height, 1),
                          "words": [[w["text"], round(w["x0"], 1), round(w["x1"], 1), round(w["top"], 1),
                                     round(w["bottom"], 1), round(w["size"], 1)] for w in words]})
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"source": path, "pages": pages}, f, ensure_ascii=False, separators=(",", ":"))
    print(out, sum(len(p["words"]) for p in pages), "words")


if __name__ == "__main__":
    main()

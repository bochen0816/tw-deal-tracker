"""核心邏輯測試：不連網，使用依 2026-11-26 實際官網資料欄位製作的範例。"""
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import build_site  # noqa: E402
import history  # noqa: E402
import scraper  # noqa: E402

RAW = {
    "code": "157668",
    "name": "Oasis Super Pro 桌上型多溫氣泡開飲機 IF-3FIZZ01K",
    "englishName": "Oasis Super Pro Tabletop",
    "basePrice": {"currencyIso": "TWD", "value": 15699.0},
    "price": {"currencyIso": "TWD", "value": 12499.0},
    "discountPrice": {"currencyIso": "TWD", "value": 3200.0},
    "discountStartDate": "2026-11-22T16:00:00.000Z",
    "discountEndDate": "2026-11-29T15:59:59.999Z",
    "couponDiscount": {"discountValue": 3200.0},
    "hidePriceValue": False,
    "url": "/Oasis/p/157668",
    "images": [{"format": "results-webp", "url": "/medias/x.webp"}],
    "deliveryName": "黑色購物週",
    "purchasable": True,
}
NOW = datetime(2026, 11, 26, 6, 0, tzinfo=timezone.utc)


def test_normalize_price_fields():
    n = scraper.normalize(RAW)
    assert n["price"] == 12499.0
    assert n["base_price"] == 15699.0
    assert n["promo_amount"] == 3200.0  # discountPrice 是折扣金額
    assert n["base_price"] - n["promo_amount"] == n["price"]
    assert n["url"] == "https://www.costco.com.tw/Oasis/p/157668"
    assert n["image"] == "https://www.costco.com.tw/medias/x.webp"


def test_normalize_hidden_price_and_missing_code():
    assert scraper.normalize({**RAW, "hidePriceValue": True})["price"] is None
    assert scraper.normalize({"name": "x"}) is None
    n = scraper.normalize({**RAW, "url": None, "discountPrice": None, "couponDiscount": None})
    assert n["url"] == "https://www.costco.com.tw/p/157668"
    assert n["promo_amount"] is None


def test_history_update_new_change_and_unchanged():
    p = scraper.normalize(RAW)
    s1, rows = history.update({}, [p], "2026-11-24")
    assert rows == [("2026-11-24", "157668", 12499.0)]
    assert s1["157668"]["min_price"] == 12499.0 and s1["157668"]["prev_price"] is None
    s2, rows = history.update(s1, [p], "2026-11-25")
    assert rows == []  # 價格沒變不記錄
    cheaper = {**p, "price": 11999.0}
    s3, rows = history.update(s2, [cheaper], "2026-11-26")
    assert rows == [("2026-11-26", "157668", 11999.0)]
    r = s3["157668"]
    assert r["prev_price"] == 12499.0 and r["changed_on"] == "2026-11-26"
    assert r["min_price"] == 11999.0 and r["max_price"] == 12499.0
    assert s1["157668"]["price"] == 12499.0  # 原狀態未被修改


def test_compute_deals_promo_and_drop():
    p = scraper.normalize(RAW)
    state, _ = history.update({}, [p], "2026-11-26")
    deals = build_site.compute_deals(state, "2026-11-26", NOW)
    assert len(deals) == 1 and deals[0]["ap"] == 20.4 and deals[0]["d"] is None
    # 活動結束後不算官方特價
    later = datetime(2026, 11, 30, tzinfo=timezone.utc)
    assert build_site.compute_deals(state, "2026-11-26", later) == []
    # 本站偵測到的降價（無官方活動）
    nopromo = {**p, "promo_amount": None}
    s, _ = history.update({}, [nopromo], "2026-11-20")
    s, _ = history.update(s, [{**nopromo, "price": 10000.0}], "2026-11-24")
    s, _ = history.update(s, [{**nopromo, "price": 10000.0}], "2026-11-26")  # 今天仍在架上、價格不變
    d = build_site.compute_deals(s, "2026-11-26", NOW)
    assert d[0]["d"] == 20.0 and d[0]["low"] is True and d[0]["dc"] == "2026-11-24"
    # 超過 7 天的降價不列入
    s, _ = history.update(s, [{**nopromo, "price": 10000.0}], "2026-12-05")
    assert build_site.compute_deals(s, "2026-12-05", NOW) == []


def test_compute_deals_skips_missing_today():
    p = scraper.normalize(RAW)
    state, _ = history.update({}, [p], "2026-11-25")
    assert build_site.compute_deals(state, "2026-11-26", NOW) == []


def test_build_site_outputs_and_escaping(tmp_path):
    evil = {**RAW, "code": "999", "name": "</script><script>alert(1)</script>&好"}
    prods = [scraper.normalize(RAW), scraper.normalize(evil)]
    state, rows = history.update({}, prods, "2026-11-26")
    hist = {}
    for d, c, v in rows:
        hist.setdefault(c, []).append((d, v))
    out = tmp_path / "site"
    stats = build_site.build(state, hist, {"date_tw": "2026-11-26"}, str(out), now=NOW)
    assert stats == {"deals": 2, "products": 2, "listed": 2}
    index = (out / "index.html").read_text(encoding="utf-8")
    assert index.count("</script>") == 3  # 只有本站自己的三個 script（特價資料、分類、程式）
    assert "<script>alert(1)" not in index
    assert "非官方" in index
    prod = (out / "p" / "999.html").read_text(encoding="utf-8")
    assert "<script>alert" not in prod and "&lt;/script&gt;" in prod
    assert "<svg" in (out / "p" / "157668.html").read_text(encoding="utf-8")
    assert len(json.loads((out / "all.json").read_text(encoding="utf-8"))) == 2
    assert "157668.html" in (out / "sitemap.xml").read_text(encoding="utf-8")
    assert (out / ".nojekyll").exists() and (out / "style.css").exists()
    assert "style.css" in index and "<style>" not in index


def test_svg_chart_single_point():
    svg = build_site.svg_chart([("2026-11-26", 100.0)], "2026-11-26")
    assert svg.startswith("<svg") and "$100" in svg
    # 第一天線段必須有長度（曾經發生只有一天資料時線條看不見）
    import re
    d = re.search(r"d='M([\d.]+),[\d.]+H([\d.]+)", svg)
    assert d and float(d.group(2)) - float(d.group(1)) > 100


def test_category_key_from_url():
    f = build_site.category_key
    assert f("https://www.costco.com.tw/Sports-Lifestyle/Camping-Outdoors/Coolers/Igloo/p/1872201") == "Sports-Lifestyle"
    assert f("https://www.costco.com.tw/Food-Dining/x/p/1") == "Food-Dining"
    assert f("https://www.costco.com.tw/Penfolds/p/1") == "other"  # 品牌頁等非主分類
    assert f("https://www.costco.com.tw/p/1") == "other"
    assert f(None) == "other"


def test_verdict_texts():
    p = scraper.normalize(RAW)
    state, _ = history.update({}, [p], "2026-11-26")
    say, pts = build_site.verdict(state["157668"], "2026-11-26", NOW)
    assert say == "正在特價" and "$3,200" in pts[0] and "只有 1 天資料" in pts[-1]
    # 下架
    say, _ = build_site.verdict(state["157668"], "2026-11-27", NOW)
    assert "沒有列出" in say
    # 追蹤夠久、比最低價貴
    r = {**state["157668"], "promo_amount": None, "first_seen": "2026-10-01", "min_price": 11000.0, "max_price": 12499.0}
    say, pts = build_site.verdict(r, "2026-11-26", NOW)
    assert say == "目前是一般價格" and "貴 $1,499" in pts[-1]
    # 追蹤夠久、是最低價
    r = {**r, "min_price": 12499.0, "max_price": 13000.0}
    say, _ = build_site.verdict(r, "2026-11-26", NOW)
    assert say == "本站記錄最低價"


def test_product_page_related_and_search_data(tmp_path):
    a = scraper.normalize(RAW)
    b = scraper.normalize({**RAW, "code": "2", "name": "同類商品", "url": "/Oasis/p/2"})
    c = scraper.normalize({**RAW, "code": "3", "name": "沒特價", "discountPrice": None, "couponDiscount": None})
    state, rows = history.update({}, [a, b, c], "2026-11-26")
    out = tmp_path / "site"
    build_site.build(state, {}, {"date_tw": "2026-11-26"}, str(out), now=NOW)
    page_a = (out / "p" / "157668.html").read_text(encoding="utf-8")
    assert "現在買划算嗎" in page_a and "到好市多官網購買" in page_a and "剩 4 天" in page_a
    allj = {d["c"]: d for d in json.loads((out / "all.json").read_text(encoding="utf-8"))}
    assert allj["157668"]["a"] == 3200.0 and allj["3"]["a"] is None and "k" in allj["3"]
    index = (out / "index.html").read_text(encoding="utf-8")
    assert "今日特價明細" in index and "共檢查商品" in index

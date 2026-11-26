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
    assert index.count("</script>") == 2  # 只有本站自己的兩個 script 結尾
    assert "<script>alert(1)" not in index
    assert "非官方" in index
    prod = (out / "p" / "999.html").read_text(encoding="utf-8")
    assert "<script>alert" not in prod and "&lt;/script&gt;" in prod
    assert "<svg" in (out / "p" / "157668.html").read_text(encoding="utf-8")
    assert len(json.loads((out / "all.json").read_text(encoding="utf-8"))) == 2
    assert "157668.html" in (out / "sitemap.xml").read_text(encoding="utf-8")
    assert (out / ".nojekyll").exists()


def test_svg_chart_single_point():
    svg = build_site.svg_chart([("2026-11-26", 100.0)], "2026-11-26")
    assert svg.startswith("<svg") and "$100" in svg

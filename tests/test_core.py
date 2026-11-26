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
    assert stats == {"deals": 2, "store": 0, "products": 2, "listed": 2}
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
    assert say == "線上特價中" and "$3,200" in pts[0] and "只有 1 天資料" in pts[-1]
    # 下架
    say, _ = build_site.verdict(state["157668"], "2026-11-27", NOW)
    assert "沒有列出" in say
    # 追蹤夠久、比最低價貴
    r = {**state["157668"], "promo_amount": None, "first_seen": "2026-10-01", "min_price": 11000.0, "max_price": 12499.0}
    say, pts = build_site.verdict(r, "2026-11-26", NOW)
    assert say == "線上是一般價格" and "貴 $1,499" in pts[-1]
    # 追蹤夠久、是最低價
    r = {**r, "min_price": 12499.0, "max_price": 13000.0}
    say, _ = build_site.verdict(r, "2026-11-26", NOW)
    assert say == "線上是本站記錄最低價"


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


def test_index_ids_unique(tmp_path):
    """曾發生：分類按鈕區與分類資料用了同一個 id，導致清單完全顯示不出來。"""
    import re
    from collections import Counter
    state, _ = history.update({}, [scraper.normalize(RAW)], "2026-11-26")
    out = tmp_path / "site"
    build_site.build(state, {}, {"date_tw": "2026-11-26", "unique_count": 10083}, str(out), now=NOW)
    index = (out / "index.html").read_text(encoding="utf-8")
    ids = Counter(re.findall(r'\bid="([^"]+)"', index))
    assert [k for k, v in ids.items() if v > 1] == []
    # 程式裡用到的 id 都要存在
    js_ids = set(re.findall(r"getElementById\('([^']+)'\)", index)) | set(re.findall(r"\$\('#([\w-]+)'\)", index))
    assert js_ids <= set(ids), js_ids - set(ids)
    assert "10,083 件" in index


def _store_item(**kw):
    base = {"codes": ["157668"], "name": "PDF 品名", "original": 4389, "discount": 900, "price": 3489,
            "channel": "store", "online_also": True, "start": "2026-11-30", "end": "2026-12-20", "weekday_only": False}
    base.update(kw)
    return base


def test_compute_store_deals_status_and_filters():
    state, _ = history.update({}, [scraper.normalize(RAW)], "2026-11-26")
    items = [
        _store_item(),                                                      # 即將開始
        _store_item(codes=["555"], start="2026-11-01", end="2026-11-22"),   # 已結束 → 不收
        _store_item(codes=["777"], channel="online"),                       # 線上限定 → 不在賣場清單
        _store_item(codes=["888"], start="2026-11-20", end="2026-11-28", weekday_only=True),  # 進行中
    ]
    out = {d["c"]: d for d in build_site.compute_store_deals(items, state, "2026-11-26")}
    assert set(out) == {"157668", "888"}
    a = out["157668"]
    assert a["st"] == "next" and a["n"] == RAW["name"] and a["pg"] is True and a["ap"] == 20.5
    assert a["e"] == "2026-12-20T15:59:59Z"  # 台灣時間 12/20 23:59:59
    b = out["888"]
    assert b["st"] == "now" and b["wk"] is True and b["pg"] is False and b["n"] == "PDF 品名"


def test_build_with_store_deals(tmp_path):
    state, _ = history.update({}, [scraper.normalize(RAW)], "2026-11-26")
    out = tmp_path / "site"
    stats = build_site.build(state, {}, {"date_tw": "2026-11-26"}, str(out), now=NOW,
                             store_items=[_store_item(), _store_item(codes=["888"])])
    assert stats["store"] == 2
    index = (out / "index.html").read_text(encoding="utf-8")
    assert 'data-ch="s"' in index and "賣場特價（11/30 起）" in index
    page = (out / "p" / "157668.html").read_text(encoding="utf-8")
    assert "storebox" in page and "賣場特價11/30 開始" in page and "$3,489" in page


def test_store_discount_corrected_by_online_same_period():
    """官網 11/30 檔期頁寫省 $860、PDF 印 $850：線上同檔期折扣上線後以線上金額校正。"""
    online = {**RAW, "discountPrice": {"value": 860.0}, "couponDiscount": None,
              "discountStartDate": "2026-11-29T16:00:00.000Z", "discountEndDate": "2026-12-20T15:59:59.999Z"}
    state, _ = history.update({}, [scraper.normalize(online)], "2026-11-30")
    item = _store_item(original=3999, discount=850, price=3149)
    d = build_site.compute_store_deals([item], state, "2026-11-30")[0]
    assert (d["a"], d["p"], d["fx"]) == (860.0, 3139.0, 850)
    # 檔期不同（例如黑色購物週的線上折扣）就不校正
    other = {**online, "discountStartDate": "2026-11-22T16:00:00.000Z", "discountEndDate": "2026-11-29T15:59:59.999Z"}
    state2, _ = history.update({}, [scraper.normalize(other)], "2026-11-26")
    d2 = build_site.compute_store_deals([item], state2, "2026-11-26")[0]
    assert (d2["a"], d2["p"], d2["fx"]) == (850, 3149, None)
    box = build_site.store_box(d)
    assert "PDF 印的折扣是 $850" in box

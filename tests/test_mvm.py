"""會員護照 PDF 解析測試：使用 fy27p3-mvm2 實際 PDF 的文字座標（tests/fixtures）。

期望值皆由人工對照 PDF 內容確認。
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import mvm  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "mvm_fy27p3_mvm2_words.json")


def _items():
    with open(FIX, encoding="utf-8") as f:
        return mvm.parse_pages(json.load(f)["pages"])


def _by_code():
    return {c: i for i in _items() for c in i["codes"]}


def test_all_prices_consistent_and_coverage():
    items = _items()
    assert len(items) >= 120  # PDF 上約 129 個 ITEM，少數特殊版面不收
    for i in items:
        assert i["original"] - i["discount"] == i["price"]
        assert i["channel"] in ("store", "online")
        assert i["start"] and i["end"] and i["start"] <= i["end"]
    codes = [c for i in items for c in i["codes"]]
    assert len(codes) == len(set(codes))


def test_store_item_standard_layout():
    i = _by_code()["157741"]  # 飛利浦電鬍刀
    assert (i["original"], i["discount"], i["price"]) == (4389, 900, 3489)
    assert i["channel"] == "store" and i["online_also"] is True
    assert (i["start"], i["end"], i["weekday_only"]) == ("2026-10-26", "2026-11-22", False)
    assert "飛利浦" in i["name"] and "伊萊克斯" not in i["name"]


def test_compact_weekday_layout_pairs_correct_item():
    """緊密版面：兩件商品並排，曾把冰棒的價格配給貓飼料。"""
    b = _by_code()
    assert (b["138794"]["price"], b["138794"]["original"]) == (479, 599) and "貓" in b["138794"]["name"]
    assert (b["64155"]["price"], b["64155"]["original"]) == (299, 389) and "雪糕" in b["64155"]["name"]
    assert b["138794"]["weekday_only"] is True and b["138794"]["end"] == "2026-11-20"
    assert b["1789710"]["codes"] == ["1789710"]  # 不可把同列的折扣 200 當成編號


def test_online_only_items():
    b = _by_code()
    assert b["154795"]["channel"] == "online" and b["154795"]["price"] == 919  # 五月花，僅限線上
    assert b["152185"]["channel"] == "online"  # 標示在區塊上方
    w = b["152411"]  # 線上專屬頁（11/27–12/06）
    assert (w["channel"], w["start"], w["end"], w["price"]) == ("online", "2026-11-27", "2026-12-06", 25599)
    assert "線上購物" not in b["154795"]["name"]


def test_tv_not_excluded_by_other_rows():
    """曾因其他列的價格欄位擋住而漏掉。"""
    assert _by_code()["153555"]["price"] == 27799

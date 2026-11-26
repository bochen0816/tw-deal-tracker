"""價格歷史：把當天抓到的資料合併進長期狀態檔與歷史紀錄。

- data/products.json：每個商品的最新狀態（含歷史最低、上次價格、價格變動日）
- data/history.csv：只有在「新商品」或「價格有變」時才新增一列（date,code,price）
"""
from __future__ import annotations

import csv
import json
import os
import sys
from datetime import datetime, timedelta, timezone

TW = timezone(timedelta(hours=8))


def today_tw() -> str:
    return datetime.now(TW).strftime("%Y-%m-%d")


def load_state(path: str) -> dict:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def update(state: dict, products: list[dict], today: str) -> tuple[dict, list[tuple[str, str, float]]]:
    """回傳（新的狀態, 要新增到 history.csv 的列）。不修改傳入的 state。"""
    new_state = {k: dict(v) for k, v in state.items()}
    rows: list[tuple[str, str, float]] = []
    for p in products:
        code = p["code"]
        price = p.get("price")
        old = new_state.get(code)
        rec = dict(old) if old else {"first_seen": today, "min_price": None, "max_price": None,
                                      "prev_price": None, "changed_on": None}
        for k in ("name", "url", "image", "base_price", "promo_amount", "promo_start", "promo_end",
                  "rating", "reviews", "group", "available"):
            rec[k] = p.get(k)
        rec["last_seen"] = today
        if price is not None:
            old_price = old.get("price") if old else None
            if old_price is None:
                rows.append((today, code, price))
            elif price != old_price:
                rec["prev_price"] = old_price
                rec["changed_on"] = today
                rows.append((today, code, price))
            rec["price"] = price
            rec["min_price"] = price if rec.get("min_price") is None else min(rec["min_price"], price)
            rec["max_price"] = price if rec.get("max_price") is None else max(rec["max_price"], price)
        elif old is None:
            rec["price"] = None
        new_state[code] = rec
    return new_state, rows


def main() -> int:
    snapshot_path = sys.argv[1] if len(sys.argv) > 1 else "data/snapshot.json"
    state_path = "data/products.json"
    hist_path = "data/history.csv"
    with open(snapshot_path, encoding="utf-8") as f:
        snap = json.load(f)
    today = today_tw()
    state, rows = update(load_state(state_path), snap["products"], today)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    new_file = not os.path.exists(hist_path)
    with open(hist_path, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["date", "code", "price"])
        for r in rows:
            w.writerow([r[0], r[1], f"{r[2]:g}"])
    with open("data/last_run.json", "w", encoding="utf-8") as f:
        json.dump({**snap["meta"], "date_tw": today, "history_rows_added": len(rows)}, f, ensure_ascii=False)
    print(f"{today}: 商品 {len(state)} 筆，新增歷史 {len(rows)} 列")
    return 0


if __name__ == "__main__":
    sys.exit(main())

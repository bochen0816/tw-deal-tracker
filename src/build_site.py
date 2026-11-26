"""產生靜態網站到 site/。

- index.html：今日降價清單（官方特價中＋近 7 天本站偵測到的降價），可排序、篩選、搜尋
- p/<code>.html：商品頁（目前價格、原價、歷史最低、價格走勢圖、前往官網）
- all.json：全商品精簡資料（搜尋用）
- sitemap.xml、robots.txt
"""
from __future__ import annotations

import csv
import html
import json
import math
import os
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

TW = timezone(timedelta(hours=8))
SITE_NAME = os.environ.get("SITE_NAME", "降價雷達")
SITE_BASE = os.environ.get("SITE_BASE", "https://bochen0816.github.io/tw-deal-tracker").rstrip("/")
RECENT_DAYS = 7

DISCLAIMER = (
    "本站為個人製作的非官方網站，與好市多（Costco）無任何關係。"
    "價格資料每日自動更新一次，可能與官網即時價格有落差，實際價格與活動條件一律以官網為準。"
)


def esc(s) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def money(v) -> str:
    if v is None:
        return "—"
    return f"${v:,.0f}"


def parse_utc(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def fmt_end(s: str | None) -> str:
    d = parse_utc(s)
    return d.astimezone(TW).strftime("%m/%d %H:%M") if d else ""


def safe_json(obj) -> str:
    """嵌入 <script> 用：避免 </script> 等字串提前結束標籤。"""
    return (json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def compute_deals(state: dict, today: str, now: datetime) -> list[dict]:
    today_d = datetime.strptime(today, "%Y-%m-%d").date()
    deals = []
    for code, r in state.items():
        price = r.get("price")
        if r.get("last_seen") != today or price is None:
            continue
        base = r.get("base_price")
        promo = r.get("promo_amount")
        end = parse_utc(r.get("promo_end"))
        promo_active = bool(promo) and (end is None or end > now)
        drop_pct = None
        prev = r.get("prev_price")
        changed = r.get("changed_on")
        if prev and changed and price < prev:
            days = (today_d - datetime.strptime(changed, "%Y-%m-%d").date()).days
            if 0 <= days < RECENT_DAYS:
                drop_pct = round((prev - price) / prev * 100, 1)
        if not promo_active and drop_pct is None:
            continue
        promo_pct = round(promo / base * 100, 1) if promo_active and base else None
        is_low = r.get("max_price") is not None and r.get("min_price") == price and r["max_price"] > price
        deals.append({
            "c": code, "n": r.get("name") or "", "p": price, "b": base,
            "a": promo if promo_active else None, "ap": promo_pct,
            "e": r.get("promo_end") if promo_active else None, "ef": fmt_end(r.get("promo_end")) if promo_active else "",
            "pp": prev if drop_pct is not None else None, "d": drop_pct, "dc": changed if drop_pct is not None else None,
            "low": is_low, "g": r.get("group") or "", "k": category_key(r.get("url")), "ch": "o",
        })
    deals.sort(key=lambda x: -(max(x["ap"] or 0, x["d"] or 0)))
    return deals


def compute_store_deals(items: list[dict], state: dict, today: str) -> list[dict]:
    """會員護照的賣場特價 → 與線上特價相同格式的清單（ch="s"）。只收尚未結束的。"""
    best: dict[str, dict] = {}
    for it in items:
        if it.get("channel") != "store" or not it.get("end") or it["end"] < today:
            continue
        code = next((c for c in it["codes"] if c in state), it["codes"][0])
        r = state.get(code) or {}
        status = "now" if it["start"] <= today else "next"
        d = {
            "ch": "s", "c": code, "n": r.get("name") or it.get("name") or f"商品 {code}",
            "p": it["price"], "b": it["original"], "a": it["discount"],
            "ap": round(it["discount"] / it["original"] * 100, 1) if it["original"] else None,
            "e": f"{it['end']}T15:59:59Z",  # 台灣時間當天 23:59:59
            "sd": it["start"], "ed": it["end"], "st": status, "wk": bool(it.get("weekday_only")),
            "oa": bool(it.get("online_also")), "k": category_key(r.get("url")), "pg": bool(r),
            "pp": None, "d": None, "dc": None, "low": False, "g": "",
        }
        old = best.get(code)
        # 同一商品有多個檔期：優先顯示進行中的，其次最早開始的
        if old is None or (d["st"], d["sd"]) < (old["st"], old["sd"]):
            best[code] = d
    deals = list(best.values())
    deals.sort(key=lambda x: -(x["ap"] or 0))
    return deals


def load_history(path: str) -> dict[str, list[tuple[str, float]]]:
    h: dict[str, list[tuple[str, float]]] = defaultdict(list)
    if not os.path.exists(path):
        return h
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                h[row["code"]].append((row["date"], float(row["price"])))
            except (KeyError, ValueError):
                continue
    return h


def svg_chart(points: list[tuple[str, float]], today: str, width: int = 640, height: int = 200) -> str:
    """階梯式價格走勢圖（價格維持到下一次變動）。"""
    if not points:
        return "<p class='muted'>尚無價格紀錄。</p>"
    pts = sorted(points)
    if pts[-1][0] < today:
        pts = pts + [(today, pts[-1][1])]
    if len(pts) == 1:
        pts = pts + [(today, pts[0][1])]
    d0 = datetime.strptime(pts[0][0], "%Y-%m-%d")
    d1 = datetime.strptime(pts[-1][0], "%Y-%m-%d")
    span = max((d1 - d0).days, 1)
    prices = [p for _, p in pts]
    lo, hi = min(prices), max(prices)
    pad = (hi - lo) * 0.15 or max(hi * 0.05, 1)
    lo, hi = lo - pad, hi + pad
    L, R, T, B = 70, 16, 14, 30
    W, H = width - L - R, height - T - B

    def y(v):
        return T + (hi - v) / (hi - lo) * H

    if d0 == d1:
        # 只有一天資料：畫一條從左到右的水平線（否則線段長度為 0 看不見）
        xs = [L, L + W]
    else:
        xs = [L + (datetime.strptime(ds, "%Y-%m-%d") - d0).days / span * W for ds, _ in pts]
    path = [f"M{xs[0]:.1f},{y(pts[0][1]):.1f}"]
    for xv, (_, v) in zip(xs[1:], pts[1:]):
        path.append(f"H{xv:.1f}V{y(v):.1f}")
    mn, mx = min(prices), max(prices)
    labels = [(mx, money(mx))] if mn == mx else [(mx, money(mx)), (mn, money(mn))]
    ylab = "".join(
        f"<text x='{L - 8}' y='{y(v) + 4:.1f}' text-anchor='end' class='ax'>{esc(t)}</text>"
        f"<line x1='{L}' x2='{L + W}' y1='{y(v):.1f}' y2='{y(v):.1f}' class='grid'/>" for v, t in labels
    )
    xlab = (f"<text x='{L}' y='{height - 8}' class='ax'>{esc(pts[0][0])}</text>"
            f"<text x='{L + W}' y='{height - 8}' text-anchor='end' class='ax'>{esc(pts[-1][0])}</text>")
    return (f"<svg viewBox='0 0 {width} {height}' class='chart' role='img' aria-label='價格走勢圖'>"
            f"{ylab}{xlab}<path d='{''.join(path)}' class='line'/></svg>")


CAT_LABELS = {
    "Food-Dining": "美食飲品",
    "Furniture-Kitchen": "家具廚房",
    "Televisions-Appliances": "家電電視",
    "Digital-Mobile": "3C 數位",
    "Health-Beauty": "保健美妝",
    "Clothing-Accessories": "服飾配件",
    "Household-Baby-Toys": "居家婦幼",
    "Sports-Lifestyle": "運動休閒",
    "Jewelry-Gold": "珠寶黃金",
    "Office-School": "辦公文具",
    "Lawn-Garden": "戶外庭園",
    "Tire": "汽車輪胎",
}


def category_key(url: str | None) -> str:
    """從官網商品網址取第一層分類，例如 /Food-Dining/.../p/123 → Food-Dining。"""
    if not url:
        return "other"
    path = url.split("costco.com.tw/", 1)[-1].split("/")
    if "p" in path:
        path = path[: path.index("p")]
    key = path[0] if len(path) > 1 else ""
    return key if key in CAT_LABELS else "other"


CAT_LABELS_ALL = {**CAT_LABELS, "other": "其他"}

FONTS = ("https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600;700"
         "&family=Noto+Sans+TC:wght@400;500;700;900&display=swap")

CSS = """
:root{--concrete:#E9ECEF;--paper:#FFFFFF;--ink:#1A1F2B;--muted:#5B6372;--line:#D5DAE0;
--tag:#FFD84D;--tag-edge:#E8BE22;--save:#0E7C4A;--save-bg:#E3F4EA;--urgent:#C2410C;
--num:"Barlow Condensed","Noto Sans TC",sans-serif}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--concrete);color:var(--ink);font:16px/1.65 "Noto Sans TC",system-ui,sans-serif;-webkit-font-smoothing:antialiased}
a{color:inherit}
:focus-visible{outline:3px solid var(--ink);outline-offset:2px}
.wrap{max-width:1120px;margin:0 auto;padding:0 20px}
.top{display:flex;align-items:center;justify-content:space-between;padding:16px 0}
.brand{font-weight:900;font-size:1.25rem;text-decoration:none;display:flex;align-items:center;gap:8px}
.brand i{width:14px;height:20px;background:var(--tag);border:2px solid var(--ink);border-radius:3px;display:inline-block;transform:rotate(-8deg)}
.top nav a{color:var(--muted);text-decoration:none;margin-left:18px;font-size:.95rem}
.top nav a:hover{color:var(--ink)}

/* 開場 */
.hero{display:grid;grid-template-columns:1.25fr 1fr;gap:48px;align-items:center;padding:36px 0 48px}
.hero h1{font-size:clamp(2rem,4.4vw,3.2rem);line-height:1.2;font-weight:900;margin:0 0 16px;letter-spacing:.01em}
.hero p{font-size:1.1rem;color:var(--muted);margin:0 0 28px;max-width:30em}
.cta{display:inline-block;background:var(--ink);color:#fff;text-decoration:none;font-weight:700;padding:14px 26px;border-radius:6px}
.cta:hover{background:#000}
.receipt{background:var(--paper);padding:26px 26px 34px;position:relative;box-shadow:0 18px 30px -18px rgba(26,31,43,.35);
transform:rotate(1.2deg);animation:print .7s ease-out both}
.receipt::after{content:"";position:absolute;left:0;right:0;bottom:-10px;height:10px;
background:linear-gradient(-45deg,transparent 7px,var(--paper) 0) 0 0/14px 10px,linear-gradient(45deg,transparent 7px,var(--paper) 0) 0 0/14px 10px}
@keyframes print{from{clip-path:inset(0 0 100% 0)}to{clip-path:inset(0 0 -20px 0)}}
.receipt h2{font-size:1rem;margin:0;text-align:center;font-weight:700}
.receipt .date{text-align:center;color:var(--muted);font-size:.85rem;margin-bottom:14px}
.receipt dl{margin:0;border-top:2px dashed var(--line);border-bottom:2px dashed var(--line);padding:10px 0}
.receipt dl div{display:flex;justify-content:space-between;align-items:baseline;padding:5px 0}
.receipt dt{color:var(--muted)}
.receipt dd{margin:0;font:700 1.5rem/1 var(--num)}
.receipt .total{display:flex;justify-content:space-between;padding-top:12px;font-weight:700}
.receipt .total span:last-child{font:700 1.5rem/1 var(--num)}

/* 工具列 */
.tools{position:sticky;top:0;z-index:5;background:var(--concrete);padding:12px 0 10px;border-bottom:1px solid var(--line)}
.search{display:flex;gap:10px}
.search input{flex:1;font:inherit;font-size:1.05rem;padding:12px 16px;border:2px solid var(--ink);border-radius:6px;background:var(--paper);color:var(--ink);min-width:0}
.search select{font:inherit;padding:0 12px;border:1px solid var(--line);border-radius:6px;background:var(--paper);color:var(--ink)}
.scopes{display:flex;gap:4px;margin-top:12px;border-bottom:2px solid var(--line)}
.scopes button{font:inherit;font-weight:700;background:none;border:0;padding:8px 14px;color:var(--muted);cursor:pointer;border-bottom:3px solid transparent;margin-bottom:-2px}
.scopes button[aria-pressed=true]{color:var(--ink);border-bottom-color:var(--ink)}
.scopes small{font:600 .95rem var(--num);margin-left:4px}
.cats{display:flex;gap:8px;overflow-x:auto;padding:12px 0 2px;scrollbar-width:none}
.cats::-webkit-scrollbar{display:none}
.cats button{flex:none;font:inherit;font-size:.92rem;background:var(--paper);border:1px solid var(--line);border-radius:999px;padding:6px 14px;cursor:pointer;color:var(--ink)}
.cats button[aria-pressed=true]{background:var(--ink);border-color:var(--ink);color:#fff}
.cats small{font:600 .9rem var(--num);opacity:.7;margin-left:4px}
.status{display:flex;justify-content:space-between;align-items:center;color:var(--muted);font-size:.92rem;margin:16px 0 12px;gap:12px}
.status button{font:inherit;background:none;border:0;color:var(--ink);text-decoration:underline;cursor:pointer;padding:0}

.channels{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px}
.channels button{font:inherit;font-size:1.1rem;font-weight:900;padding:12px;border:2px solid var(--ink);border-radius:6px;background:var(--paper);color:var(--ink);cursor:pointer}
.channels button[aria-pressed=true]{background:var(--ink);color:#fff}
.channels small{font:700 1.1rem var(--num);margin-left:8px;opacity:.75}
.storebox{background:var(--paper);border:2px solid var(--save);border-radius:6px;padding:18px 20px;margin-bottom:20px}
.storebox h2{font-size:1rem;margin:0 0 6px;color:var(--save)}
.storebox .now{font-size:2.6rem}
.storebox s{font:600 1.2rem var(--num);color:var(--muted);margin-left:10px}
.lbl{margin:0 0 6px;font-weight:700;color:var(--muted);font-size:.92rem}

/* 價格牌 */
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(232px,1fr));gap:16px}
.tag{display:flex;flex-direction:column;background:var(--paper);border-radius:6px;text-decoration:none;overflow:hidden;border:1px solid var(--line)}
.tag:hover{border-color:var(--ink)}
.tag-name{padding:14px 16px 12px;font-weight:500;line-height:1.45;flex:1;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;min-height:5.6em}
.tag-label{background:var(--tag);border-top:2px solid var(--tag-edge);padding:10px 16px 8px;display:flex;align-items:baseline;gap:10px;position:relative}
.tag-label::before{content:"";position:absolute;right:14px;top:12px;width:9px;height:9px;border-radius:50%;background:var(--concrete);box-shadow:inset 0 1px 2px rgba(0,0,0,.25)}
.now{font:700 2.1rem/1 var(--num);letter-spacing:.01em}
.tag s{color:#6E5A12;font:600 1.05rem var(--num)}
.tag-foot{background:var(--tag);padding:0 16px 12px;display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center;min-height:38px}
.save{background:var(--save);color:#fff;font-weight:700;font-size:.85rem;padding:3px 9px;border-radius:4px}
.save b{font:700 1rem var(--num);margin-left:2px}
.note{font-size:.85rem;font-weight:700;color:#6E5A12}
.end{font-size:.85rem;font-weight:700;color:#5C4B0A}
.end.urgent{color:var(--urgent)}
.more{display:block;margin:24px auto 0;font:inherit;font-weight:700;background:var(--paper);border:2px solid var(--ink);border-radius:6px;padding:12px 28px;cursor:pointer;color:var(--ink)}
.empty{background:var(--paper);border-radius:6px;padding:32px;text-align:center;grid-column:1/-1}
.empty button{margin-top:12px;font:inherit;font-weight:700;background:var(--ink);color:#fff;border:0;border-radius:6px;padding:10px 20px;cursor:pointer}

/* 說明區 */
.section{padding:64px 0 0}
.section h2{font-size:1.6rem;font-weight:900;margin:0 0 20px}
.steps{display:grid;grid-template-columns:repeat(3,1fr);gap:28px;counter-reset:s;list-style:none;padding:0;margin:0}
.steps li{counter-increment:s;padding-top:52px;position:relative}
.steps li::before{content:counter(s);position:absolute;top:0;left:0;font:700 2.6rem/1 var(--num);color:var(--ink);background:var(--tag);width:44px;height:44px;border-radius:4px;display:grid;place-items:center;font-size:1.7rem}
.steps strong{display:block;font-size:1.1rem;margin-bottom:4px}
.steps span{color:var(--muted)}
.faq{max-width:46em}
.faq details{border-bottom:1px solid var(--line);padding:14px 0}
.faq summary{font-weight:700;cursor:pointer}
.faq p{color:var(--muted);margin:8px 0 0}
footer{color:var(--muted);font-size:.85rem;padding:56px 0 40px}
footer p{max-width:46em;margin:0 0 6px}

/* 商品頁 */
.back{display:inline-block;margin:8px 0 16px;color:var(--muted);text-decoration:none}
.back:hover{color:var(--ink)}
.prod{display:grid;grid-template-columns:1fr 360px;gap:40px;align-items:start}
.prod h1{font-size:clamp(1.5rem,3vw,2.1rem);line-height:1.3;margin:4px 0 20px;font-weight:900}
.crumb{color:var(--muted);font-size:.92rem}
.bigtag{background:var(--tag);border-radius:6px;border-top:3px solid var(--tag-edge);padding:20px 22px;position:relative}
.bigtag .now{font-size:3.4rem}
.bigtag s{font:600 1.3rem var(--num);color:#6E5A12;margin-left:10px}
.bigtag .row{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:center;margin-top:10px}
.buy{display:block;text-align:center;background:var(--ink);color:#fff;text-decoration:none;font-weight:700;padding:15px;border-radius:6px;margin-top:14px}
.buy:hover{background:#000}
.fine{color:var(--muted);font-size:.85rem;margin-top:8px}
.verdict{background:var(--paper);border-radius:6px;padding:22px 24px;border-left:6px solid var(--ink)}
.verdict h2{margin:0 0 6px;font-size:1.05rem;color:var(--muted);font-weight:700}
.verdict .say{font-size:1.35rem;font-weight:900;margin:0 0 10px}
.verdict ul{margin:0;padding-left:1.2em}
.verdict li{margin:4px 0}
.panel{background:var(--paper);border-radius:6px;padding:22px 24px;margin-top:20px}
.panel h2{font-size:1.05rem;margin:0 0 12px}
.chart{width:100%;height:auto;display:block}
.chart .line{fill:none;stroke:var(--ink);stroke-width:2.5}
.chart .grid{stroke:var(--line)}
.chart .ax{fill:var(--muted);font-size:12px;font-family:"Noto Sans TC",sans-serif}
table.changes{width:100%;border-collapse:collapse;font-size:.95rem;margin-top:12px}
table.changes td{padding:6px 0;border-top:1px solid var(--line)}
table.changes td:last-child{text-align:right;font:600 1.1rem var(--num)}
.related{margin-top:48px}
.related h2{font-size:1.3rem;font-weight:900;margin:0 0 16px}

@media (max-width:820px){
.hero{grid-template-columns:1fr;gap:28px;padding:16px 0 36px}
.receipt{transform:none;max-width:420px}
.steps{grid-template-columns:1fr;gap:20px}
.prod{grid-template-columns:1fr;gap:20px}
.top nav a{margin-left:12px;font-size:.88rem}
.grid{grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px}
.now{font-size:1.75rem}
.tag-name{padding:12px 12px 10px;font-size:.92rem}
.tag-label,.tag-foot{padding-left:12px;padding-right:12px}
.scopes{overflow-x:auto}
.scopes button{flex:none;padding:8px 10px}
}
@media (prefers-reduced-motion:reduce){.receipt{animation:none}html{scroll-behavior:auto}}
"""


def page(title: str, body: str, desc: str, canonical: str, root: str) -> str:
    return f"""<!doctype html>
<html lang="zh-Hant-TW"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
<link rel="canonical" href="{esc(canonical)}">
<meta property="og:title" content="{esc(title)}"><meta property="og:description" content="{esc(desc)}">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{FONTS}">
<link rel="stylesheet" href="{root}style.css"></head>
<body><div class="wrap">
<header class="top"><a class="brand" href="{root}index.html"><i aria-hidden="true"></i>{esc(SITE_NAME)}</a>
<nav><a href="{root}index.html#how">怎麼用</a><a href="{root}index.html#faq">常見問題</a></nav></header>
<main>{body}</main>
<footer><p>{esc(DISCLAIMER)}</p><p>{esc(SITE_NAME)} 為暫定名稱。</p></footer>
</div></body></html>"""


INDEX_JS = r"""
const DEALS = JSON.parse(document.getElementById('deal-data').textContent);
const CATS = JSON.parse(document.getElementById('cat-data').textContent);
const NOW = Date.now(), DAY = 864e5, STEP = 48;
const $ = s => document.querySelector(s);
const fmt = v => v == null ? '' : '$' + Math.round(v).toLocaleString('en-US');
const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const left = d => d.e ? (Date.parse(d.e) - NOW) / DAY : null;
const saving = d => d.a || (d.pp ? d.pp - d.p : 0);
const SCOPES = {
  store:     {ch: 's', label: '進行中',   test: d => d.ch === 's' && d.st === 'now'},
  storenext: {ch: 's', label: '即將開始', test: d => d.ch === 's' && d.st === 'next'},
  promo:     {ch: 'o', label: '特價中',   test: d => d.ch === 'o' && d.a},
  soon:      {ch: 'o', label: '即將結束', test: d => d.ch === 'o' && d.a && left(d) !== null && left(d) <= 7},
  drop:      {ch: 'o', label: '最近降價', test: d => d.ch === 'o' && d.d},
  all:       {ch: 'o', label: '所有商品', test: () => true},
};
const CH = {s: '賣場特價', o: '線上特價'};
const DEFAULT = DEALS.some(SCOPES.store.test) ? 'store' : DEALS.some(SCOPES.storenext.test) ? 'storenext' : 'promo';
const md = s => s ? s.slice(5).replace('-', '/') : '';
const SORTS = {
  pct:   d => -(d.ap || d.d || 0),
  save:  d => -saving(d),
  end:   d => d.st === 'next' ? Date.parse(d.sd) : d.e ? Date.parse(d.e) : 9e15,
  price: d => d.p,
};
let ALL = null, loading = false;
const st = {scope: DEFAULT, cat: '', q: '', sort: 'pct', shown: STEP};

function readHash() {
  const h = new URLSearchParams(location.hash.slice(1));
  if (SCOPES[h.get('scope')]) st.scope = h.get('scope');
  if (h.get('cat') && CATS[h.get('cat')]) st.cat = h.get('cat');
  if (h.get('q')) st.q = h.get('q');
  if (SORTS[h.get('sort')]) st.sort = h.get('sort');
}
function writeHash() {
  const h = new URLSearchParams();
  if (st.scope !== DEFAULT) h.set('scope', st.scope);
  if (st.cat) h.set('cat', st.cat);
  if (st.q) h.set('q', st.q);
  if (st.sort !== 'pct') h.set('sort', st.sort);
  const s = h.toString();
  history.replaceState(null, '', s ? '#' + s : location.pathname);
}
function source() {
  if (st.scope !== 'all') return DEALS;
  if (ALL) return ALL;
  if (!loading) { loading = true; fetch('all.json').then(r => r.json()).then(j => { ALL = j; render(); }); }
  return null;
}
function matchQ(d) {
  if (!st.q) return true;
  const n = d.n.toLowerCase();
  return st.q.toLowerCase().split(/\s+/).every(t => n.includes(t));
}
function card(d) {
  let badge = '', notes = '', end = '';
  if (d.a) badge = `<span class="save">省 ${fmt(d.a)}<b> ${Math.round(d.ap)}%</b></span>`;
  else if (d.d) badge = `<span class="save">比上次便宜 ${fmt(d.pp - d.p)}</span>`;
  else if (d.low) badge = `<span class="note">本站紀錄最低價</span>`;
  else badge = `<span class="note">目前沒有特價</span>`;
  const l = left(d);
  if (d.ch === 's' && d.st === 'next') end = `<span class="end">${md(d.sd)} 開始</span>`;
  else if (l !== null && d.a) end = `<span class="end${l <= 2 ? ' urgent' : ''}">${l < 1 ? '今天結束' : '剩 ' + Math.ceil(l) + ' 天'}</span>`;
  if (d.wk) notes = '<span class="note">平日限定</span>';
  const was = d.a ? d.b : d.pp;
  const href = d.ch === 's' && !d.pg ? `https://www.costco.com.tw/search?text=${encodeURIComponent(d.c)}` : `p/${encodeURIComponent(d.c)}.html`;
  const ext = href.startsWith('http') ? ' target="_blank" rel="nofollow noopener"' : '';
  return `<a class="tag" href="${href}"${ext}>
<span class="tag-name">${esc(d.n)}</span>
<span class="tag-label"><span class="now">${fmt(d.p)}</span>${was ? `<s>${fmt(was)}</s>` : ''}</span>
<span class="tag-foot">${badge}${end}${notes}</span></a>`;
}
function renderChannels() {
  const ch = SCOPES[st.scope].ch;
  document.querySelectorAll('.channels button').forEach(b => b.setAttribute('aria-pressed', b.dataset.ch === ch));
  $('#scopes').innerHTML = Object.entries(SCOPES).filter(([, s]) => s.ch === ch).map(([k, s]) => {
    const n = k === 'all' ? '' : `<small>${DEALS.filter(s.test).length}</small>`;
    return `<button data-scope="${k}" aria-pressed="${k === st.scope}">${s.label}${n}</button>`;
  }).join('');
}
function render() {
  writeHash();
  renderChannels();
  $('#sort').value = st.sort;
  if ($('#q').value !== st.q) $('#q').value = st.q;
  const src = source();
  if (!src) { $('#grid').innerHTML = '<div class="empty">正在載入所有商品…</div>'; $('#more').hidden = true; return; }
  const scoped = src.filter(d => SCOPES[st.scope].test(d) && matchQ(d));
  // 分類按鈕（數量依目前範圍與搜尋計算）
  const counts = {};
  scoped.forEach(d => counts[d.k] = (counts[d.k] || 0) + 1);
  const keys = Object.keys(CATS).filter(k => counts[k]);
  $('#cats').innerHTML = [`<button data-cat="" aria-pressed="${!st.cat}">全部<small>${scoped.length}</small></button>`]
    .concat(keys.map(k => `<button data-cat="${k}" aria-pressed="${st.cat === k}">${esc(CATS[k])}<small>${counts[k]}</small></button>`)).join('');
  const list = scoped.filter(d => !st.cat || d.k === st.cat).sort((a, b) => SORTS[st.sort](a) - SORTS[st.sort](b));
  const scopeName = CH[SCOPES[st.scope].ch] + SCOPES[st.scope].label;
  const parts = [scopeName];
  if (st.cat) parts.push(CATS[st.cat]);
  if (st.q) parts.push(`「${esc(st.q)}」`);
  const filtered = st.scope !== DEFAULT || st.cat || st.q;
  $('#status').innerHTML = `<span>${parts.join('，')}：共 ${list.length.toLocaleString('en-US')} 件</span>` +
    (filtered ? '<button id="reset">清除條件</button>' : '');
  if (!list.length) {
    const offer = st.scope === 'drop' && !st.q && !st.cat
      ? `<p>本站每天比對價格，比前一次便宜的商品會出現在這裡。<br>價格紀錄從 2026/11/26 開始累積，過幾天就會有資料。</p><button data-go="promo">先看特價中的商品</button>`
      : st.scope === 'store' && !st.q && !st.cat && DEALS.some(SCOPES.storenext.test)
      ? `<p>這一期賣場特價還沒開始。</p><button data-go="storenext">看即將開始的賣場特價</button>`
      : st.q && st.scope !== 'all'
      ? `<p>在「${scopeName}」裡找不到「${esc(st.q)}」。</p><button data-go="all">改搜尋所有商品</button>`
      : `<p>沒有符合條件的商品，試試其他關鍵字或分類。</p>`;
    $('#grid').innerHTML = `<div class="empty">${offer}</div>`;
  } else {
    $('#grid').innerHTML = list.slice(0, st.shown).map(card).join('');
  }
  const rest = list.length - st.shown;
  $('#more').hidden = rest <= 0;
  $('#more').textContent = `再顯示 ${Math.min(rest, STEP)} 件（還有 ${rest.toLocaleString('en-US')} 件）`;
}
let timer;
$('#q').addEventListener('input', e => { clearTimeout(timer); timer = setTimeout(() => { st.q = e.target.value.trim(); st.shown = STEP; render(); }, 150); });
$('#sort').addEventListener('change', e => { st.sort = e.target.value; st.shown = STEP; render(); });
$('.channels').addEventListener('click', e => { const b = e.target.closest('button'); if (!b) return;
  const want = b.dataset.ch;
  st.scope = want === 's' ? (DEALS.some(SCOPES.store.test) ? 'store' : 'storenext') : 'promo';
  st.cat = ''; st.shown = STEP; render(); });
$('#scopes').addEventListener('click', e => { const b = e.target.closest('button'); if (!b) return; st.scope = b.dataset.scope; st.cat = ''; st.shown = STEP; if (st.scope === 'soon' || st.scope === 'storenext') st.sort = 'end'; render(); });
$('#cats').addEventListener('click', e => { const b = e.target.closest('button'); if (!b) return; st.cat = b.dataset.cat; st.shown = STEP; render(); });
$('#more').addEventListener('click', () => { st.shown += STEP; render(); });
document.addEventListener('click', e => {
  if (e.target.id === 'reset') { Object.assign(st, {scope: DEFAULT, cat: '', q: '', sort: 'pct', shown: STEP}); render(); }
  if (e.target.dataset && e.target.dataset.go) { st.scope = e.target.dataset.go; st.cat = ''; st.shown = STEP; render(); }
});
readHash();
render();
"""


def receipt_stats(deals: list[dict], store: list[dict], total: int) -> dict:
    promo = [d for d in deals if d["a"]]
    s_now = [d for d in store if d["st"] == "now"]
    s_next = [d for d in store if d["st"] == "next"]
    both = promo + store
    return {
        "promo": len(promo),
        "drop": sum(1 for d in deals if d["d"]),
        "store_now": len(s_now),
        "store_next": len(s_next),
        "store_next_start": min((d["sd"] for d in s_next), default=""),
        "max_pct": max((d["ap"] or 0 for d in both), default=0),
        "max_save": max((d["a"] or 0 for d in both), default=0),
        "total": total,
    }


def build_index(deals: list[dict], meta: dict, root_out: str, stats: dict) -> None:
    """deals 包含線上（ch=o）與賣場（ch=s）特價。"""
    date = meta.get("date_tw", "")
    date_show = date.replace("-", "/")
    n_store = stats["store_now"] or stats["store_next"]
    store_note = "" if stats["store_now"] else (f"（{stats['store_next_start'][5:].replace('-', '/')} 起）"
                                                 if stats["store_next"] else "")
    channels = (f'<button data-ch="s" aria-pressed="false">賣場特價<small>{n_store}</small></button>'
                f'<button data-ch="o" aria-pressed="false">線上特價<small>{stats["promo"]}</small></button>')
    body = f"""
<section class="hero">
  <div>
    <h1>好市多的特價，<br>每天幫你整理好</h1>
    <p>賣場的會員護照優惠、線上購物的特價，每天清晨自動整理。折多少、什麼時候結束、現在買划不划算，一頁看完。</p>
    <a class="cta" href="#deals">看今天的特價</a>
  </div>
  <div class="receipt" aria-label="今日特價摘要">
    <h2>今日特價明細</h2>
    <div class="date">{esc(date_show)} 清晨更新</div>
    <dl>
      <div><dt>賣場特價{esc(store_note)}</dt><dd>{n_store:,} 件</dd></div>
      <div><dt>線上特價</dt><dd>{stats['promo']:,} 件</dd></div>
      <div><dt>最高折扣</dt><dd>{stats['max_pct']:.0f}%</dd></div>
      <div><dt>單件最多省</dt><dd>{money(stats['max_save'])}</dd></div>
    </dl>
    <div class="total"><span>共檢查商品</span><span>{stats['total']:,} 件</span></div>
  </div>
</section>

<section id="deals" class="tools">
  <div class="channels" role="group" aria-label="通路">{channels}</div>
  <div class="search">
    <input id="q" type="search" placeholder="想買什麼？例如：衛生紙、咖啡、氣炸鍋" aria-label="搜尋商品" autocomplete="off">
    <select id="sort" aria-label="排序方式">
      <option value="pct">折扣最多</option><option value="save">省最多錢</option>
      <option value="end">最快結束</option><option value="price">價格低到高</option>
    </select>
  </div>
  <div class="scopes" id="scopes" role="group" aria-label="顯示範圍"></div>
  <div class="cats" id="cats" role="group" aria-label="商品分類"></div>
</section>
<div class="status" id="status"></div>
<div class="grid" id="grid"></div>
<button class="more" id="more" hidden></button>

<section class="section" id="how">
  <h2>怎麼用</h2>
  <ol class="steps">
    <li><strong>每天自動檢查價格</strong><span>每天清晨檢查好市多官網的會員護照，和線上購物 {stats['total']:,} 件商品的價格，不用自己一頁一頁翻。</span></li>
    <li><strong>挑出特價與降價</strong><span>把正在特價、或比之前便宜的商品挑出來，標出省多少錢、還剩幾天。</span></li>
    <li><strong>點進去看值不值得買</strong><span>每件商品都有價格紀錄，看得出現在是不是好價格，再到官網下單。</span></li>
  </ol>
</section>

<section class="section faq" id="faq">
  <h2>常見問題</h2>
  <details><summary>這是好市多的官方網站嗎？</summary><p>不是。這是個人製作的整理網站，和好市多沒有任何關係，也不賣東西。要買請點商品頁的「到好市多官網購買」。</p></details>
  <details><summary>賣場特價的資料從哪裡來？</summary><p>來自好市多官網公布的會員護照（會員皮夾）優惠，列出的是官方標示的賣場售價與優惠期間。各賣場的庫存、實際售價以現場為準；標示「平日限定」的商品，週六日不適用。</p></details>
  <details><summary>價格準嗎？</summary><p>每天清晨更新一次，白天官網若調整價格，這裡要到隔天才會反映。實際價格和活動條件以好市多官網為準。</p></details>
  <details><summary>為什麼有些商品找不到？</summary><p>賣場商品只收錄會員護照上的優惠品項；線上商品只收錄不用登入就看得到價格的。賣場平常的價格、需要登入才看得到的價格，這裡都沒有。</p></details>
  <details><summary>價格紀錄為什麼很短？</summary><p>本站從 2026/11/26 開始記錄價格，之後每天累積。記錄越久，越能看出現在的價格是高還是低。</p></details>
</section>

<script type="application/json" id="deal-data">{safe_json(deals)}</script>
<script type="application/json" id="cat-data">{safe_json(CAT_LABELS_ALL)}</script>
<script>{INDEX_JS}</script>
"""
    desc = (f"好市多賣場特價 {n_store} 件、線上特價 {stats['promo']} 件，最高折 {stats['max_pct']:.0f}%。"
            "每天自動整理會員護照與線上購物優惠，標出省多少、還剩幾天，附價格紀錄。")
    with open(os.path.join(root_out, "index.html"), "w", encoding="utf-8") as f:
        f.write(page(f"好市多特價整理（賣場＋線上）｜{SITE_NAME}", body, desc, SITE_BASE + "/", ""))


def verdict(r: dict, today: str, now: datetime) -> tuple[str, list[str]]:
    """商品頁「現在買划算嗎？」：只陳述資料，不做保證。"""
    price = r.get("price")
    if r.get("last_seen") != today:
        return "今天官網沒有列出這件商品", ["可能已經下架或暫時缺貨，下面是之前記錄到的價格。"]
    if price is None:
        return "官網需要登入才看得到價格", ["這件商品的價格不公開，本站沒有記錄。"]
    points = []
    end = parse_utc(r.get("promo_end"))
    promo = r.get("promo_amount")
    promo_active = bool(promo) and (end is None or end > now)
    if promo_active:
        base = r.get("base_price")
        pct = f"（{promo / base * 100:.0f}%）" if base else ""
        points.append(f"正在特價，比原價便宜 {money(promo)}{pct}，{fmt_end(r.get('promo_end'))} 前有效。")
        if end and end - now <= timedelta(days=2):
            points.append("活動快結束了。")
        say = "正在特價"
    elif r.get("prev_price") and r["prev_price"] > price:
        points.append(f"{r.get('changed_on')} 降價，比之前便宜 {money(r['prev_price'] - price)}。")
        say = "最近有降價"
    else:
        points.append("目前沒有特價。")
        say = "目前是一般價格"
    first = r.get("first_seen") or today
    days = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(first, "%Y-%m-%d")).days + 1
    mn, mx = r.get("min_price"), r.get("max_price")
    if days < 14:
        points.append(f"本站從 {first} 開始記錄這件商品，目前只有 {days} 天資料，還看不出長期價格高低。")
    elif mn is not None and price <= mn and mx and mx > price:
        points.append(f"這是本站記錄 {days} 天以來的最低價。")
        say = "本站記錄最低價"
    elif mn is not None and price > mn:
        points.append(f"比本站記錄的最低價 {money(mn)} 貴 {money(price - mn)}，不急的話可以再等等。")
    else:
        points.append(f"這 {days} 天價格都沒有變。")
    return say, points


def store_box(sd: dict | None) -> str:
    if not sd:
        return ""
    md = lambda s: s[5:].replace("-", "/")  # noqa: E731
    when = f"{md(sd['sd'])}–{md(sd['ed'])}" + ("（平日限定，週六日不適用）" if sd["wk"] else "")
    status = "進行中" if sd["st"] == "now" else f"{md(sd['sd'])} 開始"
    also = "<p class='fine'>線上購物也有優惠，售價以官網為準。</p>" if sd["oa"] else ""
    return (f"<div class='storebox'><h2>賣場特價・{esc(status)}</h2>"
            f"<div><span class='now'>{money(sd['p'])}</span><s>{money(sd['b'])}</s></div>"
            f"<div class='row'><span class='save'>省 {money(sd['a'])}<b> {sd['ap']:.0f}%</b></span></div>"
            f"<p class='fine'>優惠期間 {esc(when)}。會員護照賣場售價，各賣場以現場為準。</p>{also}</div>")


def build_product(code: str, r: dict, hist: list[tuple[str, float]], today: str, now: datetime,
                  out_dir: str, related: list[dict], store_deal: dict | None = None) -> None:
    price, base = r.get("price"), r.get("base_price")
    end = parse_utc(r.get("promo_end"))
    promo_active = bool(r.get("promo_amount")) and (end is None or end > now)
    cat = CAT_LABELS_ALL[category_key(r.get("url"))]
    row = ""
    if promo_active:
        left = end - now if end else None
        endtxt = ""
        if left is not None:
            endtxt = "今天結束" if left < timedelta(days=1) else f"剩 {math.ceil(left.total_seconds() / 86400)} 天"
            urgent = " urgent" if left <= timedelta(days=2) else ""
            endtxt = f'<span class="end{urgent}">{esc(endtxt)}（{esc(fmt_end(r.get("promo_end")))} 止）</span>'
        pct = f" {r['promo_amount'] / base * 100:.0f}%" if base else ""
        row = f'<div class="row"><span class="save">省 {money(r["promo_amount"])}<b>{pct}</b></span>{endtxt}</div>'
    was = f"<s>{money(base)}</s>" if promo_active and base and base != price else ""
    say, points = verdict(r, today, now)
    if store_deal:
        md = lambda s: s[5:].replace("-", "/")  # noqa: E731
        when = "進行中" if store_deal["st"] == "now" else f"{md(store_deal['sd'])} 開始"
        points.insert(0, f"賣場特價{when}：會員護照賣場售價 {money(store_deal['p'])}，省 {money(store_deal['a'])}，"
                         f"到 {md(store_deal['ed'])}。")
        if store_deal["st"] == "now":
            say = "賣場特價中"
    pts = "".join(f"<li>{esc(p)}</li>" for p in points)
    changes = sorted(hist, reverse=True)[:8]
    table = ""
    if len(changes) > 1:
        table = "<table class='changes'>" + "".join(
            f"<tr><td>{esc(d)}</td><td>{money(v)}</td></tr>" for d, v in changes) + "</table>"
    rel = ""
    if related:
        rel = ('<section class="related"><h2>同分類的其他特價</h2><div class="grid">' +
               "".join(_static_card(d, now) for d in related) + "</div></section>")
    body = f"""<a class="back" href="../index.html#deals">回到特價清單</a>
<div class="prod">
  <div>
    <div class="crumb">{esc(cat)}</div>
    <h1>{esc(r.get('name'))}</h1>
    <section class="verdict"><h2>現在買划算嗎？</h2><p class="say">{esc(say)}</p><ul>{pts}</ul></section>
    <section class="panel"><h2>價格紀錄</h2>{svg_chart(hist, today)}{table}
      <p class="fine">價格只在變動時記錄，從本站開始追蹤那天算起。</p></section>
  </div>
  <aside>
    {store_box(store_deal)}
    <p class="lbl">線上購物售價</p>
    <div class="bigtag"><span class="now">{money(price) if price is not None else '官網未公開'}</span>{was}{row}</div>
    <a class="buy" href="{esc(r.get('url'))}" rel="nofollow noopener" target="_blank">到好市多官網購買</a>
    <p class="fine">會開啟好市多官網。價格與活動以官網為準。商品編號 {esc(code)}</p>
  </aside>
</div>
{rel}"""
    desc = f"{r.get('name')} 目前 {money(price)}。{say}。附每日價格紀錄。"
    with open(os.path.join(out_dir, f"{code}.html"), "w", encoding="utf-8") as f:
        f.write(page(f"{r.get('name')}｜價格紀錄｜{SITE_NAME}", body, desc, f"{SITE_BASE}/p/{code}.html", "../"))


def _static_card(d: dict, now: datetime) -> str:
    end = parse_utc(d.get("e"))
    endtxt = ""
    if end:
        left = (end - now).total_seconds() / 86400
        endtxt = (f'<span class="end{" urgent" if left <= 2 else ""}">'
                  f'{"今天結束" if left < 1 else f"剩 {math.ceil(left)} 天"}</span>')
    badge = (f'<span class="save">省 {money(d["a"])}<b> {d["ap"]:.0f}%</b></span>' if d.get("a")
             else f'<span class="save">比上次便宜 {money(d["pp"] - d["p"])}</span>' if d.get("d") else "")
    if d.get("ch") == "s" and d.get("st") == "next":
        endtxt = f'<span class="end">{esc(d["sd"][5:].replace("-", "/"))} 開始</span>'
    if d.get("ch") == "s":
        endtxt += '<span class="note">賣場</span>'
    was = d["b"] if d.get("a") else d.get("pp")
    return (f'<a class="tag" href="{esc(d["c"])}.html"><span class="tag-name">{esc(d["n"])}</span>'
            f'<span class="tag-label"><span class="now">{money(d["p"])}</span>{f"<s>{money(was)}</s>" if was else ""}</span>'
            f'<span class="tag-foot">{badge}{endtxt}</span></a>')


def build(state: dict, history: dict, meta: dict, out: str, now: datetime | None = None,
          store_items: list[dict] | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    today = meta["date_tw"]
    if os.path.exists(out):
        shutil.rmtree(out)
    pdir = os.path.join(out, "p")
    os.makedirs(pdir)
    deals = compute_deals(state, today, now)
    store = compute_store_deals(store_items or [], state, today)
    store_by_code = {d["c"]: d for d in store}
    by_code = {d["c"]: d for d in deals}
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for d in store + deals:
        if (d["a"] or d["d"]) and d.get("pg", True):
            by_cat[d["k"]].append(d)
    listed = []
    for code, r in state.items():
        if r.get("last_seen") == today and r.get("price") is not None:
            d = by_code.get(code) or {"c": code, "n": r.get("name") or "", "p": r["price"], "b": r.get("base_price"),
                                      "a": None, "ap": None, "e": None, "pp": None, "d": None,
                                      "low": False, "k": category_key(r.get("url"))}
            listed.append({k: d.get(k) for k in ("c", "n", "p", "b", "a", "ap", "e", "pp", "d", "low", "k")})
    stats = receipt_stats(deals, store, meta.get("unique_count") or len(state))
    build_index(store + deals, meta, out, stats)
    for code, r in state.items():
        k = category_key(r.get("url"))
        related = [d for d in by_cat.get(k, []) if d["c"] != code][:4]
        build_product(code, r, history.get(code, []), today, now, pdir, related, store_by_code.get(code))
    with open(os.path.join(out, "all.json"), "w", encoding="utf-8") as f:
        json.dump(listed, f, ensure_ascii=False, separators=(",", ":"))
    urls = [SITE_BASE + "/"] + [f"{SITE_BASE}/p/{c}.html" for c in sorted(state)]
    with open(os.path.join(out, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
        f.writelines(f"<url><loc>{esc(u)}</loc></url>\n" for u in urls)
        f.write("</urlset>\n")
    with open(os.path.join(out, "robots.txt"), "w", encoding="utf-8") as f:
        f.write(f"User-agent: *\nAllow: /\nSitemap: {SITE_BASE}/sitemap.xml\n")
    with open(os.path.join(out, "style.css"), "w", encoding="utf-8") as f:
        f.write(CSS)
    open(os.path.join(out, ".nojekyll"), "w").close()
    return {"deals": len(deals), "store": len(store), "products": len(state), "listed": len(listed)}


def main() -> int:
    with open("data/products.json", encoding="utf-8") as f:
        state = json.load(f)
    with open("data/last_run.json", encoding="utf-8") as f:
        meta = json.load(f)
    store_items = []
    if os.path.exists("data/store_deals.json"):
        with open("data/store_deals.json", encoding="utf-8") as f:
            store_items = json.load(f).get("items", [])
    stats = build(state, load_history("data/history.csv"), meta, "site", store_items=store_items)
    print(json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

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
import os
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

TW = timezone(timedelta(hours=8))
SITE_NAME = os.environ.get("SITE_NAME", "降價雷達（暫名）")
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
            "low": is_low, "g": r.get("group") or "",
        })
    deals.sort(key=lambda x: -(max(x["ap"] or 0, x["d"] or 0)))
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

    def x(ds):
        return L + (datetime.strptime(ds, "%Y-%m-%d") - d0).days / span * W

    def y(v):
        return T + (hi - v) / (hi - lo) * H

    path = []
    for i, (ds, v) in enumerate(pts):
        if i == 0:
            path.append(f"M{x(ds):.1f},{y(v):.1f}")
        else:
            path.append(f"H{x(ds):.1f}V{y(v):.1f}")
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


CSS = """
:root{--bg:#fff;--fg:#1c1c1c;--muted:#6b6b6b;--line:#e6e6e6;--accent:#c0392b;--good:#1e8449;--chip:#f3f3f3}
@media (prefers-color-scheme:dark){:root{--bg:#141414;--fg:#ececec;--muted:#9a9a9a;--line:#2c2c2c;--accent:#ff6b5b;--good:#4cd07d;--chip:#232323}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.6 system-ui,-apple-system,"Noto Sans TC","PingFang TC","Microsoft JhengHei",sans-serif}
a{color:inherit}header,main,footer{max-width:960px;margin:0 auto;padding:0 16px}
header{padding-top:20px}h1{font-size:1.5rem;margin:0}.sub{color:var(--muted);margin:4px 0 16px;font-size:.9rem}
.bar{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px}.bar input,.bar select{font:inherit;padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg)}
.bar input{flex:1;min-width:200px}.count{color:var(--muted);font-size:.9rem;margin:4px 0 8px}
ul.list{list-style:none;padding:0;margin:0}ul.list li{border-bottom:1px solid var(--line);padding:12px 0;display:flex;gap:12px;justify-content:space-between;align-items:flex-start}
.name{font-weight:600;text-decoration:none}.name:hover{text-decoration:underline}.meta{font-size:.85rem;color:var(--muted)}
.right{text-align:right;white-space:nowrap}.now{font-size:1.15rem;font-weight:700}.was{color:var(--muted);text-decoration:line-through;font-size:.85rem}
.tag{display:inline-block;font-size:.75rem;padding:1px 8px;border-radius:999px;background:var(--chip);margin-right:4px}
.tag.off{color:var(--accent)}.tag.low{color:var(--good)}.muted{color:var(--muted)}
footer{color:var(--muted);font-size:.8rem;border-top:1px solid var(--line);margin-top:32px;padding-top:12px;padding-bottom:32px}
.chart{width:100%;height:auto}.chart .line{fill:none;stroke:var(--accent);stroke-width:2}.chart .grid{stroke:var(--line)}.chart .ax{fill:var(--muted);font-size:12px}
.kv{display:grid;grid-template-columns:auto 1fr;gap:4px 16px;margin:12px 0}.btn{display:inline-block;padding:10px 16px;border-radius:8px;background:var(--accent);color:#fff;text-decoration:none;font-weight:600}
"""


def page(title: str, body: str, desc: str, canonical: str, root: str) -> str:
    return f"""<!doctype html>
<html lang="zh-Hant-TW"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
<link rel="canonical" href="{esc(canonical)}">
<style>{CSS}</style></head>
<body><header><a href="{root}index.html" style="text-decoration:none"><h1>{esc(SITE_NAME)}</h1></a>
<p class="sub">好市多線上購物降價追蹤（非官方）</p></header>
<main>{body}</main>
<footer><p>{esc(DISCLAIMER)}</p></footer></body></html>"""


INDEX_JS = r"""
const DEALS = JSON.parse(document.getElementById('deals').textContent);
const $ = s => document.querySelector(s);
const fmt = v => v == null ? '—' : '$' + Math.round(v).toLocaleString('en-US');
let ALL = null;
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function row(d){
  const tags = [];
  if (d.ap) tags.push(`<span class="tag off">官方折 ${fmt(d.a)}（${d.ap}%）</span>`);
  if (d.d) tags.push(`<span class="tag off">比前次降 ${d.d}%</span>`);
  if (d.low) tags.push(`<span class="tag low">本站紀錄最低</span>`);
  const meta = [];
  if (d.ef) meta.push(`活動至 ${d.ef}`);
  if (d.dc) meta.push(`${d.dc} 降價`);
  if (d.g) meta.push(esc(d.g));
  const was = d.ap && d.b ? d.b : (d.pp || null);
  return `<li><div><a class="name" href="p/${encodeURIComponent(d.c)}.html">${esc(d.n)}</a>
  <div>${tags.join('')}</div><div class="meta">${meta.join('・')}</div></div>
  <div class="right"><div class="now">${fmt(d.p)}</div>${was?`<div class="was">${fmt(was)}</div>`:''}</div></li>`;
}
function rowAll(d){
  return `<li><div><a class="name" href="p/${encodeURIComponent(d.c)}.html">${esc(d.n)}</a></div><div class="right"><div class="now">${fmt(d.p)}</div></div></li>`;
}
function render(){
  const q = $('#q').value.trim().toLowerCase();
  const f = $('#f').value, s = $('#s').value;
  if (f === 'all') {
    if (!ALL) { $('#list').innerHTML = '<li class="muted">載入全部商品中…</li>';
      fetch('all.json').then(r=>r.json()).then(j=>{ALL=j; render();}); return; }
    let xs = ALL.filter(d => !q || d.n.toLowerCase().includes(q));
    if (s === 'price') xs = xs.slice().sort((a,b)=>a.p-b.p);
    $('#count').textContent = `共 ${xs.length} 件商品` + (xs.length>300?'（顯示前 300 件）':'');
    $('#list').innerHTML = xs.slice(0,300).map(rowAll).join(''); return;
  }
  let xs = DEALS.filter(d => (!q || d.n.toLowerCase().includes(q)) &&
    (f === 'deals' || (f === 'promo' && d.ap) || (f === 'drop' && d.d) || (f === 'low' && d.low)));
  const key = {pct: d => -Math.max(d.ap||0, d.d||0), amt: d => -((d.a||0) + ((d.pp||0)-(d.pp?d.p:0))),
               end: d => d.e ? Date.parse(d.e) : 9e15, price: d => d.p}[s];
  xs = xs.slice().sort((a,b)=>key(a)-key(b));
  $('#count').textContent = `共 ${xs.length} 件`;
  $('#list').innerHTML = xs.map(row).join('') || '<li class="muted">沒有符合的商品</li>';
}
['#q','#f','#s'].forEach(id => $(id).addEventListener('input', render));
render();
"""


def build_index(deals: list[dict], meta: dict, root_out: str) -> None:
    updated = meta.get("date_tw", "")
    n_promo = sum(1 for d in deals if d["ap"])
    n_drop = sum(1 for d in deals if d["d"])
    body = f"""
<p class="meta">資料日期：{esc(updated)}（每日自動更新）・官方特價中 {n_promo} 件・近 {RECENT_DAYS} 天降價 {n_drop} 件</p>
<div class="bar">
<input id="q" type="search" placeholder="搜尋商品名稱，例如：衛生紙、咖啡、iPad" aria-label="搜尋商品">
<select id="f" aria-label="篩選"><option value="deals">全部降價</option><option value="promo">官方特價中</option>
<option value="drop">近 {RECENT_DAYS} 天降價</option><option value="low">本站紀錄最低</option><option value="all">所有商品</option></select>
<select id="s" aria-label="排序"><option value="pct">折扣幅度</option><option value="amt">折扣金額</option>
<option value="end">即將結束</option><option value="price">價格低到高</option></select>
</div>
<p id="count" class="count"></p>
<ul id="list" class="list"></ul>
<script type="application/json" id="deals">{safe_json(deals)}</script>
<script>{INDEX_JS}</script>
"""
    desc = f"好市多線上購物今日降價清單：官方特價中 {n_promo} 件、近期降價 {n_drop} 件，每日自動更新，附價格走勢。"
    with open(os.path.join(root_out, "index.html"), "w", encoding="utf-8") as f:
        f.write(page(f"{SITE_NAME}｜好市多線上降價清單", body, desc, SITE_BASE + "/", ""))


def build_product(code: str, r: dict, hist: list[tuple[str, float]], today: str, now: datetime, out_dir: str) -> None:
    price, base = r.get("price"), r.get("base_price")
    end = parse_utc(r.get("promo_end"))
    promo_active = bool(r.get("promo_amount")) and (end is None or end > now)
    rows = [("目前售價", money(price))]
    if base and price is not None and base != price:
        rows.append(("原價", money(base)))
    if promo_active:
        rows.append(("官方折扣", f"{money(r['promo_amount'])}（至 {fmt_end(r.get('promo_end'))}）"))
    rows += [("本站紀錄最低", money(r.get("min_price"))), ("本站紀錄最高", money(r.get("max_price"))),
             ("開始追蹤", r.get("first_seen") or "—"), ("最後更新", r.get("last_seen") or "—")]
    if r.get("last_seen") != today:
        rows.append(("狀態", "今日官網未列出，可能已下架或缺貨"))
    kv = "".join(f"<div class='muted'>{esc(k)}</div><div>{esc(v)}</div>" for k, v in rows)
    body = f"""<p><a href="../index.html">← 回降價清單</a></p>
<h2>{esc(r.get('name'))}</h2>
<div class="kv">{kv}</div>
<h3>價格走勢</h3>{svg_chart(hist, today)}
<p class="meta">走勢圖從本站開始追蹤當天起算；價格只在變動時記錄。</p>
<p><a class="btn" href="{esc(r.get('url'))}" rel="nofollow noopener" target="_blank">到好市多官網查看</a></p>
<p class="meta">商品編號：{esc(code)}</p>"""
    desc = f"{r.get('name')} 目前售價 {money(price)}，本站紀錄最低 {money(r.get('min_price'))}，附每日價格走勢。"
    with open(os.path.join(out_dir, f"{code}.html"), "w", encoding="utf-8") as f:
        f.write(page(f"{r.get('name')}｜價格走勢｜{SITE_NAME}", body, desc, f"{SITE_BASE}/p/{code}.html", "../"))


def build(state: dict, history: dict, meta: dict, out: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    today = meta["date_tw"]
    if os.path.exists(out):
        shutil.rmtree(out)
    pdir = os.path.join(out, "p")
    os.makedirs(pdir)
    deals = compute_deals(state, today, now)
    build_index(deals, meta, out)
    listed = []
    for code, r in state.items():
        build_product(code, r, history.get(code, []), today, now, pdir)
        if r.get("last_seen") == today and r.get("price") is not None:
            listed.append({"c": code, "n": r.get("name") or "", "p": r["price"]})
    with open(os.path.join(out, "all.json"), "w", encoding="utf-8") as f:
        json.dump(listed, f, ensure_ascii=False, separators=(",", ":"))
    urls = [SITE_BASE + "/"] + [f"{SITE_BASE}/p/{c}.html" for c in sorted(state)]
    with open(os.path.join(out, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
        f.writelines(f"<url><loc>{esc(u)}</loc></url>\n" for u in urls)
        f.write("</urlset>\n")
    with open(os.path.join(out, "robots.txt"), "w", encoding="utf-8") as f:
        f.write(f"User-agent: *\nAllow: /\nSitemap: {SITE_BASE}/sitemap.xml\n")
    open(os.path.join(out, ".nojekyll"), "w").close()
    return {"deals": len(deals), "products": len(state), "listed": len(listed)}


def main() -> int:
    with open("data/products.json", encoding="utf-8") as f:
        state = json.load(f)
    with open("data/last_run.json", encoding="utf-8") as f:
        meta = json.load(f)
    stats = build(state, load_history("data/history.csv"), meta, "site")
    print(json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

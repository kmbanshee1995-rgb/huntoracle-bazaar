#!/usr/bin/env python3
"""Hunt Oracle Char Bazaar scraper (tibia.com only).

Runs in GitHub Actions every 30 minutes and writes into ./out:
  list.json      all current auctions, compact, with a price range of similar sold characters
  d/<id>.json    details of each current auction (skills, quests, bosses, charms, mounts, outfits ...)
  history.json   ended auctions from the last ~30 days (vocation, level, skill, bid, sold)
Polite: one request at a time with a pause, and details are fetched only once per auction.
"""
import json, os, re, sys, time, html, urllib.request, urllib.error, statistics

BASE = "https://www.tibia.com/charactertrade/?subtopic="
OUT = sys.argv[1] if len(sys.argv) > 1 else "out"
DELAY = float(os.environ.get("DELAY", "1.1"))
MAX_DETAILS = int(os.environ.get("MAX_DETAILS", "1500"))   # per run, the rest comes next run
UA = "Mozilla/5.0 (compatible; HuntOracleBazaar/1.0; +https://huntoracle.pl)"
T0 = time.time(); TIME_LIMIT = float(os.environ.get("TIME_LIMIT", "2000"))  # seconds per run

def get(url, tries=3):
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html", "Accept-Language": "en-US,en;q=0.8"})
            with urllib.request.urlopen(req, timeout=40) as r:
                t = r.read().decode("utf-8", "replace")
            time.sleep(DELAY)
            if 'class="Auction"' in t or "CharacterDetailsBlock" in t or "No character auctions" in t or "Results:" in t:
                return t
            raise RuntimeError("unexpected page (challenge?)")
        except Exception as e:
            print("  retry", k + 1, url[-60:], e, flush=True); time.sleep(5 * (k + 1))
    raise RuntimeError("failed: " + url)

def txt(s): return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", " ", s or ""))).strip()
def num(s):
    m = re.search(r"-?\d+", (s or "").replace(",", "")); return int(m.group()) if m else 0

SKILLRE = re.compile(r"^(\d+)\s+(Axe Fighting|Sword Fighting|Club Fighting|Distance Fighting|Fist Fighting|Magic Level|Shielding)")
SKK = {"Axe Fighting": "axe", "Sword Fighting": "sword", "Club Fighting": "club", "Distance Fighting": "distance", "Fist Fighting": "fist", "Magic Level": "magic", "Shielding": "shielding", "Fishing": "fishing"}

def split_auctions(t):
    idx = [m.start() for m in re.finditer(r'<div class="Auction"\s*>', t)]
    return [t[a:(idx[i + 1] if i + 1 < len(idx) else a + 20000)] for i, a in enumerate(idx)]

MON = {m: i + 1 for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}
def ts_of(t):
    import calendar
    m = re.search(r"([A-Z][a-z]{2})\s+(\d+)\s+(\d{4}),\s*(\d+):(\d+)\s*(CEST|CET)?", t or "")
    if not m: return 0
    off = 2 if m.group(6) == "CEST" else 1
    return calendar.timegm((int(m.group(3)), MON.get(m.group(1), 1), int(m.group(2)), int(m.group(4)) - off, int(m.group(5)), 0))

def parse_header(h):
    o = {}
    m = re.search(r"auctionid=(\d+)", h) or re.search(r"AuctionTimer_(\d+)", h) or re.search(r'name="auctionid" value="(\d+)"', h)
    o["id"] = int(m.group(1)) if m else 0
    m = re.search(r'class="AuctionCharacterName"\s*>([\s\S]*?)</div>', h); o["n"] = txt(m.group(1)) if m else ""
    m = re.search(r"Level:\s*(\d+)\s*\|\s*Vocation:\s*([^|<]+)\|\s*(Male|Female)\s*\|\s*World:\s*(?:<a[^>]*>)?([^<]+)", h)
    if m: o["l"] = int(m.group(1)); o["v"] = m.group(2).strip(); o["w"] = m.group(4).strip()
    m = re.search(r'class="AuctionOutfitImage"\s+src="[^"]*/outfits/([^"/]+)\.gif"', h); o["o"] = m.group(1) if m else ""
    m = re.search(r'data-timestamp="(\d+)"', h); o["e"] = int(m.group(1)) if m else 0
    for l, v in re.findall(r'class="ShortAuctionDataLabel"\s*>([^<]*)</div>\s*<div class="ShortAuctionDataValue"\s*>([\s\S]*?)</div>', h):
        k = l.strip().rstrip(":")
        if k == "Auction End" and not o["e"]:
            o["et"] = txt(v)
        elif k.endswith("Bid"):
            o["bt"] = {"Current Bid": "c", "Minimum Bid": "m", "Winning Bid": "w"}.get(k, k); o["b"] = num(txt(v))
    if not o["e"] and o.get("et"): o["e"] = ts_of(o["et"])
    o.pop("et", None)
    o["i"] = [int(x) for x in re.findall(r'<div class="CVIcon CVIconObject"[^>]*>\s*<img src="[^"]*/objects/(\d+)\.gif"', h)]
    feats = [(int(c), txt(t)) for c, t in re.findall(r'<div class="Entry"\s*>\s*<img class="CharacterFeatureCategory"\s+src="[^"]*usp-category-(\d+)\.png"\s*/?>([\s\S]*?)</div>', h)]
    o["sk"] = {}
    for c, t in feats:
        m = SKILLRE.match(t)
        if m: o["sk"][SKK[m.group(2)]] = int(m.group(1))
    o["f"] = [t for c, t in feats if not SKILLRE.match(t)][:6]
    return o

def parse_list(t):
    items = [a for a in map(parse_header, split_auctions(t)) if a["id"] and a.get("n")]
    pages = max([int(x) for x in re.findall(r"currentpage=(\d+)", t)] or [1])
    return items, pages

def block(t, bid):
    m = re.search(r'class="CharacterDetailsBlock[^"]*"\s+id="' + bid + '"', t)
    if not m: return ""
    j = t.find('class="CharacterDetailsBlock', m.start() + 30)
    return t[m.start(): j if j > 0 else m.start() + 200000]

def rows(b):
    out = []
    for r in re.findall(r'<tr class="(?:Odd|Even)(?![^"]*IndicateMoreEntries)[^"]*"\s*>([\s\S]*?)</tr>', b):
        cells = [txt(c) for c in re.findall(r"<td[^>]*>([\s\S]*?)</td>", r)]
        if cells and not (len(cells) == 1 and re.match(r"^No .* (yet|found)|^\(", cells[0])): out.append(cells)
    return out

def icons(b, kind):
    return [[txt(n), int(i)] for n, i in re.findall(r'<div class="CVIcon"\s+title="([^"]*)"\s*>\s*<img src="[^"]*/' + kind + r'/(\d+)(?:_\d+)?\.gif"', b)]

def outfit_icons(b):
    return [[txt(n), i] for n, i in re.findall(r'<div class="CVIcon"\s+title="([^"]*)"\s*>\s*<img src="[^"]*/outfits/([\d_]+)\.gif"', b)]

def parse_details(t):
    g = block(t, "General"); gen = {}
    for k, v in re.findall(r'<span class="LabelV"\s*>([^<]*?):?</span>\s*<div[^>]*>([\s\S]*?)</div>', g): gen[k.strip().rstrip(":")] = txt(v)
    sk = {SKK.get(k.strip(), k.strip()): int(v) for k, v in re.findall(r'class="LabelColumn"\s*>\s*<b>([^<]+)</b>\s*</td>\s*<td class="LevelColumn"\s*>(\d+)', g)}
    keep = ["Hit Points", "Mana", "Capacity", "Speed", "Blessings", "Mounts", "Outfits", "Titles", "Creation Date", "Gold", "Achievement Points", "Charm Expansion",
            "Available Charm Points", "Spent Charm Points", "Available Minor Charm Echoes", "Spent Minor Charm Echoes", "Hunting Task Points", "Permanent Prey Slots",
            "Prey Wildcards", "Hirelings", "Exalted Dust", "Boss Points", "Bonus Promotion Points", "Animus Masteries unlocked", "Regular World Transfer"]
    d = {"g": {k: gen[k] for k in keep if k in gen}, "sk": sk}
    d["q"] = [r[0] for r in rows(block(t, "CompletedQuestLines"))]
    d["bo"] = [[r[2], num(r[1]), num(r[0])] for r in rows(block(t, "BosstiaryProgress")) if len(r) >= 3 and num(r[1]) > 0]
    d["ch"] = [[r[2], r[1][:2], num(r[3] if len(r) > 3 else "0")] for r in rows(block(t, "Charms")) if len(r) >= 3]
    d["bl"] = [re.sub(r"\s*\(.*\)$", "", r[1]) for r in rows(block(t, "Blessings")) if len(r) >= 2 and num(r[0]) > 0]
    d["im"] = [r[0] for r in rows(block(t, "Imbuements"))]
    d["mo"] = icons(block(t, "Mounts"), "mounts"); d["smo"] = icons(block(t, "StoreMounts"), "mounts")
    d["ou"] = outfit_icons(block(t, "Outfits")); d["sou"] = outfit_icons(block(t, "StoreOutfits"))
    be = [r for r in rows(block(t, "BestiaryProgress")) if len(r) >= 3]
    d["be"] = [len(be), sum(1 for r in be if num(r[0]) >= 4)]
    d["ac"] = len(rows(block(t, "Achievements")))
    return d

def load(p, default):
    try:
        with open(p, encoding="utf-8") as f: return json.load(f)
    except Exception: return default

def save(p, o):
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "w", encoding="utf-8") as f: json.dump(o, f, ensure_ascii=False, separators=(",", ":"))

def base_voc(v):
    v = (v or "").lower()
    for k in ("knight", "paladin", "sorcerer", "druid", "monk"):
        if k in v: return k
    return "none"

MAIN = {"knight": ("axe", "sword", "club"), "paladin": ("distance",), "monk": ("fist",), "sorcerer": ("magic",), "druid": ("magic",), "none": ()}
def main_skill(a):
    vals = [a.get("sk", {}).get(k, 0) for k in MAIN[base_voc(a.get("v"))]]
    return max(vals) if vals else 0

def price_range(a, hist):
    """25-75% of winning bids of similar sold characters: same vocation, level close, main skill close when known."""
    v, L, s = base_voc(a.get("v")), a.get("l", 0), main_skill(a)
    for lt, st in ((0.08, 4), (0.15, 7), (0.25, 99)):
        pool = [h[3] for h in hist if h[0] == v and h[4] and abs(h[1] - L) <= max(8, L * lt) and (not s or not h[2] or abs(h[2] - s) <= st)]
        if len(pool) >= 4: break
    if len(pool) < 3: return None
    pool.sort(); n = len(pool)
    q = lambda p: pool[min(n - 1, int(p * (n - 1) + 0.5))]
    return [q(0.25), q(0.5), q(0.75), n]

def main():
    os.makedirs(OUT, exist_ok=True)
    hist = load(os.path.join(OUT, "history.json"), [])            # [voc, level, mainSkill, bid, sold, end, id]
    seen_hist = {h[6] for h in hist}
    # 1. ended auctions: newest pages until known ones, then continue an unfinished backfill from where it stopped
    meta = load(os.path.join(OUT, "meta.json"), {"bf": 1, "done": False})
    def take(page):
        items, pages = parse_list(get(BASE + "pastcharactertrades&order_column=101&order_direction=0&currentpage=%d" % page))
        fresh = [a for a in items if a["id"] not in seen_hist]
        for a in fresh:
            hist.append([base_voc(a.get("v")), a.get("l", 0), main_skill(a), a.get("b", 0), 1 if a.get("bt") == "w" else 0, a.get("e", 0), a["id"]]); seen_hist.add(a["id"])
        print("history page", page, "/", pages, "new", len(fresh), flush=True)
        return len(fresh), pages
    new_h, page = 0, 1
    while time.time() - T0 < TIME_LIMIT * 0.55:
        n, pages = take(page); new_h += n
        if n == 0 or page >= pages: break
        page += 1
    if not meta.get("done"):
        page = max(meta.get("bf", 1), page + 1)
        while time.time() - T0 < TIME_LIMIT * 0.55:
            n, pages = take(page); new_h += n
            if page >= pages: meta["done"] = True; break
            page += 1
        meta["bf"] = page
    save(os.path.join(OUT, "meta.json"), meta)
    cutoff = time.time() - 45 * 86400
    hist = [h for h in hist if h[5] == 0 or h[5] >= cutoff]
    save(os.path.join(OUT, "history.json"), hist)
    # 2. current auctions (all pages)
    cur, page = [], 1
    while True:
        items, pages = parse_list(get(BASE + "currentcharactertrades&order_column=101&order_direction=1&currentpage=%d" % page))
        cur += items; print("current page", page, "/", pages, len(items), flush=True)
        if page >= pages: break
        page += 1
    ids = {a["id"] for a in cur}
    # 3. details for auctions we have not fetched yet
    ddir = os.path.join(OUT, "d"); os.makedirs(ddir, exist_ok=True)
    have = {int(f[:-5]) for f in os.listdir(ddir) if f.endswith(".json")}
    for f in have - ids: os.remove(os.path.join(ddir, "%d.json" % f))      # ended auctions
    todo = [a["id"] for a in cur if a["id"] not in have][:MAX_DETAILS]
    for k, aid in enumerate(todo):
        if time.time() - T0 > TIME_LIMIT: print("time limit, rest next run"); break
        try:
            save(os.path.join(ddir, "%d.json" % aid), parse_details(get(BASE + "currentcharactertrades&page=details&auctionid=%d" % aid)))
        except Exception as e: print("detail fail", aid, e)
        if k % 50 == 0: print("details", k, "/", len(todo), flush=True)
    # 4. list with price ranges and a few detail fields for filtering
    for a in cur:
        d = load(os.path.join(ddir, "%d.json" % a["id"]), None)
        if d:
            a["sk"] = {k: v for k, v in d["sk"].items() if k != "fishing"}; a["d"] = 1
            a["q"] = len(d["q"]); a["bo"] = len(d["bo"]); a["cp"] = num(d["g"].get("Spent Charm Points", "0"))
        a["r"] = price_range(a, hist)
    save(os.path.join(OUT, "list.json"), {"t": int(time.time()), "n": len(cur), "a": cur})
    print("done", len(cur), "auctions,", len(hist), "history (+%d)," % new_h, "details new", len(todo), "in", int(time.time() - T0), "s")

if __name__ == "__main__":
    main()

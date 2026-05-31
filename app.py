from flask import Flask, render_template, jsonify, request, session, redirect, url_for
from flask_cors import CORS
import requests
import psycopg2
import psycopg2.extras
import os
import time
from datetime import datetime

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "apppedia2026")
DATABASE_URL   = os.environ.get("DATABASE_URL")

_cache = {}
_CACHE_TTL = 3600  # 1시간

def cache_get(key):
    if key in _cache:
        data, ts = _cache[key]
        if time.time() - ts < _CACHE_TTL:
            return data
        del _cache[key]
    return None

def cache_set(key, data):
    _cache[key] = (data, time.time())

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "apppedia-secret-2026")
CORS(app)

def get_db():
    url = DATABASE_URL
    if url and "sslmode" not in url:
        url += "?sslmode=require"
    return psycopg2.connect(url)

COLLECTIONS = [
    {"id": "weekly",      "emoji": "🔥", "title": "이번 주 추천",    "desc": "에디터가 직접 골랐어요"},
    {"id": "productivity","emoji": "💼", "title": "생산성 앱 모음",   "desc": "일 잘하는 사람들의 앱"},
    {"id": "game",        "emoji": "🎮", "title": "인기 게임",        "desc": "요즘 가장 핫한 게임"},
    {"id": "photo",       "emoji": "📸", "title": "사진 & 영상",      "desc": "더 예쁘게, 더 재미있게"},
]

SAMPLE_CURATED = [
    # 이번 주 추천
    {"app_id":"362057947",  "collection":"weekly","store":"appstore"},  # 카카오톡
    {"app_id":"544007664",  "collection":"weekly","store":"appstore"},  # YouTube
    {"app_id":"839333328",  "collection":"weekly","store":"appstore"},  # 토스
    {"app_id":"378084485",  "collection":"weekly","store":"appstore"},  # 배달의민족
    {"app_id":"393499958",  "collection":"weekly","store":"appstore"},  # 네이버
    # 생산성
    {"app_id":"1097040613", "collection":"productivity","store":"appstore"},  # Microsoft To Do
    {"app_id":"422689480",  "collection":"productivity","store":"appstore"},  # Fantastical
    {"app_id":"1274495053", "collection":"productivity","store":"appstore"},  # Notion
    {"app_id":"1018769995", "collection":"productivity","store":"appstore"},  # 당근
    # 게임
    {"app_id":"1229016807", "collection":"game","store":"appstore"},  # 브롤스타즈
    {"app_id":"529479190",  "collection":"game","store":"appstore"},  # 클래시 오브 클랜
    {"app_id":"363590051",  "collection":"game","store":"appstore"},  # Netflix
    # 사진 & 영상
    {"app_id":"1022267439", "collection":"photo","store":"appstore"},  # SNOW
    {"app_id":"1500855883", "collection":"photo","store":"appstore"},  # CapCut
    {"app_id":"389801252",  "collection":"photo","store":"appstore"},  # Instagram
]

def init_db():
    con = get_db()
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS votes (
            app_id TEXT PRIMARY KEY,
            vote_count INTEGER DEFAULT 0
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS curated (
            app_id     TEXT,
            collection TEXT,
            name       TEXT,
            icon       TEXT,
            developer  TEXT,
            category   TEXT,
            store      TEXT,
            url        TEXT,
            added_at   TEXT,
            PRIMARY KEY (app_id, collection)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS rank_history (
            id        SERIAL PRIMARY KEY,
            tab       TEXT,
            date      TEXT,
            rank      INTEGER,
            app_id    TEXT,
            name      TEXT,
            icon      TEXT,
            store     TEXT,
            developer TEXT,
            category  TEXT
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_history_tab_date ON rank_history(tab, date)")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS reviews (
            id         SERIAL PRIMARY KEY,
            app_id     TEXT,
            nickname   TEXT,
            rating     INTEGER,
            comment    TEXT,
            created_at TEXT
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_reviews_app_id ON reviews(app_id)")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id         SERIAL PRIMARY KEY,
            title      TEXT,
            content    TEXT,
            thumbnail  TEXT,
            created_at TEXT
        )
    """)
    con.commit()
    cur.execute("SELECT COUNT(*) FROM curated")
    if cur.fetchone()[0] == 0:
        seed_curated(con)
    con.close()

def seed_curated(con):
    cur = con.cursor()
    for item in SAMPLE_CURATED:
        info = fetch_itunes_info(item["app_id"])
        if not info:
            continue
        cur.execute("""
            INSERT INTO curated (app_id, collection, name, icon, developer, category, store, url, added_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (app_id, collection) DO NOTHING
        """, (item["app_id"], item["collection"], info["name"], info["icon"],
              info["developer"], info["category"], item["store"], info["url"],
              datetime.now().isoformat()))
    con.commit()

def fetch_itunes_info(app_id):
    try:
        r = requests.get(f"https://itunes.apple.com/kr/lookup?id={app_id}", timeout=8)
        results = r.json().get("results", [])
        if not results:
            return None
        d = results[0]
        return {
            "name":      d.get("trackName", ""),
            "icon":      d.get("artworkUrl100", ""),
            "developer": d.get("artistName", ""),
            "category":  d.get("primaryGenreName", ""),
            "url":       d.get("trackViewUrl", ""),
        }
    except:
        return None

init_db()

def save_rank_history(tab, apps):
    today = datetime.now().strftime("%Y-%m-%d")
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM rank_history WHERE tab=%s AND date=%s", (tab, today))
    if cur.fetchone()[0] > 0:
        con.close()
        return
    for a in apps:
        cur.execute("""
            INSERT INTO rank_history (tab, date, rank, app_id, name, icon, store, developer, category)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (tab, today, a["rank"], a["app_id"], a["name"], a["icon"],
              a["store"], a.get("developer",""), a.get("category","")))
    con.commit()
    con.close()

def get_prev_ranks(tab):
    today = datetime.now().strftime("%Y-%m-%d")
    con = get_db()
    cur = con.cursor()
    cur.execute("""
        SELECT app_id, rank, date FROM rank_history
        WHERE tab=%s AND date < %s ORDER BY date DESC
    """, (tab, today))
    rows = cur.fetchall()
    con.close()
    seen = {}
    for app_id, rank, date in rows:
        if app_id not in seen:
            seen[app_id] = rank
    return seen

def attach_rank_change(apps, tab):
    prev = get_prev_ranks(tab)
    for a in apps:
        old = prev.get(a["app_id"])
        if old is None:
            a["change"] = "new"
        elif old > a["rank"]:
            a["change"] = f"+{old - a['rank']}"
        elif old < a["rank"]:
            a["change"] = f"-{a['rank'] - old}"
        else:
            a["change"] = "0"
    return apps

def fetch_apple_rss(feed, limit=50, store="appstore", id_prefix=""):
    url = f"https://itunes.apple.com/kr/rss/{feed}/limit={limit}/json"
    try:
        r = requests.get(url, timeout=8)
        data = r.json()
        entries = data["feed"]["entry"]
        apps = []
        for i, e in enumerate(entries):
            apps.append({
                "app_id":    id_prefix + e["id"]["attributes"]["im:id"],
                "name":      e["im:name"]["label"],
                "icon":      e["im:image"][-1]["label"],
                "developer": e.get("im:artist", {}).get("label", ""),
                "category":  e.get("category", {}).get("attributes", {}).get("label", ""),
                "store":     store,
                "url":       e["id"]["label"],
                "rank":      i + 1,
            })
        return apps
    except Exception as ex:
        print(f"Apple RSS fetch error ({feed}): {ex}")
        return []

def interleave(list_a, list_b, limit=50):
    result = []
    for a, b in zip(list_a, list_b):
        result.append(a)
        result.append(b)
    for item in list_a[len(list_b):] + list_b[len(list_a):]:
        result.append(item)
    for i, a in enumerate(result):
        a["rank"] = i + 1
    return result[:limit]

def fetch_appstore_games(limit=25):
    apps = []
    try:
        r = requests.get(
            "https://itunes.apple.com/search",
            params={"term": "게임", "entity": "software", "genreId": "6014",
                    "limit": limit, "country": "kr", "lang": "ko_kr"},
            timeout=8
        )
        seen = set()
        for d in r.json().get("results", []):
            app_id = str(d.get("trackId", ""))
            if not app_id or app_id in seen:
                continue
            seen.add(app_id)
            apps.append({
                "app_id":    app_id,
                "name":      d.get("trackName", ""),
                "icon":      d.get("artworkUrl100", ""),
                "developer": d.get("artistName", ""),
                "category":  d.get("primaryGenreName", ""),
                "store":     "appstore",
                "url":       d.get("trackViewUrl", ""),
                "rank":      len(apps) + 1,
            })
    except Exception as ex:
        print(f"AppStore games fetch error: {ex}")
    return apps

def fetch_googleplay_games(limit=25):
    apps = []
    seen = set()
    try:
        from google_play_scraper import search
        game_keywords = ["브롤스타즈", "클래시오브클랜", "쿠키런킹덤", "메이플스토리M",
                         "리니지M", "배틀그라운드모바일", "원신", "포켓몬GO", "카트라이더",
                         "서머너즈워", "세븐나이츠", "로스트아크모바일"]
        for kw in game_keywords:
            if len(apps) >= limit:
                break
            results = search(kw, lang="ko", country="kr", n_hits=5)
            for r in results:
                if not r.get("appId") or not r.get("title"):
                    continue
                gp_id = "gp_" + r["appId"]
                if gp_id in seen:
                    continue
                seen.add(gp_id)
                apps.append({
                    "app_id":    gp_id,
                    "name":      r["title"],
                    "icon":      r.get("icon") or "",
                    "developer": r.get("developer") or "",
                    "category":  r.get("genre") or "",
                    "store":     "googleplay",
                    "url":       f"https://play.google.com/store/apps/details?id={r['appId']}",
                    "rank":      len(apps) + 1,
                })
    except Exception as ex:
        print(f"Google Play games fetch error: {ex}")
    return apps[:limit]

def fetch_games(limit=50):
    appstore   = fetch_appstore_games(limit // 2)
    googleplay = fetch_googleplay_games(limit // 2)
    return interleave(appstore, googleplay, limit)

def fetch_googleplay_new(limit=25):
    keywords = ["신규 앱", "새로운 앱", "2025 출시", "최신 앱", "신작",
                "새로 나온", "베타 앱", "런칭", "출시", "새 앱"]
    try:
        from google_play_scraper import search
        seen = set()
        apps = []
        for kw in keywords:
            if len(apps) >= limit:
                break
            results = search(kw, lang="ko", country="kr", n_hits=5)
            for r in results:
                if not r.get("appId") or not r.get("title"):
                    continue
                gp_id = "gp_" + r["appId"]
                if gp_id in seen:
                    continue
                seen.add(gp_id)
                apps.append({
                    "app_id":    gp_id,
                    "name":      r["title"],
                    "icon":      r.get("icon") or "",
                    "developer": r.get("developer") or "",
                    "category":  r.get("genre") or "",
                    "store":     "googleplay",
                    "url":       f"https://play.google.com/store/apps/details?id={r['appId']}",
                    "rank":      len(apps) + 1,
                })
        return apps[:limit]
    except Exception as ex:
        print(f"Google Play new fetch error: {ex}")
        return []

def fetch_googleplay_popular(limit=50):
    keywords = ["카카오", "네이버", "쿠팡", "배달의민족", "유튜브", "인스타그램",
                "틱톡", "당근마켓", "토스", "카카오페이", "무신사", "올리브영",
                "넷플릭스", "스포티파이", "라인", "밴드", "네이버지도", "카카오맵"]
    try:
        from google_play_scraper import search
        seen = set()
        apps = []
        for kw in keywords:
            if len(apps) >= limit:
                break
            results = search(kw, lang="ko", country="kr", n_hits=5)
            for r in results:
                if not r.get("appId") or not r.get("title"):
                    continue
                if r["appId"] not in seen:
                    seen.add(r["appId"])
                    apps.append({
                        "app_id":    "gp_" + r["appId"],
                        "name":      r["title"],
                        "icon":      r.get("icon") or "",
                        "developer": r.get("developer") or "",
                        "category":  r.get("genre") or "",
                        "store":     "googleplay",
                        "url":       f"https://play.google.com/store/apps/details?id={r['appId']}",
                        "rank":      len(apps) + 1,
                    })
        return apps[:limit]
    except Exception as ex:
        print(f"Google Play fetch error: {ex}")
        return []

def get_votes(app_ids):
    if not app_ids:
        return {}
    con = get_db()
    cur = con.cursor()
    placeholders = ",".join(["%s"] * len(app_ids))
    cur.execute(f"SELECT app_id, vote_count FROM votes WHERE app_id IN ({placeholders})", app_ids)
    result = {row[0]: row[1] for row in cur.fetchall()}
    con.close()
    return result

def get_curated():
    con = get_db()
    cur = con.cursor()
    cur.execute("""
        SELECT app_id, collection, name, icon, developer, category, store, url
        FROM curated ORDER BY added_at DESC
    """)
    rows = cur.fetchall()
    con.close()
    apps_by_col = {}
    for r in rows:
        col = r[1]
        if col not in apps_by_col:
            apps_by_col[col] = []
        apps_by_col[col].append({
            "app_id": r[0], "collection": r[1], "name": r[2], "icon": r[3],
            "developer": r[4], "category": r[5], "store": r[6], "url": r[7],
        })
    result = []
    for c in COLLECTIONS:
        result.append({
            "id":    c["id"],
            "emoji": c["emoji"],
            "title": c["title"],
            "desc":  c["desc"],
            "apps":  apps_by_col.get(c["id"], []),
        })
    return result

def attach_votes(apps):
    ids = [a["app_id"] for a in apps]
    votes = get_votes(ids)
    for a in apps:
        a["votes"] = votes.get(a["app_id"], 0)
    return apps

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/rankings")
def rankings():
    tab  = request.args.get("tab", "downloads")
    paid = request.args.get("paid") == "true"
    cache_key = f"rankings_{tab}_{'paid' if paid else 'free'}"
    if tab not in ("votes", "curated"):
        cached = cache_get(cache_key)
        if cached is not None:
            return jsonify(cached)

    if tab == "downloads":
        feed      = "toppaidapplications" if paid else "topfreeapplications"
        appstore  = fetch_apple_rss(feed, 25)
        googleplay = fetch_googleplay_popular(25)
        apps = interleave(appstore, googleplay, 50)
    elif tab == "revenue":
        apps = fetch_apple_rss("topgrossingapplications", 50)
    elif tab == "new":
        appstore   = fetch_apple_rss("newfreeapplications", 25)
        googleplay = fetch_googleplay_new(25)
        apps = interleave(appstore, googleplay, 50)
    elif tab == "googleplay":
        apps = fetch_googleplay_popular(50)
    elif tab == "games":
        apps = fetch_games(50)
    elif tab == "votes":
        appstore = fetch_apple_rss("topfreeapplications", 50)
        gplay    = fetch_googleplay_popular(30)
        apps     = appstore + gplay
        attach_votes(apps)
        apps.sort(key=lambda x: x["votes"], reverse=True)
        return jsonify(apps[:50])
    elif tab == "curated":
        return jsonify(get_curated())
    else:
        return jsonify([])

    attach_rank_change(apps, tab)
    save_rank_history(tab, apps)
    result = attach_votes(apps)
    cache_set(cache_key, result)
    return jsonify(result)

@app.route("/api/sparklines")
def sparklines():
    from datetime import timedelta
    tab        = request.args.get("tab", "downloads")
    app_ids    = [a.strip() for a in request.args.get("app_ids", "").split(",") if a.strip()]
    if not app_ids:
        return jsonify({})
    today = datetime.now()
    dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
    try:
        con = get_db()
        cur = con.cursor()
        placeholders = ",".join(["%s"] * len(app_ids))
        cur.execute(f"""
            SELECT app_id, date, rank FROM rank_history
            WHERE tab=%s AND app_id IN ({placeholders}) AND date >= %s
            ORDER BY date ASC
        """, [tab] + app_ids + [dates[0]])
        rows = cur.fetchall()
        con.close()
    except:
        return jsonify({})
    hist = {}
    for app_id, date, rank in rows:
        if app_id not in hist:
            hist[app_id] = {}
        hist[app_id][date] = rank
    result = {app_id: [hist.get(app_id, {}).get(d) for d in dates] for app_id in app_ids}
    return jsonify(result)

@app.route("/ping")
def ping():
    return jsonify({"status": "ok", "time": datetime.now().isoformat()})

@app.route("/api/vote", methods=["POST"])
def vote():
    data   = request.get_json()
    app_id = data.get("app_id")
    if not app_id:
        return jsonify({"error": "app_id required"}), 400
    con = get_db()
    cur = con.cursor()
    cur.execute("""
        INSERT INTO votes (app_id, vote_count) VALUES (%s, 1)
        ON CONFLICT(app_id) DO UPDATE SET vote_count = votes.vote_count + 1
    """, (app_id,))
    con.commit()
    cur.execute("SELECT vote_count FROM votes WHERE app_id = %s", (app_id,))
    count = cur.fetchone()[0]
    con.close()
    return jsonify({"app_id": app_id, "vote_count": count})

@app.route("/api/curate", methods=["POST"])
def curate():
    data     = request.get_json()
    required = ["app_id", "name", "icon", "developer", "store", "url"]
    if not all(data.get(k) for k in required):
        return jsonify({"error": "missing fields"}), 400
    con = get_db()
    cur = con.cursor()
    cur.execute("""
        INSERT INTO curated (app_id, name, icon, developer, category, store, url, added_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (app_id, collection) DO UPDATE SET
            name=EXCLUDED.name, icon=EXCLUDED.icon, developer=EXCLUDED.developer,
            category=EXCLUDED.category, store=EXCLUDED.store, url=EXCLUDED.url,
            added_at=EXCLUDED.added_at
    """, (data["app_id"], data["name"], data["icon"], data.get("developer",""),
          data.get("category",""), data["store"], data["url"],
          datetime.now().isoformat()))
    con.commit()
    con.close()
    return jsonify({"ok": True})

@app.route("/api/search")
def search_apps():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    results = []
    try:
        r = requests.get(
            "https://itunes.apple.com/search",
            params={"term": q, "entity": "software", "limit": 10, "country": "kr", "lang": "ko_kr"},
            timeout=8
        )
        for d in r.json().get("results", []):
            results.append({
                "app_id":    str(d["trackId"]),
                "name":      d.get("trackName", ""),
                "icon":      d.get("artworkUrl100", ""),
                "developer": d.get("artistName", ""),
                "category":  d.get("primaryGenreName", ""),
                "store":     "appstore",
                "url":       d.get("trackViewUrl", ""),
                "rating":    round(d.get("averageUserRating") or 0, 1),
            })
    except Exception as ex:
        print(f"AppStore search error: {ex}")
    try:
        from google_play_scraper import search as gp_search
        gp_results = gp_search(q, lang="ko", country="kr", n_hits=10)
        for d in gp_results:
            if not d.get("appId") or not d.get("title"):
                continue
            results.append({
                "app_id":    "gp_" + d["appId"],
                "name":      d["title"],
                "icon":      d.get("icon") or "",
                "developer": d.get("developer") or "",
                "category":  d.get("genre") or "",
                "store":     "googleplay",
                "url":       f"https://play.google.com/store/apps/details?id={d['appId']}",
                "rating":    round(d.get("score") or 0, 1),
            })
    except Exception as ex:
        print(f"GooglePlay search error: {ex}")
    return jsonify(results)

@app.route("/api/history")
def history():
    tab  = request.args.get("tab", "downloads")
    date = request.args.get("date", "")
    con  = get_db()
    cur  = con.cursor()
    if date:
        cur.execute("""
            SELECT rank, app_id, name, icon, store, developer, category
            FROM rank_history WHERE tab=%s AND date=%s ORDER BY rank
        """, (tab, date))
        rows = cur.fetchall()
        con.close()
        return jsonify([{
            "rank": r[0], "app_id": r[1], "name": r[2],
            "icon": r[3], "store": r[4], "developer": r[5], "category": r[6]
        } for r in rows])
    else:
        cur.execute("""
            SELECT DISTINCT date FROM rank_history
            WHERE tab=%s ORDER BY date DESC LIMIT 90
        """, (tab,))
        dates = [r[0] for r in cur.fetchall()]
        con.close()
        return jsonify(dates)

@app.route("/weekly")
def weekly():
    return render_template("weekly.html")

@app.route("/api/weekly")
def api_weekly():
    con = get_db()
    cur = con.cursor()
    cur.execute("""
        SELECT v.app_id, v.vote_count
        FROM votes v ORDER BY v.vote_count DESC LIMIT 10
    """)
    rows = cur.fetchall()
    con.close()
    if not rows:
        apps = fetch_apple_rss("topfreeapplications", 10)
        return jsonify(attach_votes(apps))
    result = []
    for rank, (app_id, vote_count) in enumerate(rows, 1):
        if app_id.startswith("gp_"):
            gp_id = app_id[3:]
            try:
                from google_play_scraper import app as gp_app
                d = gp_app(gp_id, lang="ko", country="kr")
                info = {
                    "name":      d.get("title", gp_id),
                    "icon":      d.get("icon", ""),
                    "developer": d.get("developer", ""),
                    "category":  d.get("genre", ""),
                    "store":     "googleplay",
                    "url":       f"https://play.google.com/store/apps/details?id={gp_id}"
                }
            except:
                info = {"name": gp_id, "icon": "", "developer": "", "store": "googleplay",
                        "url": "", "category": ""}
        else:
            info = fetch_itunes_info(app_id) or {}
            info["store"] = "appstore"
            info["url"]   = info.get("url", "")
        result.append({
            "rank":      rank,
            "app_id":    app_id,
            "name":      info.get("name", ""),
            "icon":      info.get("icon", ""),
            "developer": info.get("developer", ""),
            "category":  info.get("category", ""),
            "store":     info.get("store", "appstore"),
            "url":       info.get("url", ""),
            "votes":     vote_count,
        })
    return jsonify(result)

@app.route("/privacy")
def privacy():
    return render_template("privacy.html")

@app.route("/app/<app_id>")
def app_detail(app_id):
    app_data = {}
    try:
        if app_id.startswith("gp_"):
            gp_id = app_id[3:]
            from google_play_scraper import app as gp_app
            d = gp_app(gp_id, lang="ko", country="kr")
            app_data = {
                "name":        d.get("title", ""),
                "icon":        d.get("icon", ""),
                "developer":   d.get("developer", ""),
                "category":    d.get("genre", ""),
                "description": (d.get("description") or "")[:200],
                "store":       "googleplay",
                "url":         f"https://play.google.com/store/apps/details?id={gp_id}",
            }
        else:
            r = requests.get(f"https://itunes.apple.com/kr/lookup?id={app_id}", timeout=8)
            results = r.json().get("results", [])
            if results:
                d = results[0]
                app_data = {
                    "name":        d.get("trackName", ""),
                    "icon":        d.get("artworkUrl100", ""),
                    "developer":   d.get("artistName", ""),
                    "category":    d.get("primaryGenreName", ""),
                    "description": (d.get("description") or "")[:200],
                    "store":       "appstore",
                    "url":         d.get("trackViewUrl", ""),
                }
    except:
        pass
    return render_template("app_detail.html", app_id=app_id, app=app_data)

@app.route("/api/app/<app_id>")
def api_app_detail(app_id):
    if app_id.startswith("gp_"):
        gp_id = app_id[3:]
        try:
            from google_play_scraper import app as gp_app
            d = gp_app(gp_id, lang="ko", country="kr")
            return jsonify({
                "app_id":       app_id,
                "name":         d.get("title", ""),
                "icon":         d.get("icon", ""),
                "developer":    d.get("developer", ""),
                "category":     d.get("genre", ""),
                "rating":       round(d.get("score") or 0, 1),
                "rating_count": d.get("ratings", 0),
                "description":  d.get("description", ""),
                "screenshots":  d.get("screenshots", [])[:5],
                "version":      d.get("version", ""),
                "updated":      d.get("updated", ""),
                "store":        "googleplay",
                "url":          f"https://play.google.com/store/apps/details?id={gp_id}",
            })
        except Exception as ex:
            return jsonify({"error": str(ex)}), 404
    else:
        try:
            r = requests.get(f"https://itunes.apple.com/kr/lookup?id={app_id}", timeout=8)
            results = r.json().get("results", [])
            if not results:
                return jsonify({"error": "not found"}), 404
            d = results[0]
            return jsonify({
                "app_id":       app_id,
                "name":         d.get("trackName", ""),
                "icon":         d.get("artworkUrl512") or d.get("artworkUrl100", ""),
                "developer":    d.get("artistName", ""),
                "category":     d.get("primaryGenreName", ""),
                "rating":       round(d.get("averageUserRating") or 0, 1),
                "rating_count": d.get("userRatingCount", 0),
                "description":  d.get("description", ""),
                "screenshots":  d.get("screenshotUrls", [])[:5],
                "version":      d.get("version", ""),
                "updated":      d.get("currentVersionReleaseDate", "")[:10],
                "store":        "appstore",
                "url":          d.get("trackViewUrl", ""),
                "price":        d.get("formattedPrice", "무료"),
            })
        except Exception as ex:
            return jsonify({"error": str(ex)}), 404

@app.route("/api/posts")
def get_posts():
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT id, title, thumbnail, created_at, content FROM posts ORDER BY created_at DESC LIMIT 20")
    rows = cur.fetchall()
    con.close()
    result = []
    for r in rows:
        content = r[4] or ""
        read_min = max(1, round(len(content) / 300))
        result.append({
            "id": r[0], "title": r[1], "thumbnail": r[2],
            "created_at": r[3][:10], "read_min": read_min,
            "excerpt": content[:80] + "..." if len(content) > 80 else content
        })
    return jsonify(result)

@app.route("/api/posts/<int:post_id>")
def get_post(post_id):
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT id, title, content, thumbnail, created_at FROM posts WHERE id=%s", (post_id,))
    r = cur.fetchone()
    con.close()
    if not r:
        return jsonify({"error": "not found"}), 404
    return jsonify({"id": r[0], "title": r[1], "content": r[2], "thumbnail": r[3], "created_at": r[4][:10]})

@app.route("/post/<int:post_id>")
def post_detail(post_id):
    return render_template("post_detail.html", post_id=post_id)

@app.route("/api/reviews/<app_id>")
def get_reviews(app_id):
    con = get_db()
    cur = con.cursor()
    cur.execute("""
        SELECT id, nickname, rating, comment, created_at
        FROM reviews WHERE app_id=%s ORDER BY created_at DESC LIMIT 50
    """, (app_id,))
    rows = cur.fetchall()
    con.close()
    return jsonify([{
        "id": r[0], "nickname": r[1], "rating": r[2],
        "comment": r[3], "created_at": r[4][:10]
    } for r in rows])

@app.route("/api/reviews/<app_id>", methods=["POST"])
def post_review(app_id):
    data     = request.get_json()
    nickname = (data.get("nickname") or "").strip()[:20]
    rating   = int(data.get("rating") or 0)
    comment  = (data.get("comment") or "").strip()[:200]
    if not nickname or not comment or not (1 <= rating <= 5):
        return jsonify({"error": "입력값을 확인해주세요"}), 400
    con = get_db()
    cur = con.cursor()
    cur.execute("""
        INSERT INTO reviews (app_id, nickname, rating, comment, created_at)
        VALUES (%s,%s,%s,%s,%s)
    """, (app_id, nickname, rating, comment, datetime.now().isoformat()))
    con.commit()
    con.close()
    return jsonify({"ok": True})

@app.route("/admin", methods=["GET", "POST"])
def admin():
    if request.method == "POST" and "password" in request.form:
        if request.form["password"] == ADMIN_PASSWORD:
            session["admin"] = True
        else:
            return render_template("admin.html", error="비밀번호가 틀렸어요", logged_in=False, collections=COLLECTIONS)
    if not session.get("admin"):
        return render_template("admin.html", logged_in=False, collections=COLLECTIONS)
    curated = get_curated()
    return render_template("admin.html", logged_in=True, collections=COLLECTIONS, curated=curated)

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect("/admin")

@app.route("/admin/post", methods=["POST"])
def admin_post():
    if not session.get("admin"):
        return jsonify({"error": "unauthorized"}), 401
    data      = request.get_json()
    title     = (data.get("title") or "").strip()
    content   = (data.get("content") or "").strip()
    thumbnail = (data.get("thumbnail") or "").strip()
    if not title or not content:
        return jsonify({"error": "제목과 내용을 입력해주세요"}), 400
    con = get_db()
    cur = con.cursor()
    cur.execute("INSERT INTO posts (title, content, thumbnail, created_at) VALUES (%s,%s,%s,%s)",
                (title, content, thumbnail, datetime.now().isoformat()))
    con.commit()
    con.close()
    return jsonify({"ok": True})

@app.route("/admin/post/<int:post_id>/edit", methods=["POST"])
def admin_edit_post(post_id):
    if not session.get("admin"):
        return jsonify({"error": "unauthorized"}), 401
    data      = request.get_json()
    title     = data.get("title", "").strip()
    content   = data.get("content", "").strip()
    thumbnail = data.get("thumbnail", "").strip()
    if not title or not content:
        return jsonify({"error": "missing fields"}), 400
    con = get_db()
    cur = con.cursor()
    cur.execute("UPDATE posts SET title=%s, content=%s, thumbnail=%s WHERE id=%s",
                (title, content, thumbnail or None, post_id))
    con.commit()
    con.close()
    return jsonify({"ok": True})

@app.route("/admin/post/<int:post_id>/delete", methods=["POST"])
def admin_delete_post(post_id):
    if not session.get("admin"):
        return jsonify({"error": "unauthorized"}), 401
    con = get_db()
    cur = con.cursor()
    cur.execute("DELETE FROM posts WHERE id=%s", (post_id,))
    con.commit()
    con.close()
    return jsonify({"ok": True})

@app.route("/admin/add", methods=["POST"])
def admin_add():
    if not session.get("admin"):
        return jsonify({"error": "unauthorized"}), 401
    data       = request.get_json()
    app_id     = data.get("app_id", "").strip()
    collection = data.get("collection", "").strip()
    store      = data.get("store", "appstore")
    if not app_id or not collection:
        return jsonify({"error": "missing fields"}), 400
    if store == "appstore":
        info = fetch_itunes_info(app_id)
    else:
        try:
            from google_play_scraper import app as gp_app
            gp_id  = app_id[3:] if app_id.startswith("gp_") else app_id
            app_id = "gp_" + gp_id
            d      = gp_app(gp_id, lang="ko", country="kr")
            info   = {"name": d.get("title",""), "icon": d.get("icon",""),
                      "developer": d.get("developer",""), "category": d.get("genre",""),
                      "url": f"https://play.google.com/store/apps/details?id={gp_id}"}
        except:
            info = None
    if not info:
        return jsonify({"error": "앱 정보를 찾지 못했어요"}), 404
    con = get_db()
    cur = con.cursor()
    cur.execute("""
        INSERT INTO curated (app_id, collection, name, icon, developer, category, store, url, added_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (app_id, collection) DO UPDATE SET
            name=EXCLUDED.name, icon=EXCLUDED.icon, developer=EXCLUDED.developer,
            category=EXCLUDED.category, store=EXCLUDED.store, url=EXCLUDED.url,
            added_at=EXCLUDED.added_at
    """, (app_id, collection, info["name"], info["icon"], info["developer"],
          info["category"], store, info["url"], datetime.now().isoformat()))
    con.commit()
    con.close()
    return jsonify({"ok": True, "name": info["name"]})

@app.route("/admin/delete", methods=["POST"])
def admin_delete():
    if not session.get("admin"):
        return jsonify({"error": "unauthorized"}), 401
    data       = request.get_json()
    app_id     = data.get("app_id")
    collection = data.get("collection")
    con = get_db()
    cur = con.cursor()
    cur.execute("DELETE FROM curated WHERE app_id=%s AND collection=%s", (app_id, collection))
    con.commit()
    con.close()
    return jsonify({"ok": True})

@app.route("/sitemap.xml")
def sitemap():
    urls = ['  <url>\n    <loc>https://www.apppedia.co.kr/</loc>\n    <changefreq>hourly</changefreq>\n    <priority>1.0</priority>\n  </url>']

    # 에디터픽 앱 페이지
    try:
        con = get_db()
        cur = con.cursor()
        cur.execute("SELECT DISTINCT app_id FROM curated")
        for (app_id,) in cur.fetchall():
            urls.append(f'  <url>\n    <loc>https://www.apppedia.co.kr/app/{app_id}</loc>\n    <changefreq>daily</changefreq>\n    <priority>0.8</priority>\n  </url>')

        # 트렌드 글 페이지
        cur.execute("SELECT id FROM posts ORDER BY created_at DESC")
        for (post_id,) in cur.fetchall():
            urls.append(f'  <url>\n    <loc>https://www.apppedia.co.kr/post/{post_id}</loc>\n    <changefreq>weekly</changefreq>\n    <priority>0.7</priority>\n  </url>')
        con.close()
    except:
        pass

    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    xml += '\n'.join(urls)
    xml += '\n</urlset>'
    return app.response_class(xml, mimetype="application/xml")

@app.route("/robots.txt")
def robots():
    txt = """User-agent: *
Allow: /
Sitemap: https://www.apppedia.co.kr/sitemap.xml"""
    return app.response_class(txt, mimetype="text/plain")

if __name__ == "__main__":
    app.run(debug=True, port=5000)

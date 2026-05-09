from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
import requests
import sqlite3
import os
from datetime import datetime

app = Flask(__name__)
CORS(app)

DB_PATH = os.path.join(os.path.dirname(__file__), "appranking.db")

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
    con = sqlite3.connect(DB_PATH)
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
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            tab      TEXT,
            date     TEXT,
            rank     INTEGER,
            app_id   TEXT,
            name     TEXT,
            icon     TEXT,
            store    TEXT,
            developer TEXT,
            category  TEXT
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_history_tab_date ON rank_history(tab, date)")
    con.commit()
    # 샘플 데이터 없으면 채우기
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
            INSERT OR IGNORE INTO curated (app_id, collection, name, icon, developer, category, store, url, added_at)
            VALUES (?,?,?,?,?,?,?,?,?)
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
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM rank_history WHERE tab=? AND date=?", (tab, today))
    if cur.fetchone()[0] > 0:
        con.close()
        return
    for a in apps:
        cur.execute("""
            INSERT INTO rank_history (tab, date, rank, app_id, name, icon, store, developer, category)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (tab, today, a["rank"], a["app_id"], a["name"], a["icon"],
              a["store"], a.get("developer",""), a.get("category","")))
    con.commit()
    con.close()

def get_prev_ranks(tab):
    today = datetime.now().strftime("%Y-%m-%d")
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        SELECT app_id, rank, date FROM rank_history
        WHERE tab=? AND date < ? ORDER BY date DESC
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
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    placeholders = ",".join("?" * len(app_ids))
    cur.execute(f"SELECT app_id, vote_count FROM votes WHERE app_id IN ({placeholders})", app_ids)
    result = {row[0]: row[1] for row in cur.fetchall()}
    con.close()
    return result

def get_curated():
    con = sqlite3.connect(DB_PATH)
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
    tab = request.args.get("tab", "downloads")

    if tab == "downloads":
        apps = fetch_apple_rss("topfreeapplications", 50)
    elif tab == "revenue":
        apps = fetch_apple_rss("topgrossingapplications", 50)
    elif tab == "new":
        apps = fetch_apple_rss("newfreeapplications", 50)
    elif tab == "googleplay":
        apps = fetch_googleplay_popular(50)
    elif tab == "votes":
        appstore = fetch_apple_rss("topfreeapplications", 50)
        gplay = fetch_googleplay_popular(30)
        apps = appstore + gplay
        attach_votes(apps)
        apps.sort(key=lambda x: x["votes"], reverse=True)
        return jsonify(apps[:50])
    elif tab == "curated":
        return jsonify(get_curated())
    else:
        return jsonify([])

    attach_rank_change(apps, tab)
    save_rank_history(tab, apps)
    return jsonify(attach_votes(apps))

@app.route("/api/vote", methods=["POST"])
def vote():
    data = request.get_json()
    app_id = data.get("app_id")
    if not app_id:
        return jsonify({"error": "app_id required"}), 400
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        INSERT INTO votes (app_id, vote_count) VALUES (?, 1)
        ON CONFLICT(app_id) DO UPDATE SET vote_count = vote_count + 1
    """, (app_id,))
    con.commit()
    cur.execute("SELECT vote_count FROM votes WHERE app_id = ?", (app_id,))
    count = cur.fetchone()[0]
    con.close()
    return jsonify({"app_id": app_id, "vote_count": count})

@app.route("/api/curate", methods=["POST"])
def curate():
    data = request.get_json()
    required = ["app_id", "name", "icon", "developer", "store", "url"]
    if not all(data.get(k) for k in required):
        return jsonify({"error": "missing fields"}), 400
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO curated (app_id, name, icon, developer, category, store, url, added_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (data["app_id"], data["name"], data["icon"], data.get("developer",""),
          data.get("category",""), data["store"], data["url"],
          datetime.now().isoformat()))
    con.commit()
    con.close()
    return jsonify({"ok": True})

@app.route("/api/history")
def history():
    tab  = request.args.get("tab", "downloads")
    date = request.args.get("date", "")
    con  = sqlite3.connect(DB_PATH)
    cur  = con.cursor()
    if date:
        cur.execute("""
            SELECT rank, app_id, name, icon, store, developer, category
            FROM rank_history WHERE tab=? AND date=? ORDER BY rank
        """, (tab, date))
    else:
        cur.execute("""
            SELECT DISTINCT date FROM rank_history
            WHERE tab=? ORDER BY date DESC LIMIT 90
        """, (tab,))
        dates = [r[0] for r in cur.fetchall()]
        con.close()
        return jsonify(dates)
    rows = cur.fetchall()
    con.close()
    return jsonify([{
        "rank": r[0], "app_id": r[1], "name": r[2],
        "icon": r[3], "store": r[4], "developer": r[5], "category": r[6]
    } for r in rows])

@app.route("/app/<app_id>")
def app_detail(app_id):
    return render_template("app_detail.html", app_id=app_id)

@app.route("/api/app/<app_id>")
def api_app_detail(app_id):
    if app_id.startswith("gp_"):
        gp_id = app_id[3:]
        try:
            from google_play_scraper import app as gp_app
            d = gp_app(gp_id, lang="ko", country="kr")
            return jsonify({
                "app_id":      app_id,
                "name":        d.get("title", ""),
                "icon":        d.get("icon", ""),
                "developer":   d.get("developer", ""),
                "category":    d.get("genre", ""),
                "rating":      round(d.get("score") or 0, 1),
                "rating_count":d.get("ratings", 0),
                "description": d.get("description", ""),
                "screenshots": d.get("screenshots", [])[:5],
                "version":     d.get("version", ""),
                "updated":     d.get("updated", ""),
                "store":       "googleplay",
                "url":         f"https://play.google.com/store/apps/details?id={gp_id}",
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
                "app_id":      app_id,
                "name":        d.get("trackName", ""),
                "icon":        d.get("artworkUrl512") or d.get("artworkUrl100", ""),
                "developer":   d.get("artistName", ""),
                "category":    d.get("primaryGenreName", ""),
                "rating":      round(d.get("averageUserRating") or 0, 1),
                "rating_count":d.get("userRatingCount", 0),
                "description": d.get("description", ""),
                "screenshots": d.get("screenshotUrls", [])[:5],
                "version":     d.get("version", ""),
                "updated":     d.get("currentVersionReleaseDate", "")[:10],
                "store":       "appstore",
                "url":         d.get("trackViewUrl", ""),
                "price":       d.get("formattedPrice", "무료"),
            })
        except Exception as ex:
            return jsonify({"error": str(ex)}), 404

@app.route("/sitemap.xml")
def sitemap():
    xml = '''<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://apppedia.onrender.com/</loc>
    <changefreq>hourly</changefreq>
    <priority>1.0</priority>
  </url>
</urlset>'''
    return app.response_class(xml, mimetype="application/xml")

@app.route("/robots.txt")
def robots():
    txt = """User-agent: *
Allow: /
Sitemap: https://apppedia.onrender.com/sitemap.xml"""
    return app.response_class(txt, mimetype="text/plain")

if __name__ == "__main__":
    app.run(debug=True, port=5000)

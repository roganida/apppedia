from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
import requests
import sqlite3
import os
from datetime import datetime

app = Flask(__name__)
CORS(app)

DB_PATH = os.path.join(os.path.dirname(__file__), "appranking.db")

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
            app_id    TEXT PRIMARY KEY,
            name      TEXT,
            icon      TEXT,
            developer TEXT,
            category  TEXT,
            store     TEXT,
            url       TEXT,
            added_at  TEXT
        )
    """)
    con.commit()
    con.close()

init_db()

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
    cur.execute("SELECT app_id, name, icon, developer, category, store, url, added_at FROM curated ORDER BY added_at DESC")
    rows = cur.fetchall()
    con.close()
    return [{"app_id": r[0], "name": r[1], "icon": r[2], "developer": r[3],
             "category": r[4], "store": r[5], "url": r[6], "added_at": r[7]} for r in rows]

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

if __name__ == "__main__":
    app.run(debug=True, port=5000)

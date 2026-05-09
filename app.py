from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
import requests
import json
import sqlite3
import os
from datetime import datetime

app = Flask(__name__)
CORS(app)

DB_PATH = os.path.join(os.path.dirname(__file__), "appranking.db")

# ── DB 초기화 ──────────────────────────────────────────
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

# ── App Store RSS (상위 100개) ─────────────────────────
def fetch_appstore(limit=50):
    url = f"https://itunes.apple.com/kr/rss/topfreeapplications/limit={limit}/json"
    try:
        r = requests.get(url, timeout=8)
        data = r.json()
        entries = data["feed"]["entry"]
        apps = []
        for i, e in enumerate(entries):
            apps.append({
                "app_id":    e["id"]["attributes"]["im:id"],
                "name":      e["im:name"]["label"],
                "icon":      e["im:image"][-1]["label"],
                "developer": e.get("im:artist", {}).get("label", ""),
                "category":  e.get("category", {}).get("attributes", {}).get("label", ""),
                "store":     "appstore",
                "url":       e["id"]["label"],
                "rank":      i + 1,
                "genre":     e.get("category", {}).get("attributes", {}).get("label", ""),
            })
        return apps
    except Exception as ex:
        print(f"AppStore fetch error: {ex}")
        return []

# ── Google Play (공개 RSS / 대체 방식) ────────────────
def fetch_googleplay(limit=50):
    try:
        r = requests.get(
            f"https://itunes.apple.com/kr/rss/topgrossingapplications/limit={limit}/json",
            timeout=8
        )
        data = r.json()
        entries = data["feed"]["entry"]
        apps = []
        for i, e in enumerate(entries):
            apps.append({
                "app_id":    "gp_" + e["id"]["attributes"]["im:id"],
                "name":      e["im:name"]["label"],
                "icon":      e["im:image"][-1]["label"],
                "developer": e.get("im:artist", {}).get("label", ""),
                "category":  e.get("category", {}).get("attributes", {}).get("label", ""),
                "store":     "googleplay",
                "url":       e["id"]["label"],
                "rank":      i + 1,
                "genre":     e.get("category", {}).get("attributes", {}).get("label", ""),
            })
        return apps
    except Exception as ex:
        print(f"GooglePlay fetch error: {ex}")
        return []

# ── 투표 수 조회 ──────────────────────────────────────
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

# ── 큐레이션 목록 ─────────────────────────────────────
def get_curated():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT app_id, name, icon, developer, category, store, url, added_at FROM curated ORDER BY added_at DESC")
    rows = cur.fetchall()
    con.close()
    return [{"app_id": r[0], "name": r[1], "icon": r[2], "developer": r[3],
             "category": r[4], "store": r[5], "url": r[6], "added_at": r[7]} for r in rows]

# ── 라우트 ────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/rankings")
def rankings():
    tab = request.args.get("tab", "downloads")  # downloads | popularity | votes | curated

    if tab == "downloads":
        apps = fetch_appstore(50)
        ids = [a["app_id"] for a in apps]
        votes = get_votes(ids)
        for a in apps:
            a["votes"] = votes.get(a["app_id"], 0)
        return jsonify(apps)

    elif tab == "popularity":
        apps = fetch_googleplay(50)
        ids = [a["app_id"] for a in apps]
        votes = get_votes(ids)
        for a in apps:
            a["votes"] = votes.get(a["app_id"], 0)
        return jsonify(apps)

    elif tab == "votes":
        appstore = fetch_appstore(50)
        googleplay = fetch_googleplay(50)
        all_apps = appstore + googleplay
        ids = [a["app_id"] for a in all_apps]
        votes = get_votes(ids)
        for a in all_apps:
            a["votes"] = votes.get(a["app_id"], 0)
        all_apps.sort(key=lambda x: x["votes"], reverse=True)
        # 투표 0인 앱은 원래 순위 유지
        return jsonify(all_apps[:50])

    elif tab == "curated":
        return jsonify(get_curated())

    return jsonify([])

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

import os
import time
import json
import urllib.parse

import requests
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import RedirectResponse

load_dotenv()

CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI")
SCOPES = (
    "user-top-read user-read-recently-played "
    "user-read-playback-state user-modify-playback-state "
    "playlist-modify-private playlist-modify-public"
)
TOKEN_FILE = "tokens.json"

app = FastAPI(title="Moodify")


@app.get("/")
def home():
    return {"status": "Moodify is running", "next": "open /login"}


@app.get("/login")
def login():
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
    }
    url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode(params)
    return RedirectResponse(url)


@app.get("/callback")
def callback(code: str = None, error: str = None):
    if error:
        return {"error": error}
    r = requests.post(
        "https://accounts.spotify.com/api/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
        },
        auth=(CLIENT_ID, CLIENT_SECRET),
    )
    if r.status_code != 200:
        return {"error": r.text}
    tokens = r.json()
    tokens["expires_at"] = time.time() + tokens["expires_in"]
    with open(TOKEN_FILE, "w") as f:
        json.dump(tokens, f)
    return {"message": "Logged in! Tokens saved."}

def get_token():
    with open(TOKEN_FILE) as f:
        tokens = json.load(f)
    if time.time() > tokens["expires_at"] - 60:
        r = requests.post(
            "https://accounts.spotify.com/api/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
            },
            auth=(CLIENT_ID, CLIENT_SECRET),
        )
        new = r.json()
        tokens["access_token"] = new["access_token"]
        tokens["expires_at"] = time.time() + new["expires_in"]
        if "refresh_token" in new:
            tokens["refresh_token"] = new["refresh_token"]
        with open(TOKEN_FILE, "w") as f:
            json.dump(tokens, f)
    return tokens["access_token"]


def spotify_get(path, params=None):
    return requests.get(
        "https://api.spotify.com/v1" + path,
        headers={"Authorization": "Bearer " + get_token()},
        params=params,
    )


@app.get("/top")
def top(time_range: str = "short_term", limit: int = 10):
    r = spotify_get("/me/top/tracks", {"time_range": time_range, "limit": limit})
    if r.status_code != 200:
        return {"error": r.status_code, "detail": r.text}
    return [
        {
            "track": t["name"],
            "artist": ", ".join(a["name"] for a in t["artists"]),
            "id": t["id"],
        }
        for t in r.json()["items"]
    ]

@app.get("/devices")
def devices():
    r = spotify_get("/me/player/devices")
    if r.status_code != 200:
        return {"error": r.status_code, "detail": r.text}
    return [
        {"name": d["name"], "type": d["type"], "id": d["id"], "active": d["is_active"]}
        for d in r.json()["devices"]
    ]


@app.get("/play")
def play(track_id: str, device_id: str = None):
    params = {"device_id": device_id} if device_id else None
    r = requests.put(
        "https://api.spotify.com/v1/me/player/play",
        headers={"Authorization": "Bearer " + get_token()},
        params=params,
        json={"uris": ["spotify:track:" + track_id]},
    )
    if r.status_code in (200, 202, 204):
        return {"playing": track_id}
    return {"error": r.status_code, "detail": r.text}

import sqlite3

DB_FILE = "moodify.db"


def init_db():
    con = sqlite3.connect(DB_FILE)
    con.execute(
        """CREATE TABLE IF NOT EXISTS plays (
            played_at TEXT,
            track_id TEXT,
            track TEXT,
            artist TEXT,
            duration_ms INTEGER,
            PRIMARY KEY (played_at, track_id)
        )"""
    )
    con.commit()
    con.close()


init_db()


@app.get("/sync")
def sync():
    r = spotify_get("/me/player/recently-played", {"limit": 50})
    if r.status_code != 200:
        return {"error": r.status_code, "detail": r.text}
    con = sqlite3.connect(DB_FILE)
    added = 0
    for item in r.json()["items"]:
        t = item["track"]
        cur = con.execute(
            "INSERT OR IGNORE INTO plays VALUES (?, ?, ?, ?, ?)",
            (
                item["played_at"],
                t["id"],
                t["name"],
                ", ".join(a["name"] for a in t["artists"]),
                t["duration_ms"],
            ),
        )
        added += cur.rowcount
    con.commit()
    total = con.execute("SELECT COUNT(*) FROM plays").fetchone()[0]
    con.close()
    return {"new_plays_saved": added, "total_in_database": total}

from collections import Counter
from datetime import datetime, timedelta, timezone


def parse_time(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


@app.get("/stats")
def stats(period: str = "week"):
    days = 7 if period == "week" else 30
    since = datetime.now(timezone.utc) - timedelta(days=days)

    con = sqlite3.connect(DB_FILE)
    rows = con.execute(
        "SELECT played_at, track, artist, duration_ms FROM plays ORDER BY played_at"
    ).fetchall()
    con.close()

    plays = []
    for played_at, track, artist, dur in rows:
        t = parse_time(played_at)
        if t >= since:
            plays.append((t, track, artist.split(", ")[0], dur))

    if not plays:
        return {"message": "No plays in this period yet. Play music, then open /sync."}

    # sessions: a gap of more than 30 minutes starts a new session
    session_minutes = []
    first = prev = plays[0][0]
    last_dur = plays[0][3]
    for t, _, _, dur in plays[1:]:
        if t - prev > timedelta(minutes=30):
            session_minutes.append((prev - first).total_seconds() / 60 + last_dur / 60000)
            first = t
        prev = t
        last_dur = dur
    session_minutes.append((prev - first).total_seconds() / 60 + last_dur / 60000)

    track_counts = Counter((p[1], p[2]) for p in plays)
    artist_counts = Counter(p[2] for p in plays)
    total = len(plays)
    top3_share = sum(c for _, c in artist_counts.most_common(3)) / total

    per_day = Counter((p[0].date(), p[1]) for p in plays)
    max_repeat_in_day = max(per_day.values())

    avg_session = sum(session_minutes) / len(session_minutes)
    is_binge = avg_session > 90 and (top3_share > 0.4 or max_repeat_in_day >= 5)

    return {
        "period": period,
        "total_plays": total,
        "top_tracks": [
            {"track": t, "artist": a, "plays": c}
            for (t, a), c in track_counts.most_common(5)
        ],
        "top_artists": [
            {"artist": a, "plays": c} for a, c in artist_counts.most_common(5)
        ],
        "sessions": len(session_minutes),
        "avg_session_minutes": round(avg_session, 1),
        "longest_session_minutes": round(max(session_minutes), 1),
        "top3_artist_share_percent": round(top3_share * 100, 1),
        "max_repeats_of_one_track_in_a_day": max_repeat_in_day,
        "binge_listener": is_binge,
    }

from apscheduler.schedulers.background import BackgroundScheduler


def auto_sync():
    try:
        print("Auto-sync:", sync())
    except Exception as e:
        print("Auto-sync failed:", e)


scheduler = BackgroundScheduler()
scheduler.add_job(auto_sync, "interval", minutes=20)
scheduler.start()

def parse_mood(text):
    prompt = (
        "You turn a person's mood description into a music profile. "
        "Reply with ONLY a JSON object with these keys: "
        '"moods" (list of 3 to 5 short mood words), '
        '"energy" (one of "low", "medium", "high"), '
        '"genres" (list of 2 to 4 music genres that fit), '
        '"search_text" (one short sentence describing the music that fits). '
        "Mood description: " + text
    )
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": "Bearer " + os.getenv("GROQ_API_KEY", "")},
        json={
            "model": "openai/gpt-oss-20b",
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "temperature": 0.3,
        },
    )
    if r.status_code != 200:
        return {"error": r.status_code, "detail": r.text}
    return json.loads(r.json()["choices"][0]["message"]["content"])


@app.get("/mood")
def mood(text: str):
    return parse_mood(text)

@app.get("/groq-models")
def groq_models():
    r = requests.get(
        "https://api.groq.com/openai/v1/models",
        headers={"Authorization": "Bearer " + os.getenv("GROQ_API_KEY", "")},
    )
    if r.status_code != 200:
        return {"error": r.status_code, "detail": r.text}
    return [m["id"] for m in r.json()["data"]]

import chromadb

chroma_client = chromadb.PersistentClient(path="chroma_db")
collection = chroma_client.get_or_create_collection("tracks")
_embedder = None


def embed(texts):
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer

        _embedder = SentenceTransformer("all-MiniLM-L6-v2")
    return _embedder.encode(texts).tolist()


CATALOGUE_QUERIES = [
    ("calm instrumental focus", "calm, peaceful, focused, low energy"),
    ("lofi chill study beats", "chill, relaxed, focused, low energy"),
    ("soft acoustic relaxing", "soft, relaxed, gentle, low energy"),
    ("peaceful piano", "peaceful, calm, soothing, low energy"),
    ("sleep music", "sleepy, calm, soothing, very low energy"),
    ("sufi songs", "spiritual, soulful, calm, medium energy"),
    ("rainy day songs", "nostalgic, mellow, cozy, low energy"),
    ("sad hindi songs", "sad, heartbroken, emotional, low energy"),
    ("sad english songs", "sad, lonely, emotional, low energy"),
    ("breakup songs", "heartbroken, sad, hurt, low energy"),
    ("romantic hindi songs", "romantic, love, tender, medium energy"),
    ("love songs acoustic", "romantic, love, warm, low energy"),
    ("nostalgic 90s hindi", "nostalgic, sentimental, warm, medium energy"),
    ("happy upbeat pop", "happy, cheerful, upbeat, high energy"),
    ("feel good songs", "happy, positive, feel good, medium energy"),
    ("road trip songs", "adventurous, free, upbeat, medium energy"),
    ("punjabi party songs", "party, dance, energetic, high energy"),
    ("bollywood dance hits", "dance, party, fun, high energy"),
    ("workout gym motivation", "workout, intense, powerful, high energy"),
    ("hip hop energy", "confident, hype, energetic, high energy"),
    ("punjabi hip hop", "confident, swag, energetic, high energy"),
    ("motivational songs", "motivated, determined, confident, high energy"),
    ("angry rock", "angry, intense, aggressive, high energy"),
]


@app.get("/build-catalogue")
def build_catalogue():
    found = {}
    errors = []
    for query, tags in CATALOGUE_QUERIES:
        r = spotify_get("/search", {"q": query, "type": "track", "limit": 10})
        if r.status_code != 200:
            errors.append({"query": query, "status": r.status_code})
            continue
        for t in r.json()["tracks"]["items"]:
            if not t:
                continue
            artist = ", ".join(a["name"] for a in t["artists"])
            found[t["id"]] = {
                   "doc": "Mood: " + tags + ". Music that feels " + tags + ".",
                   "meta": {"track": t["name"], "artist": artist, "tags": tags, "energy": "low" if "low energy" in tags else ("high" if "high energy" in tags else "medium")},
            }
        time.sleep(0.3)
    if found:
        ids = list(found.keys())
        collection.upsert(
            ids=ids,
            documents=[found[i]["doc"] for i in ids],
            metadatas=[found[i]["meta"] for i in ids],
            embeddings=embed([found[i]["doc"] for i in ids]),
        )
    return {
        "tracks_saved": len(found),
        "total_in_catalogue": collection.count(),
        "errors": errors,
    }

def top_artist_names():
    names = set()
    for rng in ("medium_term", "short_term"):
        r = spotify_get("/me/top/artists", {"time_range": rng, "limit": 50})
        if r.status_code == 200:
            names.update(a["name"] for a in r.json()["items"])
    return names


@app.get("/suggest")
def suggest(text: str, play: bool = False, device_id: str = None):
    profile = parse_mood(text)
    if "error" in profile:
        return profile

    query = (
        "Mood: " + ", ".join(profile.get("moods", []))
        + ". Energy: " + str(profile.get("energy", ""))
        + ". Genres: " + ", ".join(profile.get("genres", []))
        + ". " + str(profile.get("search_text", ""))
    )
    energy = str(profile.get("energy", "")).lower()
    where = {"energy": energy} if energy in ("low", "medium", "high") else None
    res = collection.query(query_embeddings=embed([query]), n_results=30, where=where)
    favourites = top_artist_names()

    ranked = []
    for id_, meta, dist in zip(
        res["ids"][0], res["metadatas"][0], res["distances"][0]
    ):
        liked = any(a in favourites for a in meta["artist"].split(", "))
        score = dist - (0.15 if liked else 0)
        ranked.append((score, id_, meta, liked))
    ranked.sort(key=lambda x: x[0])
    picks = ranked[:10]

    result = {
        "mood_profile": profile,
        "songs": [
            {
                "track": m["track"],
                "artist": m["artist"],
                "id": i,
                "from_your_favourite_artists": liked,
            }
            for _, i, m, liked in picks
        ],
    }

    if play:
        params = {"device_id": device_id} if device_id else None
        r = requests.put(
            "https://api.spotify.com/v1/me/player/play",
            headers={"Authorization": "Bearer " + get_token()},
            params=params,
            json={"uris": ["spotify:track:" + i for _, i, _, _ in picks]},
        )
        result["playback"] = (
            "started" if r.status_code in (200, 202, 204)
            else {"error": r.status_code, "detail": r.text}
        )
    return result

@app.get("/playlist")
def playlist(text: str):
    result = suggest(text)
    if "songs" not in result:
        return result
    uris = ["spotify:track:" + s["id"] for s in result["songs"]]
    headers = {"Authorization": "Bearer " + get_token()}
    name = "Moodify: " + text[:40]
    r = requests.post(
        "https://api.spotify.com/v1/me/playlists",
        headers=headers,
        json={"name": name, "public": False, "description": "Made by Moodify"},
    )
    if r.status_code not in (200, 201):
        return {"error": r.status_code, "detail": r.text}
    pl = r.json()
    added = None
    for path in ("items", "tracks"):
        added = requests.post(
            "https://api.spotify.com/v1/playlists/" + pl["id"] + "/" + path,
            headers=headers,
            json={"uris": uris},
        )
        if added.status_code in (200, 201):
            return {
                "playlist": name,
                "songs": len(uris),
                "url": pl["external_urls"]["spotify"],
            }
    return {"error": added.status_code, "detail": added.text}


def label_tracks(tracks):
    lines = "\n".join(
        str(i + 1) + ". " + t["track"] + " by " + t["artist"]
        for i, t in enumerate(tracks)
    )
    prompt = (
        "For each song below, give its likely mood and energy, judged from "
        "the title and artist. Reply with ONLY a JSON object like "
        '{"labels": [{"moods": ["word", "word", "word"], "energy": "low"}]} '
        'where energy is "low", "medium" or "high", with exactly one entry '
        "per song, in the same order.\n" + lines
    )
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": "Bearer " + os.getenv("GROQ_API_KEY", "")},
        json={
            "model": "openai/gpt-oss-20b",
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        },
    )
    if r.status_code != 200:
        return None
    try:
        labels = json.loads(r.json()["choices"][0]["message"]["content"])["labels"]
    except Exception:
        return None
    if len(labels) != len(tracks):
        return None
    return labels


@app.get("/label-test")
def label_test():
    sample = [
        {"track": "Sleepless", "artist": "AP Dhillon"},
        {"track": "After Dark", "artist": "Drake, Static Major, Ty Dolla $ign"},
        {"track": "Peaceful Piano", "artist": "Relaxing Piano"},
        {"track": "Badfella", "artist": "Sidhu Moose Wala"},
    ]
    return label_tracks(sample) or {"error": "labelling failed"}


@app.get("/relabel-catalogue")
def relabel_catalogue():
    data = collection.get(include=["metadatas"])
    ids = data["ids"]
    metas = data["metadatas"]
    new_ids, new_docs, new_metas = [], [], []
    failed = 0
    for start in range(0, len(ids), 8):
        batch_ids = ids[start:start + 8]
        batch_metas = metas[start:start + 8]
        labels = label_tracks(
            [{"track": m["track"], "artist": m["artist"]} for m in batch_metas]
        )
        if labels is None:
            failed += len(batch_ids)
            continue
        for id_, m, lab in zip(batch_ids, batch_metas, labels):
            moods = ", ".join(lab.get("moods", []))
            energy = str(lab.get("energy", "medium")).lower()
            if energy not in ("low", "medium", "high"):
                energy = "medium"
            tags = moods + ", " + energy + " energy"
            new_ids.append(id_)
            new_docs.append("Mood: " + tags + ". Music that feels " + tags + ".")
            new_metas.append(
                {"track": m["track"], "artist": m["artist"], "tags": tags, "energy": energy}
            )
        time.sleep(2)
    if new_ids:
        collection.upsert(
            ids=new_ids,
            documents=new_docs,
            metadatas=new_metas,
            embeddings=embed(new_docs),
        )
    return {"relabelled": len(new_ids), "failed": failed, "total": collection.count()}

LABEL_LAST_ERROR = ""


def label_tracks(tracks):
    global LABEL_LAST_ERROR
    lines = "\n".join(
        str(i + 1) + ". " + t["track"] + " by " + t["artist"]
        for i, t in enumerate(tracks)
    )
    prompt = (
        "For each song below, give its likely mood and energy, judged from "
        "the title and artist. Reply with ONLY a JSON object like "
        '{"labels": [{"moods": ["word", "word", "word"], "energy": "low"}]} '
        'where energy is "low", "medium" or "high", with exactly one entry '
        "per song, in the same order.\n" + lines
    )
    for attempt in range(3):
        try:
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": "Bearer " + os.getenv("GROQ_API_KEY", "")},
                json={
                    "model": "openai/gpt-oss-20b",
                    "messages": [{"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.2,
                },
                timeout=60,
            )
        except Exception as e:
            LABEL_LAST_ERROR = "request failed: " + str(e)
            time.sleep(3)
            continue
        if r.status_code == 429:
            LABEL_LAST_ERROR = "rate limited: " + r.text[:300]
            time.sleep(10)
            continue
        if r.status_code != 200:
            LABEL_LAST_ERROR = str(r.status_code) + " " + r.text[:300]
            time.sleep(3)
            continue
        try:
            content = r.json()["choices"][0]["message"]["content"]
            labels = json.loads(content)["labels"]
        except Exception as e:
            LABEL_LAST_ERROR = "bad json: " + str(e) + " | " + r.text[:300]
            continue
        if len(labels) != len(tracks):
            LABEL_LAST_ERROR = (
                "wrong count: got " + str(len(labels)) + ", expected " + str(len(tracks))
            )
            continue
        return labels
    return None


@app.get("/label-error")
def label_error():
    return {"last_error": LABEL_LAST_ERROR}

@app.get("/relabel-some")
def relabel_some(limit: int = 40):
    data = collection.get(include=["metadatas"])
    todo = [
        (i, m) for i, m in zip(data["ids"], data["metadatas"])
        if not m.get("labelled")
    ]
    before = len(todo)
    todo = todo[:limit]
    new_ids, new_docs, new_metas = [], [], []
    for start in range(0, len(todo), 8):
        batch = todo[start:start + 8]
        labels = label_tracks(
            [{"track": m["track"], "artist": m["artist"]} for _, m in batch]
        )
        if labels is None:
            continue
        for (id_, m), lab in zip(batch, labels):
            moods = ", ".join(lab.get("moods", []))
            energy = str(lab.get("energy", "medium")).lower()
            if energy not in ("low", "medium", "high"):
                energy = "medium"
            tags = moods + ", " + energy + " energy"
            new_ids.append(id_)
            new_docs.append("Mood: " + tags + ". Music that feels " + tags + ".")
            new_metas.append(
                {
                    "track": m["track"],
                    "artist": m["artist"],
                    "tags": tags,
                    "energy": energy,
                    "labelled": True,
                }
            )
        time.sleep(1)
    if new_ids:
        collection.upsert(
            ids=new_ids,
            documents=new_docs,
            metadatas=new_metas,
            embeddings=embed(new_docs),
        )
    return {
        "relabelled_now": len(new_ids),
        "still_to_do": before - len(new_ids),
        "total": collection.count(),
    }
#!/usr/bin/env python3
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import closing
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DB_PATH = os.path.join(BASE_DIR, "bookshelf.db")
API_URL = "https://www.googleapis.com/books/v1/volumes"
CACHE_SECONDS = 600
SESSION_SECONDS = 7 * 24 * 60 * 60
SYNC_SECONDS = 5
SYNC_OVERLAP = 5
MAX_BODY = 10000
MAX_SYNC_BODY = 20 * 1024 * 1024
HOSTNAME = socket.gethostname()
USERNAME_RULE = re.compile(r"^[A-Za-z0-9_]{3,30}$")
BOOK_FIELDS = ("id", "title", "authors", "year", "category",
               "rating", "pages", "cover", "link")
SYNC_TABLES = {
    "users": ("id", "username", "salt", "password_hash",
              "created", "updated"),
    "library": ("user_id", "book_id", "book", "deleted", "updated"),
}
cache = {}


def load_setting(name):
    """Read a setting from the environment, then from the .env file."""
    value = os.environ.get(name, "")
    env_path = os.path.join(BASE_DIR, ".env")
    if not value and os.path.exists(env_path):
        with open(env_path) as env_file:
            for line in env_file:
                if line.startswith(name + "="):
                    value = line.split("=", 1)[1].strip()
    return value


API_KEY = load_setting("GOOGLE_BOOKS_API_KEY")
SECRET_KEY = load_setting("SECRET_KEY")
SYNC_KEY = load_setting("SYNC_KEY")
SYNC_PEER = load_setting("SYNC_PEER").rstrip("/")


# database

def connect():
    """Open a connection that commits each statement on its own."""
    conn = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def query(sql, params=()):
    with closing(connect()) as conn:
        return conn.execute(sql, params).fetchall()


def transaction(work):
    """Run work(conn) so that all its changes are saved, or none are."""
    with closing(connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            result = work(conn)
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
        return result


def stable_id(username):
    """Give an old user the same id on both servers."""
    text = "bookshelf-user:" + username
    return hashlib.sha256(text.encode()).hexdigest()[:32]


def table_names(conn):
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return [row["name"] for row in rows]


def column_names(conn, table):
    rows = conn.execute("PRAGMA table_info({})".format(table))
    return [row["name"] for row in rows]


def create_tables(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS users ("
                 "id TEXT PRIMARY KEY, "
                 "username TEXT UNIQUE NOT NULL, "
                 "salt TEXT NOT NULL, "
                 "password_hash TEXT NOT NULL, "
                 "created REAL NOT NULL, "
                 "updated REAL NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS library ("
                 "user_id TEXT NOT NULL, "
                 "book_id TEXT NOT NULL, "
                 "book TEXT NOT NULL, "
                 "deleted INTEGER NOT NULL DEFAULT 0, "
                 "updated REAL NOT NULL, "
                 "PRIMARY KEY (user_id, book_id))")
    conn.execute("CREATE TABLE IF NOT EXISTS sync_state ("
                 "name TEXT PRIMARY KEY, "
                 "value REAL NOT NULL)")
    conn.execute("CREATE INDEX IF NOT EXISTS users_updated "
                 "ON users (updated)")
    conn.execute("CREATE INDEX IF NOT EXISTS library_updated "
                 "ON library (updated)")


def migrate_old_tables(conn):
    """Copy data from the old integer-id tables into the new ones."""
    now = time.time()
    new_ids = {}
    for row in conn.execute("SELECT * FROM old_users").fetchall():
        new_ids[row["id"]] = stable_id(row["username"])
        conn.execute("INSERT OR IGNORE INTO users (id, username, salt, "
                     "password_hash, created, updated) "
                     "VALUES (?, ?, ?, ?, 0, ?)",
                     (new_ids[row["id"]], row["username"], row["salt"],
                      row["password_hash"], now))
    if "old_library" in table_names(conn):
        for row in conn.execute("SELECT * FROM old_library").fetchall():
            if row["user_id"] in new_ids:
                conn.execute("INSERT OR IGNORE INTO library (user_id, "
                             "book_id, book, deleted, updated) "
                             "VALUES (?, ?, ?, 0, ?)",
                             (new_ids[row["user_id"]], row["book_id"],
                              row["book"], now))
        conn.execute("DROP TABLE old_library")
    conn.execute("DROP TABLE old_users")


def init_db():
    """Create the tables, upgrading an old database if there is one."""
    def work(conn):
        names = table_names(conn)
        old = "users" in names and "updated" not in column_names(conn,
                                                                 "users")
        if old:
            conn.execute("ALTER TABLE users RENAME TO old_users")
            if "library" in names:
                conn.execute("ALTER TABLE library RENAME TO old_library")
        conn.execute("DROP TABLE IF EXISTS sessions")
        create_tables(conn)
        if old:
            migrate_old_tables(conn)
    transaction(work)


# accounts and sessions

def hash_password(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(),
                               bytes.fromhex(salt), 200000).hex()


def create_user(username, password):
    if not USERNAME_RULE.match(username):
        return 400, {"error": "username must be 3-30 letters, numbers "
                     "or underscores"}
    if len(password) < 8:
        return 400, {"error": "password must be at least 8 characters."}
    salt = secrets.token_hex(16)
    now = time.time()
    try:
        query("INSERT INTO users (id, username, salt, password_hash, "
              "created, updated) VALUES (?, ?, ?, ?, ?, ?)",
              (secrets.token_hex(16), username, salt,
               hash_password(password, salt), now, now))
    except sqlite3.IntegrityError:
        return 409, {"error": "that username is already taken."}
    return 201, {"username": username}


def check_login(username, password):
    """Return the user if the username and password match."""
    rows = query("SELECT * FROM users WHERE username = ?", (username,))
    if not rows:
        return None
    user = rows[0]
    attempt = hash_password(password, user["salt"])
    if hmac.compare_digest(attempt, user["password_hash"]):
        return user
    return None


def sign(text):
    return hmac.new(SECRET_KEY.encode(), text.encode(),
                    hashlib.sha256).hexdigest()


def new_session(user):
    """Make a signed token that both servers can check without the db."""
    expires = int(time.time()) + SESSION_SECONDS
    text = "{}.{}.{}".format(user["id"], user["username"], expires)
    return text + "." + sign(text)


def user_from_token(token):
    parts = token.split(".")
    if len(parts) != 4:
        return None
    user_id, username, expires, signature = parts
    if not hmac.compare_digest(sign(".".join(parts[:3])).encode(),
                               signature.encode()):
        return None
    if not expires.isdigit() or int(expires) < time.time():
        return None
    return {"id": user_id, "username": username}


# two-way sync

def get_state(name):
    rows = query("SELECT value FROM sync_state WHERE name = ?", (name,))
    return rows[0]["value"] if rows else 0.0


def set_state(name, value):
    query("INSERT OR REPLACE INTO sync_state (name, value) VALUES (?, ?)",
          (name, value))


def changes_since(since):
    """Collect every row changed on this server since a point in time."""
    result = {}
    for table, cols in SYNC_TABLES.items():
        rows = query("SELECT {} FROM {} WHERE updated >= ?".format(
            ", ".join(cols), table), (since,))
        result[table] = [dict(row) for row in rows]
    return result


def clean_rows(rows, table):
    if not isinstance(rows, list):
        return []
    cols = SYNC_TABLES[table]
    return [row for row in rows
            if isinstance(row, dict) and all(col in row for col in cols)]


def merge_user(conn, row):
    local = conn.execute("SELECT updated FROM users WHERE id = ?",
                         (row["id"],)).fetchone()
    if local is not None:
        if float(row["updated"]) > local["updated"]:
            conn.execute("UPDATE users SET salt = ?, password_hash = ?, "
                         "updated = ? WHERE id = ?",
                         (row["salt"], row["password_hash"],
                          float(row["updated"]), row["id"]))
        return
    rival = conn.execute("SELECT id, created FROM users WHERE username = ?",
                         (row["username"],)).fetchone()
    if rival is not None:
        # same username made on both servers: the oldest account wins
        if (float(row["created"]), row["id"]) > (rival["created"],
                                                 rival["id"]):
            return
        conn.execute("DELETE FROM users WHERE id = ?", (rival["id"],))
    conn.execute("INSERT INTO users (id, username, salt, password_hash, "
                 "created, updated) VALUES (?, ?, ?, ?, ?, ?)",
                 (row["id"], row["username"], row["salt"],
                  row["password_hash"], float(row["created"]),
                  float(row["updated"])))


def merge_book(conn, row):
    local = conn.execute("SELECT updated FROM library "
                         "WHERE user_id = ? AND book_id = ?",
                         (row["user_id"], row["book_id"])).fetchone()
    # the newest change wins
    if local is None or float(row["updated"]) > local["updated"]:
        conn.execute("INSERT OR REPLACE INTO library (user_id, book_id, "
                     "book, deleted, updated) VALUES (?, ?, ?, ?, ?)",
                     (row["user_id"], row["book_id"], row["book"],
                      1 if row["deleted"] else 0, float(row["updated"])))


def merge_changes(changes):
    """Apply the other server's changes in one transaction."""
    if not isinstance(changes, dict):
        raise ValueError("changes must be an object")

    def work(conn):
        for row in clean_rows(changes.get("users"), "users"):
            merge_user(conn, row)
        for row in clean_rows(changes.get("library"), "library"):
            merge_book(conn, row)
    transaction(work)


def sync_once():
    """Send our changes to the peer and save the changes it sends back."""
    sent = get_state("sent")
    received = get_state("received")
    now = time.time() - SYNC_OVERLAP
    body = json.dumps({"since": received,
                       "changes": changes_since(sent)}).encode()
    request = urllib.request.Request(
        SYNC_PEER + "/api/sync", data=body,
        headers={"Content-Type": "application/json",
                 "X-Sync-Key": SYNC_KEY})
    with urllib.request.urlopen(request, timeout=30) as response:
        answer = json.load(response)
    merge_changes(answer.get("changes") or {})
    set_state("sent", now)
    set_state("received", float(answer["now"]))


def sync_forever():
    while True:
        try:
            sync_once()
        except (urllib.error.URLError, OSError, ValueError, KeyError,
                TypeError, sqlite3.Error) as err:
            print("sync with {} failed: {}".format(SYNC_PEER, err),
                  file=sys.stderr)
        time.sleep(SYNC_SECONDS)


# books from google

def simplify(item):
    info = item.get("volumeInfo", {})
    cover = info.get("imageLinks", {}).get("thumbnail", "")
    return {
        "id": item.get("id", ""),
        "title": info.get("title", "untitled"),
        "authors": ", ".join(info.get("authors", ["unknown author"])),
        "year": info.get("publishedDate", "")[:4],
        "category": (info.get("categories") or ["uncategorised"])[0],
        "rating": info.get("averageRating", 0),
        "pages": info.get("pageCount", 0),
        "cover": cover.replace("http://", "https://"),
        "link": info.get("infoLink", ""),
    }


def search_books(text):
    if not API_KEY:
        return 500, {"error": "The server is missing its API key."}

    saved = cache.get(text)
    if saved and time.time() - saved[0] < CACHE_SECONDS:
        return 200, saved[1]

    params = urllib.parse.urlencode(
        {"q": text, "maxResults": 40, "key": API_KEY})
    try:
        with urllib.request.urlopen(API_URL + "?" + params,
                                    timeout=10) as response:
            data = json.load(response)
    except urllib.error.HTTPError as err:
        if err.code in (400, 403):
            return 502, {"error": "The book service rejected the request. "
                                  "The API key may be invalid"}
        if err.code == 429:
            return 503, {"error": "Too many searches right now. "
                                  "Please wait a minute."}
        return 502, {"error": "The book service failed. Try again later."}
    except (urllib.error.URLError, TimeoutError, ValueError):
        return 503, {"error": "The book service is unreachable. "
                              "Please try again later."}

    result = {"books": [simplify(item) for item in data.get("items", [])]}
    cache[text] = (time.time(), result)
    return 200, result


# web server

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def end_headers(self):
        self.send_header("X-Served-By", HOSTNAME)
        super().end_headers()

    def send_json(self, status, data, cookie=None):
        body = json.dumps(dict(data, server=HOSTNAME)).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def read_json(self, limit):
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > limit:
                return None
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def session_token(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        return cookie["session"].value if "session" in cookie else ""

    def current_user(self):
        token = self.session_token()
        return user_from_token(token) if token else None

    def login_cookie(self, token, max_age):
        return ("session={}; HttpOnly; SameSite=Strict; Path=/; "
                "Max-Age={}".format(token, max_age))

    def split_path(self):
        path, _, text = self.path.partition("?")
        return path, urllib.parse.parse_qs(text)

    def do_GET(self):
        path, params = self.split_path()
        if not path.startswith("/api/"):
            super().do_GET()
            return
        user = self.current_user()
        if user is None:
            self.send_json(401, {"error": "Please login first."})
        elif path == "/api/me":
            self.send_json(200, {"username": user["username"]})
        elif path == "/api/library":
            rows = query("SELECT book FROM library "
                         "WHERE user_id = ? AND deleted = 0",
                         (user["id"],))
            self.send_json(200, {"books": [json.loads(r["book"])
                                           for r in rows]})
        elif path == "/api/books":
            text = params.get("q", [""])[0].strip()
            if not text or len(text) > 100:
                self.send_json(400, {"error": "Type 1 to 100 characters "
                                              "to search."})
            else:
                self.send_json(*search_books(text.lower()))
        else:
            self.send_json(404, {"error": "Not found"})

    def do_POST(self):
        path, _ = self.split_path()
        limit = MAX_SYNC_BODY if path == "/api/sync" else MAX_BODY
        data = self.read_json(limit)
        if data is None:
            self.send_json(400, {"error": "Invalid request."})
        elif path == "/api/sync":
            self.handle_sync(data)
        elif path in ("/api/register", "/api/login"):
            self.handle_auth(path, data)
        elif path == "/api/logout":
            self.send_json(200, {"message": "Logged out."},
                           self.login_cookie("", 0))
        elif path == "/api/library":
            self.handle_save(data)
        else:
            self.send_json(404, {"error": "Not found."})

    def do_DELETE(self):
        path, params = self.split_path()
        user = self.current_user()
        if path != "/api/library":
            self.send_json(404, {"error": "Not found"})
        elif user is None:
            self.send_json(401, {"error": "please log in first"})
        else:
            book_id = params.get("id", [""])[0]
            if not 0 < len(book_id) < 100:
                self.send_json(400, {"error": "Invalid book."})
                return
            # keep a "deleted" marker so the delete reaches the other server
            query("INSERT INTO library (user_id, book_id, book, deleted, "
                  "updated) VALUES (?, ?, '{}', 1, ?) "
                  "ON CONFLICT (user_id, book_id) DO UPDATE SET "
                  "deleted = 1, updated = excluded.updated",
                  (user["id"], book_id, time.time()))
            self.send_json(200, {"message": "Removed."})

    def handle_auth(self, path, data):
        username = str(data.get("username", "")).strip()
        password = str(data.get("password", ""))
        if path == "/api/register":
            status, result = create_user(username, password)
            if status != 201:
                self.send_json(status, result)
                return
        user = check_login(username, password)
        if user is None:
            self.send_json(401, {"error": "wrong username or password."})
            return
        token = new_session(user)
        self.send_json(200, {"username": user["username"]},
                       self.login_cookie(token, SESSION_SECONDS))

    def handle_save(self, data):
        user = self.current_user()
        if user is None:
            self.send_json(401, {"error": "Please log in first."})
            return
        book = {field: data.get(field) for field in BOOK_FIELDS}
        if not isinstance(book["id"], str) or not 0 < len(book["id"]) < 100:
            self.send_json(400, {"error": "Invalid book."})
            return
        query("INSERT INTO library (user_id, book_id, book, deleted, "
              "updated) VALUES (?, ?, ?, 0, ?) "
              "ON CONFLICT (user_id, book_id) DO UPDATE SET "
              "book = excluded.book, deleted = 0, "
              "updated = excluded.updated",
              (user["id"], book["id"], json.dumps(book), time.time()))
        self.send_json(201, {"message": "Saved."})

    def handle_sync(self, data):
        key = self.headers.get("X-Sync-Key", "")
        if not SYNC_KEY or not hmac.compare_digest(key.encode(),
                                                   SYNC_KEY.encode()):
            self.send_json(403, {"error": "Forbidden."})
            return
        try:
            since = float(data.get("since", 0))
            now = time.time() - SYNC_OVERLAP
            outgoing = changes_since(since)
            merge_changes(data.get("changes") or {})
        except (TypeError, ValueError, KeyError, sqlite3.Error):
            self.send_json(400, {"error": "Invalid sync data."})
            return
        self.send_json(200, {"now": now, "changes": outgoing})


if __name__ == "__main__":
    init_db()
    if len(sys.argv) == 4 and sys.argv[1] == "adduser":
        code, answer = create_user(sys.argv[2], sys.argv[3])
        print(answer.get("error") or "User created: " + sys.argv[2])
        sys.exit(0 if code == 201 else 1)
    if not SECRET_KEY:
        print("SECRET_KEY is missing. Put the same SECRET_KEY in .env "
              "on both servers.", file=sys.stderr)
        sys.exit(1)
    if SYNC_PEER and SYNC_KEY:
        threading.Thread(target=sync_forever, daemon=True).start()
        print("Syncing with {} every {}s".format(SYNC_PEER, SYNC_SECONDS))
    else:
        print("Sync is off: set SYNC_PEER and SYNC_KEY in .env")
    port = int(os.environ.get("PORT", "5000"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("Bookshelf running on http://127.0.0.1:{}".format(port))
    server.serve_forever()

import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.request
from contextlib import closing
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DB_PATH = os.path.join(BASE_DIR, "bookshelf.db")
API_URL = "https://www.googleapis.com/books/v1/volumes"
CACHE_SECONDS = 600
SESSION_SECONDS = 7 * 24 * 60 * 60
HOSTNAME = socket.gethostname()
USERNAME_RULE = re.compile(r"^[A-Za-z0-9]{3,30}$")
BOOK_FIELDS = ("id", "title", "authors", "year", "category",
               "rating","pages","cover","link")
cache = {}

def load_key():
    "getting google book api key from environment"
    key = os.environ.get("GOOGLE_BOOKS_API_KEY", "")
    env_path = os.path.join(BASE_DIR, ".env")
    if not key and os.path.exists(env_path):
        with open(env_path) as env_file:
            for line in env_file:
                if line.startswith("GOOGLE_BOOKS_API_KEY="):
                    key = line.split("=",1)[1].strip()
    return key

API_KEY = load_key()

#database for authontication and creating users

def query(sql, params=()):
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(sql, params).fetchall()

def init_db():
    """creating tables for first time"""
    query("CREATE TABLE IF NOT EXISTS users("
          "id INTEGER PRIMARY KEY AUTOINCREMENT, "
          "username TEXT UNIQUE NOT NULL, "
          "salt TEXT NOT NULL, "
          "password_hash TEXT NOT NULL)")
    query("CREATE TABLE IF NOT EXISTS sessions ("
          "token TEXT PRIMARY KEY, "
          "user_id INTEGER NOT NULL, "
          "created REAL NOT NULL)")
    query("CREATE TABLE IF NOT EXISTS library ("
          "user_id INTEGER NOT NULL, "
          "book_id TEXT NOT NULL, "
          "book TEXT NOT NULL, "
          "PRIMARY KEY (user_id, book_id))")

# accounts and sessions

def hash_password(password, salt):
    return hashlib.pbkdf2_hmac("sha256",password.encode(),
                               bytes.fromhex(salt), 200000).hex()

def create_user(username, password):
    if not USERNAME_RULE.match(username):
        return 400, {"error": "username must be 3-30 letters, numbers "
                     "or underscores"}
    if len(password) < 8:
        return 400, {"error": "password must be at least 8 characters."}
    salt = secrets.token_hex(16)
    try:
        query("INSERT INTO users (username, salt, password_hash) "
              "VALUES (?, ?, ?)",
              (username, salt, hash_password(password,salt))) 
    except sqlite3.IntegrityError:
        return 409, {"error": "that username is already taken."}
    return 201, {"username": username}

"""Checking login info"""
def check_login(username, password):
    rows = query("SELECT * FROM users WHERE username = ?", (username,))
    if not rows:
        return None
    user = rows[0]
    attempt = hash_password(password, user["salt"])
    if hmac.compare_digest(attempt, user["password_hash"]):
        return user
    return None

def new_session(user_id):
    now =time.time()
    query("DELETE FROM sessions WHERE created < ?", (now - SESSION_SECONDS,))
    token = secrets.token_urlsafe(32)
    query("INSERT INTO sessions (token, user_id, created) VALUES (?, ?, ?)",
          (token, user_id, now))
    return token

def user_from_token(token):
    rows = query("SELECT users.id. users.username FROM sessions "
                 "JOIN users ON users.id = sessions.user_id "
                 "WHERE sessions.token = ? AND sessions.created > ?",
                 (token, time.time() - SESSION_SECONDS))
    return rows[0] if rows else None

# books from google
def simplify(item):
    info = item.get("volumeInfo", {})
    cover = info.get("imageLinks", {}).get("thumbnail", "")
    return {
        "id": item.get("id", ""),
        "title": info.get("title", "untitled"),
        "authors": ", ".join(info.get("authors",["unknown author"])),
        "year": info.get("publishedDate", "")[:4],
        "category": (info.get("categories") or ["uncategorised"])[0],
        "rating": info.get("averageRating", 0),
        "pages": info.get("pageCount", 0),
        "cover": cover.replace("http://", "https://"),
        "link": info.get("infoLink", ""),
    }

#searching books\
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
        return 502, {"The boo4k service failed. Try again later."}
    except (urllib.error.URLError, TimeoutError, ValueError):
        return 503, {"error": "The book service is unreachable. "
                               "Please try again later."}

    result = {"books": [simplify(item) for item in data.get("items", [])]}
    cache[text] = (time.time(), result)
    return 200, result

# managing web server
class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def end_headers(self):
        self.send_header("X-served-By", HOSTNAME)
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

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length",0))
            if length > 10000:
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
            self.send_json(401,{"error": "Please login first."})
        elif path == "api/me":
            self.send_json(200, {"username": user["username"]})
        elif path == "/api/library":
            rows = query("SELECT book FROM library WHERE user_id = ?",
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
        data = self.read_json()
        if data is None:
            self.send_json(400, {"error": "Invalid request."})
        elif path in ("/api/register", "/api/login"):
            self.handle_auth(path, data)
        elif path == "/api/logout":
            query("DELETE FROM sessions WHERE token = ?",
                  (self.session_token(),))
            self.send_json(200, {"message": "Logged out. "},
                           self.login_cookie("", 0))
        elif path == "api/library":
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
            query("DELETE FROM library WHERE user_id = ? AND book_id = ?",
                  (user["id"], book_id))
            self.send_json(200, {"message": "Removed."})

    def handle_auth(self, path, data):
        username = str(data.get("username", "")).strip()
        password = str(data.get("password", ""))
        if path == "/api/register":
            status, result = create_user(username, password)
            if status !=201:
                self.send_json(status, result)
                return
        user = check_login(username, password)
        if user is None:
            self.send_json(401, {"error": "wrong username or password."})
            return
        token = new_session(user["id"])
        self.send_json(200, {"username": user["username"]},
                       self.login_cookie(token, SESSION_SECONDS))

    def handle_save(self, data):
        user = self.current_user()
        if user is None:
            self.send_json(401, {"error": "Please log in first."})
            return
        book = {field: data.get(field) for field in BOOK_FIELDS}
        if not isinstance(book["id"], str) or not 0< len(book["id"]) < 100:
            self.send_json(400, {"error": "Invalid book."})
            return
        query("INSERT OR REPLACE INTO library (user_id, book_id, book) "
              "VALUES (?, ?, ?)", (user["id"], book["id"], json.dumps(book)))
        self.send_json(201, {"message": "Saved."})

if __name__ == "__main__":
    init_db()
    if len(sys.argv) == 4 and sys.argv[1] == "adduser":
        code, answer = create_user(sys.argv[2], sys.argv[3])
        print(answer.get("error") or "User created: " + sys.argv[2])
        sys.exit(0 if code == 201 else 1)
    port = int(os.environ.get("PORT", "5000"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("Bookshelf running on http://127.0.0.1:{}".format(port))
    server.serve_forever()
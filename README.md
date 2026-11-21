# Bookshelf - Find and Save Books

**Live site:** https://tharcisse-irasubiza.tech (load balancer IP: http://3.9.180.134)

| Server | Name | IP |
|---|---|---|
| Web server 1 | 7296-web-01 | 18.130.100.127 |
| Web server 2 | 7296-web-02 | 3.10.142.222 |
| Load balancer | 7296-lb-01 | 3.9.180.134 |

**Demo login:** username `demo_user`, password `Demo12345` (or create your own account).

Bookshelf is a web app that helps students and readers find books on any
title, author or topic, compare them by year, category, rating and length,
and keep a personal reading list. Instead of searching many websites, a
student can look up "data structures" or "personal finance", see the best
rated books first, and save the ones they want to read later.

## Features

- Three pages: **Log in**, **Create account** and a **Dashboard** after login
- Dashboard stats: books saved, pages to read, average rating, number of categories
- Bar chart of your saved books by category
- Search millions of books by title, author or topic (Google Books API)
- Filter results by category
- Sort by relevance, title, newest, oldest or highest rating
- User accounts: register, log in, log out (passwords hashed, stored in SQLite)
- Each user has a personal "My Library" saved in the database, available after logging in again
- Filter and sort your saved library too
- Clear error messages for empty searches, no results, API failure or no internet
- API key kept on the server only; the browser never sees it
- Search results cached for 10 minutes to load faster and save API requests
- Footer shows which server answered, to prove load balancing works

## Tech Stack

- Backend: Python 3 standard library only (no packages to install)
- Database: SQLite (built into Python)
- Frontend: HTML, CSS, JavaScript
- Servers: Nginx (reverse proxy) on Web01 and Web02, HAProxy on Lb01
- HTTPS: free Let's Encrypt certificate on Lb01

## How It Works

```
Browser -> Lb01 (HAProxy :443 HTTPS / :80) -> Web01 or Web02 (Nginx :80) -> app.py (:5000) -> Google Books API
```

The browser calls `/api/books?q=python` on our own server. `app.py` checks
that the user is logged in, adds the secret API key, calls the Google Books
API, and sends back only the book details the page needs.

### API routes

| Method | Route | What it does | Login needed |
|---|---|---|---|
| POST | `/api/register` | Create an account and log in | No |
| POST | `/api/login` | Log in | No |
| POST | `/api/logout` | Log out | No |
| GET | `/api/me` | Who is logged in | Yes |
| GET | `/api/books?q=...` | Search Google Books | Yes |
| GET | `/api/library` | List my saved books | Yes |
| POST | `/api/library` | Save a book | Yes |
| DELETE | `/api/library?id=...` | Remove a saved book | Yes |

### Database tables

- `users`: id, username, salt, password_hash
- `sessions`: token, user_id, created (logins last 7 days)
- `library`: user_id, book_id, book details

## Project Structure

```
bookshelf/
├── app.py            # Python server: pages, login, library and book search
├── bookshelf.db      # SQLite database (created on first run, not in Git)
├── static/
│   ├── index.html    # Log in page
│   ├── register.html # Create account page
│   ├── dashboard.html# Dashboard (stats, chart, search, library)
│   ├── auth.js       # Log in and create account logic
│   ├── dashboard.js  # Dashboard logic: stats, chart, search, filter, sort, save
│   └── style.css     # One shared stylesheet for all pages
├── .gitignore        # Keeps .env (API key) and bookshelf.db out of GitHub
└── README.md
```

## About the API (Google Books API)

I chose the Google Books API because it is an official API run by Google,
it is free, it covers millions of books, and it is served only over HTTPS.
Before building, I read the official documentation:
[Using the API](https://developers.google.com/books/docs/v1/using) and
[Volumes: list](https://developers.google.com/books/docs/v1/reference/volumes/list).

| Detail | What the documentation says | How Bookshelf uses it |
|---|---|---|
| Endpoint | `GET https://www.googleapis.com/books/v1/volumes?q={search terms}` | Called only by `app.py`, never by the browser |
| Required parameter | `q`: the search text | The user's search, limited to 100 characters |
| Results per request | `maxResults`: 0 to 40 | Asks for 40, the maximum |
| Pagination | `startIndex`: position of the first result (starts at 0) | Not used; the first 40 results are shown |
| Authentication | An API key for public data, or OAuth 2.0 for a user's own Google library | API key only, because book search is public data |
| Sending the key | The `key` query parameter | Added on the server from `.env`, so it never reaches the browser |
| Rate limit | Daily quota per project, shown in Google Cloud Console under the API's Quotas tab (the default is commonly 1,000 requests per day) | Results are cached for 10 minutes, so repeated searches do not use the quota |
| Quota exceeded | HTTP `429` | Shows "Too many searches right now. Please wait a minute." |
| Bad or missing key | HTTP `400` or `403` | Shows "The book service rejected the request." |
| Missing fields | Many books have no cover, rating, category or year | The server fills in safe defaults |

## Get a Google Books API Key

1. Go to https://console.cloud.google.com and sign in with a Google account.
2. Create a new project.
3. Open "APIs & Services" > "Library", search "Books API" and click Enable.
4. Open "APIs & Services" > "Credentials" > "Create credentials" > "API key".
5. Click the new key, choose "Restrict key", and under "API restrictions"
   select only "Books API". If the key ever leaks, it cannot be used for
   other Google services.
6. Copy the key into `.env` (see below). Check the daily limit under
   "APIs & Services" > "Books API" > "Quotas".

## Run Locally

1. Clone the repository:
   ```bash
   git clone https://github.com/Tharcisse-Irasubiza/BookShelf.git
   cd BookShelf
   ```
2. Create a file named `.env` in the project folder containing one line:
   `GOOGLE_BOOKS_API_KEY=` followed directly by your key.
3. Start the app:
   ```bash
   python3 app.py
   ```
4. Open http://127.0.0.1:5000, click **Create an account**, and you are taken
   to your dashboard.

To create an account from the terminal instead:
```bash
python3 app.py adduser USERNAME PASSWORD
```

## Deployment

### 1. Web01 and Web02 (repeat on both servers)

Copy the code and add the key:
```bash
cd ~
git clone https://github.com/Tharcisse-Irasubiza/BookShelf.git
cd BookShelf
vi .env    # add the GOOGLE_BOOKS_API_KEY line
```

Run the app as a service so it starts on boot and restarts if it crashes.
File `/etc/systemd/system/bookshelf.service`:
```ini
[Unit]
Description=Bookshelf web app
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/BookShelf
ExecStart=/usr/bin/python3 /home/ubuntu/BookShelf/app.py
Restart=always

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now bookshelf
curl -s http://127.0.0.1:5000/api/me
```
The last command should answer `Please login first.`, which shows the app
is running.

Create the same demo account on this server (the grader's login). Run it
without `sudo`, so the database file belongs to the `ubuntu` user:
```bash
cd ~/BookShelf
python3 app.py adduser demo_user Demo12345
```

Nginx forwards port 80 to the app. File `/etc/nginx/sites-available/bookshelf`:
```nginx
server {
    listen 80 default_server;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```
```bash
sudo rm -f /etc/nginx/sites-enabled/default
sudo ln -s /etc/nginx/sites-available/bookshelf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl restart nginx
```

### 2. Lb01 (HAProxy load balancer)

Added to `/etc/haproxy/haproxy.cfg`:
```
frontend bookshelf_front
    bind *:80
    default_backend bookshelf_back

backend bookshelf_back
    balance roundrobin
    cookie SERVERID insert indirect nocache
    server web01 18.130.100.127:80 check cookie web01
    server web02 3.10.142.222:80 check cookie web02
```
```bash
sudo haproxy -c -f /etc/haproxy/haproxy.cfg
sudo systemctl restart haproxy
```

- `roundrobin` sends each new visitor to the next server in turn.
- `check` makes HAProxy stop sending traffic to a server that goes down.
- `cookie SERVERID insert` gives each browser a cookie so it keeps going to
  the same server (sticky sessions). This is needed because each web server
  has its own SQLite file: a user who logged in on Web01 is only known to
  Web01.

### 3. Domain and HTTPS

The domain `tharcisse-irasubiza.tech` has two DNS A records (`@` and `www`)
pointing to Lb01 (`3.9.180.134`). Because the app has a login, I added HTTPS
so usernames and passwords are encrypted. On Lb01, a free Let's Encrypt
certificate was created with Certbot:
```bash
sudo apt install -y certbot
sudo systemctl stop haproxy
sudo certbot certonly --standalone -d tharcisse-irasubiza.tech -d www.tharcisse-irasubiza.tech
sudo mkdir -p /etc/haproxy/certs
sudo bash -c 'cat /etc/letsencrypt/live/tharcisse-irasubiza.tech/fullchain.pem /etc/letsencrypt/live/tharcisse-irasubiza.tech/privkey.pem > /etc/haproxy/certs/tharcisse-irasubiza.tech.pem'
sudo chmod 600 /etc/haproxy/certs/tharcisse-irasubiza.tech.pem
```
The frontend then listens on port 443 and sends domain visitors from HTTP to
HTTPS:
```
frontend bookshelf_front
    bind *:80
    bind *:443 ssl crt /etc/haproxy/certs/tharcisse-irasubiza.tech.pem
    http-request redirect scheme https code 301 if !{ ssl_fc } { hdr(host) -i tharcisse-irasubiza.tech www.tharcisse-irasubiza.tech }
    default_backend bookshelf_back
```
```bash
sudo haproxy -c -f /etc/haproxy/haproxy.cfg
sudo systemctl start haproxy
```
The redirect only applies to the domain, because the certificate is for the
domain and not for the IP address.

### 4. Testing the Load Balancer

```bash
for i in 1 2 3 4; do curl -sI http://3.9.180.134 | grep -i served; done
```
`curl` sends no cookie, so the `X-Served-By` header alternates between the
two web servers. In a browser, the footer ("Served by ...") stays on one
server because of the sticky cookie; opening the site in a private window
shows a new visitor being sent to the other server. When one web server is
stopped (`sudo systemctl stop bookshelf`), HAProxy sends everyone to the
other one.

## Error Handling

| Situation | What the user sees |
|---|---|
| Empty username or password | "Please enter a username and password." |
| Username or password breaks the rules | "username must be 3-30 letters, numbers or underscores" / "Password must be at least 8 characters." |
| Username already exists | "That username is already taken." |
| Wrong login | "Wrong username or password." |
| Passwords do not match (create account) | "Passwords do not match." |
| Session expired or not logged in | Sent back to the log in page |
| Empty search box | "Please type a title, author or topic." |
| Search longer than 100 characters | "Type 1 to 100 characters to search." |
| No books match | "No books found. Try another search." |
| Invalid or rejected API key | "The book service rejected the request." |
| Too many requests | "Too many searches right now. Please wait a minute." |
| Google Books down or slow | "The book service is unreachable." |
| Server missing its API key | "The server is missing its API key." |
| User's internet is off | "Cannot reach the server." |
| Books with no cover, author, year or rating | Shown with "Unknown author", "Year unknown", "No rating" |

## Security

- The site uses HTTPS (Let's Encrypt), so logins are encrypted between the
  browser and the load balancer.
- The API key is stored in `.env` on each server and ignored by Git.
- Only the `static/` folder is served, so `.env` and `app.py` cannot be
  downloaded from the browser.
- Passwords are hashed with PBKDF2-SHA256 (200,000 rounds) and a random salt;
  plain passwords are never stored.
- Login sessions use a random token in an `HttpOnly`, `SameSite=Strict`
  cookie, so JavaScript cannot read it and other sites cannot use it.
- All SQL uses `?` placeholders, so input cannot change the query
  (SQL injection). Usernames are also limited to letters, numbers and `_`.
- Every route except register and login requires a valid session, and users
  can only see and change their own library.
- Request bodies over 10 KB and invalid JSON are rejected.
- Search text is checked on the server (1 to 100 characters).
- Book details are added with `textContent`, so book data cannot inject
  HTML or scripts (XSS). Only `https://` links are shown.

## Challenges

- **Hiding the API key:** calling Google Books straight from JavaScript would
  show the key to anyone who opens the browser tools. I added a small Python
  server that calls the API and keeps the key on the server.
- **Missing data:** many books have no cover, rating, category or year. The
  server fills in safe defaults so the page never breaks.
- **Proving load balancing:** both servers look the same in the browser, so
  I added an `X-Served-By` header and a footer showing the server name.
- **Logins behind a load balancer:** each web server has its own SQLite
  database, so a user known on Web01 is unknown on Web02. I used HAProxy
  sticky sessions so each browser stays on one server, and created the demo
  account on both servers. A larger app would use one shared database
  server such as PostgreSQL.
- **Losing my SSH key:** my servers were set up with a key I had created in
  the intranet sandbox. When the sandbox was reset, the private key was gone
  and every server answered "Permission denied (publickey)". I created a new
  key on my computer, added it to my intranet profile, got new servers, and
  now keep a backup of my private key.
- **Finding small typos:** I typed the code myself, and small mistakes broke
  features: a full stop instead of a comma in an SQL query, a missing `_` in
  the username rule (which also stopped the demo account from being
  created), and `textcontent` instead of `textContent` (so error messages
  never appeared). I found them by reading the browser console and Network
  tab, the server terminal output and `journalctl` on the servers.
- **Git push rejected:** after editing a file on GitHub, my push was
  rejected. I used `git pull --rebase` to bring in the change, then pushed.
- **HTTPS:** the browser showed "Not secure" because the site used plain
  HTTP. I added a Let's Encrypt certificate on the load balancer.

## Bonus Tasks Completed

- User authentication with SQLite and a personal library per user
- Data visualization: dashboard stats and a category bar chart
- Caching of API responses (10 minutes)
- Security: HTTPS, password hashing, input validation, SQL injection and XSS protection

## Credits

- [Google Books API](https://developers.google.com/books) - book data
  ([documentation](https://developers.google.com/books/docs/v1/using))
- [MDN Web Docs](https://developer.mozilla.org/) - Fetch API and cookies
- [Python sqlite3](https://docs.python.org/3/library/sqlite3.html) and
  [hashlib](https://docs.python.org/3/library/hashlib.html) documentation
- [HAProxy](https://www.haproxy.org/) and [Nginx](https://nginx.org/) documentation
- [Let's Encrypt](https://letsencrypt.org/) and [Certbot](https://certbot.eff.org/) - free HTTPS certificate

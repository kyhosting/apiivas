# KY-SHIRO API — Multi-Account iVAS SMS
# Developer: Kiki Faizal

from flask import Flask, request, jsonify, Response
from datetime import datetime
import cloudscraper
from bs4 import BeautifulSoup
import logging
import os
import gzip
import re
import time
import random
from concurrent.futures import ThreadPoolExecutor, as_completed

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════
# MULTI-ACCOUNT CONFIG
# Tambah akun baru cukup tambah dict baru di list ini
# Atau set env var: IVAS_ACCOUNTS = "email1:pass1,email2:pass2"
# ════════════════════════════════════════════════════════

def load_accounts():
    """
    Load daftar akun dari environment variable atau default.
    Format env: IVAS_ACCOUNTS = "email1:pass1,email2:pass2,email3:pass3"
    """
    env = os.getenv("IVAS_ACCOUNTS", "")
    if env.strip():
        accounts = []
        for pair in env.split(","):
            pair = pair.strip()
            if ":" in pair:
                parts = pair.split(":", 1)
                accounts.append({"email": parts[0].strip(), "password": parts[1].strip()})
        if accounts:
            logger.info(f"[CONFIG] {len(accounts)} akun dari env IVAS_ACCOUNTS")
            return accounts

    # Default akun — edit langsung di sini kalau tidak pakai env
    return [
        {"email": os.getenv("IVASMS_USERNAME",  "ceptampan58@gmail.com"),
         "password": os.getenv("IVASMS_PASSWORD", "Encep12345")},
        # Tambah akun ke-2:
        # {"email": "akun2@gmail.com", "password": "password2"},
        # Tambah akun ke-3:
        # {"email": "akun3@gmail.com", "password": "password3"},
    ]

ACCOUNTS     = load_accounts()
BASE_URL     = "https://www.ivasms.com"
LOGIN_URL    = "https://www.ivasms.com/login"
LIVE_URL     = "https://www.ivasms.com/portal/live/my_sms"
RECV_URL     = "https://www.ivasms.com/portal/sms/received"


# ════════════════════════════════════════════════════════
# STEALTH — Random User-Agent & Headers
# ════════════════════════════════════════════════════════

_USER_AGENTS = [
    # Chrome Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    # Chrome Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    # Firefox
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.3; rv:123.0) Gecko/20100101 Firefox/123.0",
    # Edge
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
]

_ACCEPT_LANGUAGES = [
    "en-US,en;q=0.9",
    "en-GB,en;q=0.9",
    "en-US,en;q=0.8,id;q=0.6",
    "fr-FR,fr;q=0.9,en;q=0.8",
    "en-US,en;q=0.9,fr;q=0.7",
]

def _random_delay(min_sec=1.0, max_sec=3.5):
    """Delay acak biar pattern request tidak ketahuan."""
    time.sleep(random.uniform(min_sec, max_sec))

def build_scraper():
    """Buat scraper dengan UA acak dan headers realistis."""
    s  = cloudscraper.create_scraper()
    ua = random.choice(_USER_AGENTS)
    al = random.choice(_ACCEPT_LANGUAGES)

    s.headers.update({
        "User-Agent":                ua,
        "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language":           al,
        "Accept-Encoding":           "gzip, deflate, br",
        "Connection":                "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest":            "document",
        "Sec-Fetch-Mode":            "navigate",
        "Sec-Fetch-Site":            "none",
        "Sec-Fetch-User":            "?1",
        "Cache-Control":             "max-age=0",
    })
    return s


def decode_response(response):
    enc = response.headers.get("Content-Encoding", "").lower()
    try:
        if enc == "gzip":
            return gzip.decompress(response.content).decode("utf-8", errors="replace")
        if enc == "br":
            import brotli
            return brotli.decompress(response.content).decode("utf-8", errors="replace")
    except Exception:
        pass
    return response.text


def ajax_hdrs(referer=None):
    return {
        "Accept":           "text/html, */*; q=0.01",
        "Content-Type":     "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin":           BASE_URL,
        "Referer":          referer or RECV_URL,
    }


def to_ivas_date(date_str):
    """DD/MM/YYYY → M/D/YYYY"""
    try:
        d = datetime.strptime(date_str, "%d/%m/%Y")
        return f"{d.month}/{d.day}/{d.year}"
    except Exception:
        return date_str


# ════════════════════════════════════════════════════════
# LOGIN PER AKUN
# ════════════════════════════════════════════════════════

def login_account(account):
    """
    Login satu akun dengan delay acak supaya tidak keliatan bot.
    Return dict: {ok, scraper, csrf, live_html, email} atau {ok: False, error, email}
    """
    email    = account["email"]
    password = account["password"]
    scraper  = build_scraper()

    try:
        # Delay acak sebelum mulai — manusia tidak langsung klik login
        _random_delay(1.0, 3.0)

        # Ambil halaman login → dapat _token
        login_page = scraper.get(LOGIN_URL, timeout=20)
        soup       = BeautifulSoup(login_page.text, "html.parser")
        tok_el     = soup.find("input", {"name": "_token"})
        if not tok_el:
            return {"ok": False, "error": "_token tidak ditemukan", "email": email}
        tok = tok_el["value"]

        # Delay sebelum POST — manusia butuh waktu ketik password
        _random_delay(1.5, 4.0)

        # POST login
        resp = scraper.post(
            LOGIN_URL,
            data={"email": email, "password": password, "_token": tok},
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Referer": LOGIN_URL, "Origin": BASE_URL},
            timeout=20,
            allow_redirects=True,
        )

        if "/login" in resp.url:
            return {"ok": False, "error": "Email/password salah", "email": email}

        # Delay kecil sebelum akses portal — manusia tidak langsung browse
        _random_delay(0.8, 2.0)

        # Ambil halaman live → dapat csrf terbaru
        portal = scraper.get(LIVE_URL, timeout=20)
        html   = decode_response(portal)
        psoup  = BeautifulSoup(html, "html.parser")

        meta = psoup.find("meta", {"name": "csrf-token"})
        inp  = psoup.find("input", {"name": "_token"})
        csrf = (meta["content"] if meta else (inp["value"] if inp else tok))

        logger.info(f"[LOGIN] OK  {email}")
        return {"ok": True, "scraper": scraper, "csrf": csrf, "live_html": html, "email": email}

    except Exception as e:
        logger.error(f"[LOGIN] Error {email}: {e}")
        return {"ok": False, "error": str(e), "email": email}


def login_all_accounts():
    """Login semua akun secara paralel. Return list session yang berhasil."""
    sessions = []
    # Login semua akun — jeda acak antar akun supaya tidak kelihatan bot
    with ThreadPoolExecutor(max_workers=min(len(ACCOUNTS), 3)) as ex:
        futures = {ex.submit(login_account, acc): acc for acc in ACCOUNTS}
        for future in as_completed(futures):
            result = future.result()
            if result["ok"]:
                sessions.append(result)
            else:
                logger.warning(f"[LOGIN] Gagal: {result['email']} — {result.get('error','')}")
    logger.info(f"[LOGIN] {len(sessions)}/{len(ACCOUNTS)} akun berhasil")
    return sessions


# ════════════════════════════════════════════════════════
# LIVE SMS
# ════════════════════════════════════════════════════════

def parse_live_sms(html, account_email=""):
    soup    = BeautifulSoup(html, "html.parser")
    results = []

    for tbl in soup.find_all("table"):
        ths = [th.get_text(strip=True).lower() for th in tbl.find_all("th")]
        if not any("message" in h or "sid" in h or "live" in h for h in ths):
            continue

        for row in tbl.find_all("tr"):
            tds = row.find_all("td")
            if len(tds) < 3:
                continue

            raw0     = tds[0].get_text(separator="\n", strip=True)
            sid      = tds[1].get_text(strip=True) if len(tds) > 1 else ""
            msg_text = tds[-1].get_text(strip=True)

            if not raw0 or not msg_text or len(msg_text) < 4:
                continue
            if re.match(r"^(live sms|sid|range|sender|message|time)", raw0, re.I):
                continue

            lines  = [l.strip() for l in raw0.split("\n") if l.strip()]
            range_ = lines[0] if lines else ""
            number = ""
            for l in lines[1:]:
                d = re.sub(r"\D", "", l)
                if len(d) >= 8:
                    number = d
                    break
            if not number:
                m = re.search(r"(\d{8,15})", raw0)
                if m:
                    number = m.group(1)

            if not range_:
                continue

            results.append({
                "range":        range_,
                "phone_number": number,
                "otp_message":  msg_text,
                "sid":          sid,
                "source":       "live",
                "account":      account_email,
            })

    return results


# ════════════════════════════════════════════════════════
# RECEIVED SMS — 3 level AJAX
# ════════════════════════════════════════════════════════

def get_ranges(scraper, csrf, from_date, to_date):
    """Level 1: GET semua range yang punya SMS di tanggal tsb."""
    try:
        _random_delay(0.5, 1.5)  # delay sebelum request
        resp = scraper.post(
            f"{BASE_URL}/portal/sms/received/getsms",
            data={"from": to_ivas_date(from_date), "to": to_ivas_date(to_date), "_token": csrf},
            headers=ajax_hdrs(),
            timeout=15,
        )
        if resp.status_code != 200:
            return []

        html = decode_response(resp)
        soup = BeautifulSoup(html, "html.parser")
        result = []

        # Prioritas 1: div.rng → span.rname
        for div in soup.select("div.rng"):
            rname = div.select_one("span.rname")
            if not rname:
                continue
            name = rname.get_text(strip=True)
            if not name:
                continue

            # Cari range_id dari onclick atau div.sub
            rng_id = None
            oc = div.get("onclick", "")
            m  = re.search(r"toggleRange\s*\(\s*'[^']*'\s*,\s*'([^']+)'", oc)
            if m:
                rng_id = m.group(1)
            if not rng_id:
                sub = div.select_one("div[id^='sp_']")
                if sub:
                    rng_id = sub.get("id", "").replace("sp_", "")
            if not rng_id:
                rng_id = name.replace(" ", "_")

            if not any(r["name"] == name for r in result):
                result.append({"name": name, "id": rng_id})

        # Prioritas 2: onclick toggleRange(...)
        if not result:
            for el in soup.find_all(onclick=True):
                oc = el.get("onclick", "")
                m  = re.search(r"toggleRange\s*\(\s*'([^']+)'\s*,\s*'([^']+)'", oc)
                if m:
                    name, rng_id = m.group(1).strip(), m.group(2).strip()
                    if name and not any(r["name"] == name for r in result):
                        result.append({"name": name, "id": rng_id})

        logger.info(f"[RANGES] {len(result)} ranges: {[r['name'] for r in result]}")
        return result

    except Exception as e:
        logger.error(f"[RANGES] Error: {e}")
        return []


def get_numbers(scraper, csrf, range_name, from_date, to_date):
    """Level 2: GET nomor-nomor di range tertentu. range_name pakai SPASI."""
    try:
        _random_delay(0.8, 2.0)  # delay antar request
        resp = scraper.post(
            f"{BASE_URL}/portal/sms/received/getsms/number",
            data={
                "_token": csrf,
                "start":  to_ivas_date(from_date),
                "end":    to_ivas_date(to_date),
                "range":  range_name,   # ← SPASI, bukan underscore
            },
            headers=ajax_hdrs(),
            timeout=15,
        )
        if resp.status_code != 200:
            return []

        html = decode_response(resp)

        # Kalau dapat JS template → parameter salah
        if len(html) < 2000 and "function toggleNum" in html and "Number:id" in html.replace(" ", ""):
            logger.warning(f"[NUMBERS] JS template untuk {range_name} — parameter salah")
            return []

        numbers = []

        # Prioritas 1: onclick="toggleNumXXX('2250711220970', '...')"
        for m in re.finditer(r"toggleNum\w+\s*\(\s*'(\d{7,15})'", html):
            n = m.group(1)
            if n not in numbers:
                numbers.append(n)

        # Prioritas 2: semua angka 10-15 digit dalam single-quotes di HTML
        if not numbers:
            for n in re.findall(r"'(\d{10,15})'", html):
                if n not in numbers:
                    numbers.append(n)

        # Prioritas 3: BeautifulSoup span/div leaf element
        if not numbers:
            soup = BeautifulSoup(html, "html.parser")
            for el in soup.find_all(["span", "div", "td"]):
                if el.find_all(True):
                    continue
                digits = re.sub(r"\D", "", el.get_text(strip=True))
                if 10 <= len(digits) <= 15 and digits not in numbers:
                    numbers.append(digits)

        logger.info(f"[NUMBERS] {range_name} → {len(numbers)}: {numbers}")
        return numbers

    except Exception as e:
        logger.error(f"[NUMBERS] Error {range_name}: {e}")
        return []


def get_sms(scraper, csrf, phone_number, range_name, from_date, to_date):
    """Level 3: GET isi SMS untuk nomor tertentu. range_name pakai SPASI."""
    try:
        _random_delay(0.5, 1.8)  # delay antar nomor
        resp = scraper.post(
            f"{BASE_URL}/portal/sms/received/getsms/number/sms",
            data={
                "_token": csrf,
                "start":  to_ivas_date(from_date),
                "end":    to_ivas_date(to_date),
                "Number": phone_number,
                "Range":  range_name,   # ← SPASI, bukan underscore
            },
            headers=ajax_hdrs(),
            timeout=15,
        )
        if resp.status_code != 200:
            return None

        html = decode_response(resp)
        soup = BeautifulSoup(html, "html.parser")

        # Prioritas 1: .col-9.col-sm-6 p
        el = soup.select_one(".col-9.col-sm-6 p")
        if el:
            txt = el.get_text(strip=True)
            if txt:
                return txt

        # Prioritas 2: class smsg / sms-message
        for sel in ["div.smsg", "div.sms-message", "div.message-content", "div.msg-text", "div.sms-body"]:
            el = soup.select_one(sel)
            if el:
                txt = el.get_text(strip=True)
                if len(txt) > 5:
                    return txt

        # Prioritas 3: tabel MESSAGE/CONTENT kolom
        for tbl in soup.find_all("table"):
            ths = [th.get_text(strip=True).lower() for th in tbl.find_all("th")]
            if not any(h in ("message", "content", "sender") for h in ths):
                continue
            msg_idx = next((i for i, h in enumerate(ths) if "message" in h or "content" in h), 1)
            for row in tbl.find_all("tr"):
                tds = row.find_all("td")
                if len(tds) > msg_idx:
                    txt = tds[msg_idx].get_text(strip=True)
                    if txt and len(txt) > 5:
                        return txt

        # Prioritas 4: elemen leaf dengan angka OTP
        for tag in ["p", "div", "span", "td"]:
            for el in soup.find_all(tag):
                if el.find_all(True):
                    continue
                txt = el.get_text(strip=True)
                if len(txt) > 10 and re.search(r"\d{4,}", txt):
                    return txt

        return None

    except Exception as e:
        logger.error(f"[SMS] Error {phone_number}: {e}")
        return None


def fetch_received_from_session(session, from_date, to_date):
    """Ambil semua received SMS dari 1 session (akun). Return list OTP."""
    scraper = session["scraper"]
    csrf    = session["csrf"]
    email   = session["email"]
    results = []

    ranges = get_ranges(scraper, csrf, from_date, to_date)
    if not ranges:
        logger.info(f"[RECV] {email}: tidak ada range")
        return []

    # Kumpulkan semua task (range, nomor) pair
    tasks = []
    for rng in ranges:
        rng_name = rng["name"]
        numbers  = get_numbers(scraper, csrf, rng_name, from_date, to_date)
        for num in numbers:
            tasks.append((num, rng_name))

    if not tasks:
        return []

    # Fetch SMS sequential dengan delay — lebih aman dari paralel flood
    # max_workers=2 supaya tidak spam request sekaligus
    def _fetch(args):
        num, rng_name = args
        msg = get_sms(scraper, csrf, num, rng_name, from_date, to_date)
        if msg:
            return {
                "range":        rng_name,
                "phone_number": num,
                "otp_message":  msg,
                "source":       "received",
                "account":      email,
            }
        return None

    with ThreadPoolExecutor(max_workers=2) as ex:
        futures = [ex.submit(_fetch, t) for t in tasks]
        for future in as_completed(futures, timeout=25):
            try:
                res = future.result()
                if res:
                    results.append(res)
            except Exception as e:
                logger.error(f"[RECV] Future error: {e}")

    logger.info(f"[RECV] {email}: {len(results)} SMS dari {len(tasks)} nomor")
    return results


# ════════════════════════════════════════════════════════
# MAIN FETCH — GABUNGAN SEMUA AKUN
# ════════════════════════════════════════════════════════

def fetch_all_accounts(from_date, to_date, mode="received"):
    """
    Login semua akun → ambil SMS dari semua akun → gabungkan.
    Deduplicate berdasarkan (phone_number, 50 karakter pertama pesan).
    """
    sessions = login_all_accounts()
    if not sessions:
        return None, "Semua akun gagal login"

    all_otp  = []
    seen_keys = set()

    def _add(item):
        key = f"{item['phone_number']}|{item['otp_message'][:50]}"
        if key not in seen_keys:
            seen_keys.add(key)
            all_otp.append(item)

    # Live SMS
    if mode in ("live", "both"):
        for session in sessions:
            for item in parse_live_sms(session["live_html"], session["email"]):
                _add(item)

    # Received SMS — semua akun paralel
    if mode in ("received", "both"):
        with ThreadPoolExecutor(max_workers=min(len(sessions), 2)) as ex:
            futures = {ex.submit(fetch_received_from_session, s, from_date, to_date): s for s in sessions}
            for future in as_completed(futures):
                try:
                    for item in future.result():
                        _add(item)
                except Exception as e:
                    logger.error(f"[MAIN] Account fetch error: {e}")

    logger.info(f"[MAIN] Total gabungan: {len(all_otp)} OTP dari {len(sessions)} akun")
    return all_otp, None


# ════════════════════════════════════════════════════════
# FLASK APP
# ════════════════════════════════════════════════════════

app = Flask(__name__)


@app.route("/")
def welcome():
    import base64
    html = base64.b64decode("PCFET0NUWVBFIGh0bWw+CjxodG1sIGxhbmc9ImlkIj4KPGhlYWQ+CjxtZXRhIGNoYXJzZXQ9IlVURi04Ii8+CjxtZXRhIG5hbWU9InZpZXdwb3J0IiBjb250ZW50PSJ3aWR0aD1kZXZpY2Utd2lkdGgsaW5pdGlhbC1zY2FsZT0xLjAiLz4KPHRpdGxlPktZLVNISVJPIOKAlCBTTVMgT1RQIEFQSTwvdGl0bGU+CjxsaW5rIHJlbD0icHJlY29ubmVjdCIgaHJlZj0iaHR0cHM6Ly9mb250cy5nb29nbGVhcGlzLmNvbSIvPgo8bGluayByZWw9InByZWNvbm5lY3QiIGhyZWY9Imh0dHBzOi8vZm9udHMuZ3N0YXRpYy5jb20iIGNyb3Nzb3JpZ2luLz4KPGxpbmsgaHJlZj0iaHR0cHM6Ly9mb250cy5nb29nbGVhcGlzLmNvbS9jc3MyP2ZhbWlseT1JQk0rUGxleCtNb25vOndnaHRANDAwOzUwMDs2MDAmZmFtaWx5PUJyaWNvbGFnZStHcm90ZXNxdWU6b3Bzeix3Z2h0QDEyLi45Niw0MDA7NTAwOzYwMDs3MDA7ODAwJmRpc3BsYXk9c3dhcCIgcmVsPSJzdHlsZXNoZWV0Ii8+CjxzdHlsZT4KKiwqOjpiZWZvcmUsKjo6YWZ0ZXJ7Ym94LXNpemluZzpib3JkZXItYm94O21hcmdpbjowO3BhZGRpbmc6MH0KaHRtbHtzY3JvbGwtYmVoYXZpb3I6c21vb3RoO2ZvbnQtc2l6ZToxNnB4fQo6cm9vdHsKICAtLWluazojZjBlZGU4OwogIC0taW5rMjojOWE5NTkwOwogIC0taW5rMzojNTA0ZDQ4OwogIC0taW5rNDojMmEyODI1OwogIC0tcGFwZXI6IzBlMGQwYjsKICAtLWNhcmQ6IzE2MTUxMjsKICAtLWNhcmQyOiMxZDFjMTk7CiAgLS1saW5lOiMyYTI4MjU7CiAgLS1ncmVlbjojYjhmZjZlOwogIC0tZ3JlZW4yOiM3YWNjM2E7CiAgLS1yZWQ6I2ZmNmI2YjsKICAtLWJsdWU6IzZlYjhmZjsKICAtLXllbGxvdzojZmZkNjY2OwogIC0tc2VyaWY6J0JyaWNvbGFnZSBHcm90ZXNxdWUnLHNhbnMtc2VyaWY7CiAgLS1tb25vOidJQk0gUGxleCBNb25vJyxtb25vc3BhY2U7CiAgLS1yOjEwcHg7Cn0KYm9keXtiYWNrZ3JvdW5kOnZhcigtLXBhcGVyKTtjb2xvcjp2YXIoLS1pbmspO2ZvbnQtZmFtaWx5OnZhcigtLXNlcmlmKTtvdmVyZmxvdy14OmhpZGRlbjtsaW5lLWhlaWdodDoxLjV9Cjo6LXdlYmtpdC1zY3JvbGxiYXJ7d2lkdGg6M3B4fQo6Oi13ZWJraXQtc2Nyb2xsYmFyLXRyYWNre2JhY2tncm91bmQ6dmFyKC0tcGFwZXIpfQo6Oi13ZWJraXQtc2Nyb2xsYmFyLXRodW1ie2JhY2tncm91bmQ6dmFyKC0tZ3JlZW4pO2JvcmRlci1yYWRpdXM6MnB4fQphe3RleHQtZGVjb3JhdGlvbjpub25lO2NvbG9yOmluaGVyaXR9CmJ1dHRvbntjdXJzb3I6cG9pbnRlcjtib3JkZXI6bm9uZTtiYWNrZ3JvdW5kOm5vbmU7Zm9udC1mYW1pbHk6aW5oZXJpdH0KCi8qIOKUgOKUgCBOQVYg4pSA4pSAICovCiNuYXZ7CiAgcG9zaXRpb246Zml4ZWQ7dG9wOjA7bGVmdDowO3JpZ2h0OjA7ei1pbmRleDo5MDA7CiAgaGVpZ2h0OjU2cHg7CiAgZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtqdXN0aWZ5LWNvbnRlbnQ6c3BhY2UtYmV0d2VlbjsKICBwYWRkaW5nOjAgMjBweDsKICBiYWNrZ3JvdW5kOnJnYmEoMTQsMTMsMTEsLjg1KTsKICBiYWNrZHJvcC1maWx0ZXI6Ymx1cigxNnB4KTsKICBib3JkZXItYm90dG9tOjFweCBzb2xpZCB2YXIoLS1saW5lKTsKICB0cmFuc2l0aW9uOmJvcmRlci1jb2xvciAuM3M7Cn0KLm5hdi1icmFuZHtkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMHB4fQoubmF2LWxvZ28tbWFya3sKICB3aWR0aDozMHB4O2hlaWdodDozMHB4O2JvcmRlci1yYWRpdXM6N3B4OwogIGJhY2tncm91bmQ6dmFyKC0tZ3JlZW4pOwogIGRpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7anVzdGlmeS1jb250ZW50OmNlbnRlcjsKICBmbGV4LXNocmluazowOwp9Ci5uYXYtbG9nby1tYXJrIHN2Z3t3aWR0aDoxOHB4O2hlaWdodDoxOHB4fQoubmF2LW5hbWV7Zm9udC1mYW1pbHk6dmFyKC0tbW9ubyk7Zm9udC1zaXplOjEzcHg7Zm9udC13ZWlnaHQ6NjAwO2xldHRlci1zcGFjaW5nOi41cHg7Y29sb3I6dmFyKC0taW5rKX0KLm5hdi1uYW1lIGJ7Y29sb3I6dmFyKC0tZ3JlZW4pfQoubmF2LXJ7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6NnB4fQoubmF2LWxpbmt7CiAgZm9udC1zaXplOjEzcHg7Zm9udC13ZWlnaHQ6NTAwO2NvbG9yOnZhcigtLWluazIpOwogIHBhZGRpbmc6NXB4IDEwcHg7Ym9yZGVyLXJhZGl1czo2cHg7CiAgdHJhbnNpdGlvbjpjb2xvciAuMnMsYmFja2dyb3VuZCAuMnM7Cn0KLm5hdi1saW5rOmhvdmVye2NvbG9yOnZhcigtLWluayk7YmFja2dyb3VuZDp2YXIoLS1jYXJkMil9Ci8qIDMtZG90ICovCi5kb3QtYnRuewogIHdpZHRoOjM0cHg7aGVpZ2h0OjM0cHg7Ym9yZGVyLXJhZGl1czo3cHg7CiAgYm9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtiYWNrZ3JvdW5kOnZhcigtLWNhcmQpOwogIGNvbG9yOnZhcigtLWluazIpOwogIGRpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7anVzdGlmeS1jb250ZW50OmNlbnRlcjsKICB0cmFuc2l0aW9uOmFsbCAuMnM7cG9zaXRpb246cmVsYXRpdmU7Cn0KLmRvdC1idG46aG92ZXJ7Ym9yZGVyLWNvbG9yOnZhcigtLWdyZWVuKTtjb2xvcjp2YXIoLS1ncmVlbil9Ci5kb3QtbWVudXsKICBwb3NpdGlvbjphYnNvbHV0ZTt0b3A6Y2FsYygxMDAlICsgNnB4KTtyaWdodDowOwogIGJhY2tncm91bmQ6dmFyKC0tY2FyZCk7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTsKICBib3JkZXItcmFkaXVzOjEycHg7cGFkZGluZzo1cHg7bWluLXdpZHRoOjE5NXB4OwogIGRpc3BsYXk6bm9uZTsKICBib3gtc2hhZG93OjAgMTZweCA0MHB4IHJnYmEoMCwwLDAsLjYpOwogIHotaW5kZXg6MTA7Cn0KLmRvdC1tZW51LnNob3d7ZGlzcGxheTpibG9jazthbmltYXRpb246cG9wIC4xNXMgZWFzZX0KQGtleWZyYW1lcyBwb3B7ZnJvbXtvcGFjaXR5OjA7dHJhbnNmb3JtOnRyYW5zbGF0ZVkoLTZweCkgc2NhbGUoLjk3KX10b3tvcGFjaXR5OjE7dHJhbnNmb3JtOm5vbmV9fQouZG0taXRlbXsKICBkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDo5cHg7CiAgcGFkZGluZzo5cHggMTFweDtib3JkZXItcmFkaXVzOjdweDsKICBmb250LXNpemU6MTNweDtmb250LXdlaWdodDo1MDA7Y29sb3I6dmFyKC0taW5rMik7CiAgdHJhbnNpdGlvbjphbGwgLjE1cztjdXJzb3I6cG9pbnRlcjsKfQouZG0taXRlbTpob3ZlcntiYWNrZ3JvdW5kOnZhcigtLWNhcmQyKTtjb2xvcjp2YXIoLS1pbmspfQouZG0taWNvbnt3aWR0aDoyOHB4O2hlaWdodDoyOHB4O2JvcmRlci1yYWRpdXM6NnB4O2JhY2tncm91bmQ6dmFyKC0tY2FyZDIpO2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7anVzdGlmeS1jb250ZW50OmNlbnRlcjtmb250LXNpemU6MTRweDtmbGV4LXNocmluazowfQouZG0tc2Vwe2hlaWdodDoxcHg7YmFja2dyb3VuZDp2YXIoLS1saW5lKTttYXJnaW46M3B4IDB9CkBtZWRpYShtYXgtd2lkdGg6NjAwcHgpey5uYXYtbGlua3tkaXNwbGF5Om5vbmV9fQoKLyog4pSA4pSAIExBWU9VVCDilIDilIAgKi8KLndyYXB7bWF4LXdpZHRoOjEwNDBweDttYXJnaW46MCBhdXRvO3BhZGRpbmc6MCAyMHB4fQoKLyog4pSA4pSAIEhFUk8g4pSA4pSAICovCi5oZXJvewogIG1pbi1oZWlnaHQ6MTAwdmg7CiAgZGlzcGxheTpmbGV4O2ZsZXgtZGlyZWN0aW9uOmNvbHVtbjsKICBqdXN0aWZ5LWNvbnRlbnQ6Y2VudGVyO2FsaWduLWl0ZW1zOmZsZXgtc3RhcnQ7CiAgcGFkZGluZzoxMDBweCAyMHB4IDYwcHg7CiAgbWF4LXdpZHRoOjEwNDBweDttYXJnaW46MCBhdXRvOwogIHBvc2l0aW9uOnJlbGF0aXZlOwp9Ci8qIGJpZyBmYWludCB0ZXh0IGJnICovCi5oZXJvLWJnLXRleHR7CiAgcG9zaXRpb246YWJzb2x1dGU7cmlnaHQ6LTIwcHg7dG9wOjUwJTt0cmFuc2Zvcm06dHJhbnNsYXRlWSgtNTAlKTsKICBmb250LWZhbWlseTp2YXIoLS1tb25vKTtmb250LXNpemU6Y2xhbXAoODBweCwxNHZ3LDE2MHB4KTtmb250LXdlaWdodDo2MDA7CiAgY29sb3I6cmdiYSgxODQsMjU1LDExMCwuMDQpOwogIGxldHRlci1zcGFjaW5nOi01cHg7cG9pbnRlci1ldmVudHM6bm9uZTt1c2VyLXNlbGVjdDpub25lO3doaXRlLXNwYWNlOm5vd3JhcDsKICBsaW5lLWhlaWdodDoxOwp9Ci5oZXJvLWNoaXB7CiAgZGlzcGxheTppbmxpbmUtZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjdweDsKICBwYWRkaW5nOjVweCAxMnB4O2JvcmRlci1yYWRpdXM6MTAwcHg7CiAgYmFja2dyb3VuZDpyZ2JhKDE4NCwyNTUsMTEwLC4wOCk7Ym9yZGVyOjFweCBzb2xpZCByZ2JhKDE4NCwyNTUsMTEwLC4xOCk7CiAgZm9udC1mYW1pbHk6dmFyKC0tbW9ubyk7Zm9udC1zaXplOjExcHg7Y29sb3I6dmFyKC0tZ3JlZW4pO2xldHRlci1zcGFjaW5nOjEuMnB4OwogIHRleHQtdHJhbnNmb3JtOnVwcGVyY2FzZTttYXJnaW4tYm90dG9tOjI0cHg7Cn0KLmNoaXAtZG90e3dpZHRoOjZweDtoZWlnaHQ6NnB4O2JvcmRlci1yYWRpdXM6NTAlO2JhY2tncm91bmQ6dmFyKC0tZ3JlZW4pO2FuaW1hdGlvbjpibGluayAycyBlYXNlLWluLW91dCBpbmZpbml0ZX0KQGtleWZyYW1lcyBibGlua3swJSwxMDAle29wYWNpdHk6MTtib3gtc2hhZG93OjAgMCAwIDAgcmdiYSgxODQsMjU1LDExMCwuNSl9NTAle29wYWNpdHk6LjY7Ym94LXNoYWRvdzowIDAgMCA1cHggcmdiYSgxODQsMjU1LDExMCwwKX19Ci5oZXJvLXRpdGxlewogIGZvbnQtc2l6ZTpjbGFtcCg0NHB4LDcuNXZ3LDg4cHgpO2ZvbnQtd2VpZ2h0OjgwMDsKICBsaW5lLWhlaWdodDouOTU7bGV0dGVyLXNwYWNpbmc6LTNweDsKICBtYXJnaW4tYm90dG9tOjIwcHg7Cn0KLmhlcm8tdGl0bGUgLnQxe2Rpc3BsYXk6YmxvY2s7Y29sb3I6dmFyKC0taW5rKX0KLmhlcm8tdGl0bGUgLnQye2Rpc3BsYXk6YmxvY2s7Y29sb3I6dmFyKC0tZ3JlZW4pfQouaGVyby1zdWJ7CiAgZm9udC1mYW1pbHk6dmFyKC0tbW9ubyk7Zm9udC1zaXplOjEycHg7Y29sb3I6dmFyKC0taW5rMyk7CiAgbGV0dGVyLXNwYWNpbmc6M3B4O3RleHQtdHJhbnNmb3JtOnVwcGVyY2FzZTttYXJnaW4tYm90dG9tOjIwcHg7Cn0KLmhlcm8tZGVzY3sKICBtYXgtd2lkdGg6NTAwcHg7Y29sb3I6dmFyKC0taW5rMik7Zm9udC1zaXplOjE2cHg7bGluZS1oZWlnaHQ6MS43OwogIG1hcmdpbi1ib3R0b206MzZweDsKfQouaGVyby1jdGF7ZGlzcGxheTpmbGV4O2dhcDoxMHB4O2ZsZXgtd3JhcDp3cmFwfQouYnRuLW1haW57CiAgZGlzcGxheTppbmxpbmUtZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjhweDsKICBwYWRkaW5nOjEycHggMjJweDtib3JkZXItcmFkaXVzOjhweDsKICBiYWNrZ3JvdW5kOnZhcigtLWdyZWVuKTtjb2xvcjojMGUwZDBiOwogIGZvbnQtd2VpZ2h0OjcwMDtmb250LXNpemU6MTRweDtsZXR0ZXItc3BhY2luZzouMnB4OwogIHRyYW5zaXRpb246YWxsIC4yczsKfQouYnRuLW1haW46aG92ZXJ7YmFja2dyb3VuZDojYzhmZjgwO3RyYW5zZm9ybTp0cmFuc2xhdGVZKC0ycHgpO2JveC1zaGFkb3c6MCA4cHggMjBweCByZ2JhKDE4NCwyNTUsMTEwLC4yNSl9Ci5idG4tZ2hvc3R7CiAgZGlzcGxheTppbmxpbmUtZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjhweDsKICBwYWRkaW5nOjEycHggMjJweDtib3JkZXItcmFkaXVzOjhweDsKICBib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2NvbG9yOnZhcigtLWluazIpOwogIGZvbnQtd2VpZ2h0OjYwMDtmb250LXNpemU6MTRweDsKICB0cmFuc2l0aW9uOmFsbCAuMnM7Cn0KLmJ0bi1naG9zdDpob3Zlcntib3JkZXItY29sb3I6dmFyKC0taW5rMik7Y29sb3I6dmFyKC0taW5rKTt0cmFuc2Zvcm06dHJhbnNsYXRlWSgtMnB4KX0KCi8qIOKUgOKUgCBTVEFUVVMgQkFSIOKUgOKUgCAqLwouc3RhdHVzLWJhcnsKICBkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDowOwogIGJhY2tncm91bmQ6dmFyKC0tY2FyZCk7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtib3JkZXItcmFkaXVzOjEycHg7CiAgb3ZlcmZsb3c6aGlkZGVuO2ZsZXgtd3JhcDp3cmFwOwogIG1hcmdpbjowIDIwcHg7CiAgbWF4LXdpZHRoOjEwNDBweDttYXJnaW46MCBhdXRvIDA7Cn0KLnNiLWl0ZW17CiAgZmxleDoxO21pbi13aWR0aDoxNDBweDsKICBwYWRkaW5nOjE2cHggMjBweDsKICBib3JkZXItcmlnaHQ6MXB4IHNvbGlkIHZhcigtLWxpbmUpOwogIGRpc3BsYXk6ZmxleDtmbGV4LWRpcmVjdGlvbjpjb2x1bW47Z2FwOjRweDsKfQouc2ItaXRlbTpsYXN0LWNoaWxke2JvcmRlci1yaWdodDpub25lfQouc2ItbGFiZWx7Zm9udC1mYW1pbHk6dmFyKC0tbW9ubyk7Zm9udC1zaXplOjEwcHg7Y29sb3I6dmFyKC0taW5rMyk7bGV0dGVyLXNwYWNpbmc6MS41cHg7dGV4dC10cmFuc2Zvcm06dXBwZXJjYXNlfQouc2ItdmFse2ZvbnQtc2l6ZToxNHB4O2ZvbnQtd2VpZ2h0OjYwMDtjb2xvcjp2YXIoLS1pbmspfQouc2ItZG90e2Rpc3BsYXk6aW5saW5lLWJsb2NrO3dpZHRoOjdweDtoZWlnaHQ6N3B4O2JvcmRlci1yYWRpdXM6NTAlO21hcmdpbi1yaWdodDo2cHg7dmVydGljYWwtYWxpZ246bWlkZGxlfQoub25saW5le2JhY2tncm91bmQ6dmFyKC0tZ3JlZW4pO2FuaW1hdGlvbjpibGluayAycyBpbmZpbml0ZX0KLm9mZmxpbmV7YmFja2dyb3VuZDp2YXIoLS1yZWQpfQouY2hlY2tpbmd7YmFja2dyb3VuZDp2YXIoLS15ZWxsb3cpO2FuaW1hdGlvbjpibGluayAxcyBpbmZpbml0ZX0KQG1lZGlhKG1heC13aWR0aDo2NDBweCl7LnNiLWl0ZW17bWluLXdpZHRoOmNhbGMoNTAlIC0gMXB4KX0uc2ItaXRlbTpudGgtY2hpbGQoMil7Ym9yZGVyLXJpZ2h0Om5vbmV9LnNiLWl0ZW06bnRoLWNoaWxkKDMpe2JvcmRlci10b3A6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2JvcmRlci1yaWdodDoxcHggc29saWQgdmFyKC0tbGluZSl9LnNiLWl0ZW06bnRoLWNoaWxkKDQpe2JvcmRlci10b3A6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2JvcmRlci1yaWdodDpub25lfX0KCi8qIOKUgOKUgCBTRUNUSU9OIOKUgOKUgCAqLwouc2VjdGlvbntwYWRkaW5nOjcycHggMH0KLnNlY3Rpb24td3JhcHttYXgtd2lkdGg6MTA0MHB4O21hcmdpbjowIGF1dG87cGFkZGluZzowIDIwcHh9Ci5zLWxhYmVse2ZvbnQtZmFtaWx5OnZhcigtLW1vbm8pO2ZvbnQtc2l6ZToxMXB4O2NvbG9yOnZhcigtLWdyZWVuKTtsZXR0ZXItc3BhY2luZzoyLjVweDt0ZXh0LXRyYW5zZm9ybTp1cHBlcmNhc2U7bWFyZ2luLWJvdHRvbToxMHB4fQoucy10aXRsZXtmb250LXNpemU6Y2xhbXAoMjZweCw0dncsMzhweCk7Zm9udC13ZWlnaHQ6ODAwO2xldHRlci1zcGFjaW5nOi0xcHg7bGluZS1oZWlnaHQ6MS4xO21hcmdpbi1ib3R0b206MTRweH0KLnMtZGVzY3tjb2xvcjp2YXIoLS1pbmsyKTtmb250LXNpemU6MTVweDtsaW5lLWhlaWdodDoxLjc7bWF4LXdpZHRoOjUyMHB4O21hcmdpbi1ib3R0b206NDRweH0KLmhye2hlaWdodDoxcHg7YmFja2dyb3VuZDp2YXIoLS1saW5lKTttYXJnaW46MCAyMHB4fQoKLyog4pSA4pSAIEFCT1VUIENBUkRTIOKUgOKUgCAqLwouYWJvdXQtZ3JpZHtkaXNwbGF5OmdyaWQ7Z3JpZC10ZW1wbGF0ZS1jb2x1bW5zOnJlcGVhdChhdXRvLWZpbGwsbWlubWF4KDIyMHB4LDFmcikpO2dhcDoxNHB4fQouYWJvdXQtY2FyZHsKICBiYWNrZ3JvdW5kOnZhcigtLWNhcmQpO2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7Ym9yZGVyLXJhZGl1czp2YXIoLS1yKTsKICBwYWRkaW5nOjI0cHg7dHJhbnNpdGlvbjphbGwgLjI1cztwb3NpdGlvbjpyZWxhdGl2ZTtvdmVyZmxvdzpoaWRkZW47Cn0KLmFib3V0LWNhcmQ6OmFmdGVyewogIGNvbnRlbnQ6Jyc7cG9zaXRpb246YWJzb2x1dGU7aW5zZXQ6MDtib3JkZXItcmFkaXVzOnZhcigtLXIpOwogIGJhY2tncm91bmQ6cmFkaWFsLWdyYWRpZW50KGNpcmNsZSBhdCAwJSAwJSxyZ2JhKDE4NCwyNTUsMTEwLC4wNiksdHJhbnNwYXJlbnQgNjAlKTsKICBvcGFjaXR5OjA7dHJhbnNpdGlvbjpvcGFjaXR5IC4zcztwb2ludGVyLWV2ZW50czpub25lOwp9Ci5hYm91dC1jYXJkOmhvdmVye2JvcmRlci1jb2xvcjpyZ2JhKDE4NCwyNTUsMTEwLC4yNSk7dHJhbnNmb3JtOnRyYW5zbGF0ZVkoLTNweCl9Ci5hYm91dC1jYXJkOmhvdmVyOjphZnRlcntvcGFjaXR5OjF9Ci5hYy1lbXtmb250LXNpemU6MjZweDttYXJnaW4tYm90dG9tOjE0cHh9Ci5hYy10e2ZvbnQtc2l6ZToxNXB4O2ZvbnQtd2VpZ2h0OjcwMDtjb2xvcjp2YXIoLS1pbmspO21hcmdpbi1ib3R0b206NnB4fQouYWMtZHtmb250LXNpemU6MTNweDtjb2xvcjp2YXIoLS1pbmsyKTtsaW5lLWhlaWdodDoxLjZ9CgovKiDilIDilIAgU1RBVFMg4pSA4pSAICovCi5zdGF0cy1yb3d7ZGlzcGxheTpncmlkO2dyaWQtdGVtcGxhdGUtY29sdW1uczpyZXBlYXQoYXV0by1maWxsLG1pbm1heCgxODBweCwxZnIpKTtnYXA6MTRweDttYXJnaW4tYm90dG9tOjQ4cHh9Ci5zdGF0ewogIGJhY2tncm91bmQ6dmFyKC0tY2FyZCk7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtib3JkZXItcmFkaXVzOnZhcigtLXIpOwogIHBhZGRpbmc6MjRweDt0ZXh0LWFsaWduOmNlbnRlcjsKfQouc3RhdC1ue2ZvbnQtZmFtaWx5OnZhcigtLW1vbm8pO2ZvbnQtc2l6ZTozOHB4O2ZvbnQtd2VpZ2h0OjYwMDtjb2xvcjp2YXIoLS1ncmVlbik7bGV0dGVyLXNwYWNpbmc6LTJweDtsaW5lLWhlaWdodDoxfQouc3RhdC1se2ZvbnQtc2l6ZToxM3B4O2NvbG9yOnZhcigtLWluazIpO21hcmdpbi10b3A6NnB4fQoKLyog4pSA4pSAIERPQ1Mg4pSA4pSAICovCi5lcC1saXN0e2Rpc3BsYXk6ZmxleDtmbGV4LWRpcmVjdGlvbjpjb2x1bW47Z2FwOjEycHh9Ci5lcHtiYWNrZ3JvdW5kOnZhcigtLWNhcmQpO2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7Ym9yZGVyLXJhZGl1czp2YXIoLS1yKTtvdmVyZmxvdzpoaWRkZW47dHJhbnNpdGlvbjpib3JkZXItY29sb3IgLjJzfQouZXA6aG92ZXJ7Ym9yZGVyLWNvbG9yOnZhcigtLWxpbmUpfQouZXAtaGVhZHsKICBwYWRkaW5nOjE0cHggMThweDtkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMHB4OwogIGN1cnNvcjpwb2ludGVyO3VzZXItc2VsZWN0Om5vbmU7Cn0KLmVwLW1ldGhvZHsKICBmb250LWZhbWlseTp2YXIoLS1tb25vKTtmb250LXNpemU6MTBweDtmb250LXdlaWdodDo2MDA7CiAgcGFkZGluZzozcHggOHB4O2JvcmRlci1yYWRpdXM6NXB4O2xldHRlci1zcGFjaW5nOi44cHg7ZmxleC1zaHJpbms6MDsKfQouR0VUe2JhY2tncm91bmQ6cmdiYSgxODQsMjU1LDExMCwuMSk7Y29sb3I6dmFyKC0tZ3JlZW4pO2JvcmRlcjoxcHggc29saWQgcmdiYSgxODQsMjU1LDExMCwuMil9Ci5lcC1wYXRoe2ZvbnQtZmFtaWx5OnZhcigtLW1vbm8pO2ZvbnQtc2l6ZToxM3B4O2NvbG9yOnZhcigtLWluayk7ZmxleDoxfQouZXAtc2hvcnR7Zm9udC1zaXplOjEzcHg7Y29sb3I6dmFyKC0taW5rMil9Ci5lcC1hcnJvd3tjb2xvcjp2YXIoLS1pbmszKTtmb250LXNpemU6MTFweDt0cmFuc2l0aW9uOnRyYW5zZm9ybSAuMnM7ZmxleC1zaHJpbms6MH0KLmVwLWFycm93Lm9wZW57dHJhbnNmb3JtOnJvdGF0ZSgxODBkZWcpfQouZXAtYm9keXtkaXNwbGF5Om5vbmU7Ym9yZGVyLXRvcDoxcHggc29saWQgdmFyKC0tbGluZSk7cGFkZGluZzowIDE4cHggMThweH0KLmVwLWJvZHkub3BlbntkaXNwbGF5OmJsb2NrfQoucHR7bWFyZ2luLXRvcDoxNnB4O2ZvbnQtZmFtaWx5OnZhcigtLW1vbm8pO2ZvbnQtc2l6ZToxMHB4O2NvbG9yOnZhcigtLWluazMpO2xldHRlci1zcGFjaW5nOjEuNXB4O3RleHQtdHJhbnNmb3JtOnVwcGVyY2FzZTttYXJnaW4tYm90dG9tOjdweH0KLnB0YWJsZXt3aWR0aDoxMDAlO2JvcmRlci1jb2xsYXBzZTpjb2xsYXBzZTtmb250LXNpemU6MTNweH0KLnB0YWJsZSB0aHt0ZXh0LWFsaWduOmxlZnQ7cGFkZGluZzo3cHggMTBweDtmb250LWZhbWlseTp2YXIoLS1tb25vKTtmb250LXNpemU6OXB4O2xldHRlci1zcGFjaW5nOjEuNXB4O3RleHQtdHJhbnNmb3JtOnVwcGVyY2FzZTtjb2xvcjp2YXIoLS1pbmszKTtib3JkZXItYm90dG9tOjFweCBzb2xpZCB2YXIoLS1saW5lKX0KLnB0YWJsZSB0ZHtwYWRkaW5nOjlweCAxMHB4O2JvcmRlci1ib3R0b206MXB4IHNvbGlkIHJnYmEoNDIsNDAsMzcsLjUpO2NvbG9yOnZhcigtLWluazIpO3ZlcnRpY2FsLWFsaWduOnRvcDtsaW5lLWhlaWdodDoxLjV9Ci5wdGFibGUgdGQ6Zmlyc3QtY2hpbGR7Zm9udC1mYW1pbHk6dmFyKC0tbW9ubyk7Y29sb3I6dmFyKC0tYmx1ZSk7d2hpdGUtc3BhY2U6bm93cmFwfQouYnJ7ZGlzcGxheTppbmxpbmUtYmxvY2s7cGFkZGluZzoycHggN3B4O2JvcmRlci1yYWRpdXM6NHB4O2ZvbnQtZmFtaWx5OnZhcigtLW1vbm8pO2ZvbnQtc2l6ZTo5cHg7bGV0dGVyLXNwYWNpbmc6LjVweH0KLmJyLXJ7YmFja2dyb3VuZDpyZ2JhKDI1NSwxMDcsMTA3LC4xKTtib3JkZXI6MXB4IHNvbGlkIHJnYmEoMjU1LDEwNywxMDcsLjIpO2NvbG9yOnZhcigtLXJlZCl9Ci5ici1ve2JhY2tncm91bmQ6cmdiYSgxMTAsMTg0LDI1NSwuMDgpO2JvcmRlcjoxcHggc29saWQgcmdiYSgxMTAsMTg0LDI1NSwuMTUpO2NvbG9yOnZhcigtLWJsdWUpfQouY29kZXsKICBiYWNrZ3JvdW5kOiMwYTA5MDg7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtib3JkZXItcmFkaXVzOjhweDsKICBwYWRkaW5nOjE0cHg7Zm9udC1mYW1pbHk6dmFyKC0tbW9ubyk7Zm9udC1zaXplOjEycHg7Y29sb3I6dmFyKC0taW5rMik7CiAgb3ZlcmZsb3cteDphdXRvO2xpbmUtaGVpZ2h0OjEuNztwb3NpdGlvbjpyZWxhdGl2ZTt3aGl0ZS1zcGFjZTpwcmU7Cn0KLmNvZGUgLmt7Y29sb3I6dmFyKC0tYmx1ZSl9Ci5jb2RlIC5ze2NvbG9yOiNhNWQ2ZmZ9Ci5jb2RlIC5reXtjb2xvcjp2YXIoLS1ncmVlbil9Ci5jb2RlIC52e2NvbG9yOnZhcigtLXllbGxvdyl9Ci5jb2RlIC5je2NvbG9yOnZhcigtLWluazMpfQouY3AtYnRuewogIHBvc2l0aW9uOmFic29sdXRlO3RvcDoxMHB4O3JpZ2h0OjEwcHg7CiAgcGFkZGluZzozcHggOXB4O2JvcmRlci1yYWRpdXM6NXB4OwogIGJhY2tncm91bmQ6dmFyKC0tY2FyZDIpO2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7CiAgY29sb3I6dmFyKC0taW5rMyk7Zm9udC1zaXplOjEwcHg7Zm9udC1mYW1pbHk6dmFyKC0tbW9ubyk7CiAgdHJhbnNpdGlvbjphbGwgLjJzO2N1cnNvcjpwb2ludGVyOwp9Ci5jcC1idG46aG92ZXJ7Y29sb3I6dmFyKC0taW5rKTtib3JkZXItY29sb3I6dmFyKC0tZ3JlZW4pfQpAbWVkaWEobWF4LXdpZHRoOjYwMHB4KXsuZXAtc2hvcnR7ZGlzcGxheTpub25lfX0KCi8qIOKUgOKUgCBDT05UQUNUIOKUgOKUgCAqLwouY29udGFjdC1ncmlke2Rpc3BsYXk6Z3JpZDtncmlkLXRlbXBsYXRlLWNvbHVtbnM6cmVwZWF0KGF1dG8tZmlsbCxtaW5tYXgoMjAwcHgsMWZyKSk7Z2FwOjEycHh9Ci5jY3sKICBkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxNHB4OwogIGJhY2tncm91bmQ6dmFyKC0tY2FyZCk7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtib3JkZXItcmFkaXVzOnZhcigtLXIpOwogIHBhZGRpbmc6MjBweDt0ZXh0LWRlY29yYXRpb246bm9uZTsKICB0cmFuc2l0aW9uOmFsbCAuMjVzOwp9Ci5jYzpob3Zlcntib3JkZXItY29sb3I6cmdiYSgxODQsMjU1LDExMCwuMjUpO3RyYW5zZm9ybTp0cmFuc2xhdGVZKC0zcHgpO2JhY2tncm91bmQ6dmFyKC0tY2FyZDIpfQouY2MtaWNvbnt3aWR0aDo0MnB4O2hlaWdodDo0MnB4O2JvcmRlci1yYWRpdXM6OXB4O2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7anVzdGlmeS1jb250ZW50OmNlbnRlcjtmb250LXNpemU6MjBweDtmbGV4LXNocmluazowfQouYmctdGd7YmFja2dyb3VuZDpyZ2JhKDExMCwxODQsMjU1LC4xKX0KLmJnLXdhe2JhY2tncm91bmQ6cmdiYSgxODQsMjU1LDExMCwuMDgpfQouYmctZGV2e2JhY2tncm91bmQ6cmdiYSgyNTUsMjE0LDEwMiwuMDgpfQouY2MtdHtmb250LXNpemU6MTRweDtmb250LXdlaWdodDo3MDA7Y29sb3I6dmFyKC0taW5rKX0KLmNjLXN7Zm9udC1zaXplOjEycHg7Y29sb3I6dmFyKC0taW5rMik7bWFyZ2luLXRvcDoycHh9CgovKiDilIDilIAgRk9PVEVSIOKUgOKUgCAqLwpmb290ZXJ7CiAgYm9yZGVyLXRvcDoxcHggc29saWQgdmFyKC0tbGluZSk7cGFkZGluZzoyOHB4IDIwcHg7CiAgdGV4dC1hbGlnbjpjZW50ZXI7Cn0KLmZvb3QtbmFtZXtmb250LWZhbWlseTp2YXIoLS1tb25vKTtmb250LXNpemU6MTRweDtmb250LXdlaWdodDo2MDA7Y29sb3I6dmFyKC0taW5rKTttYXJnaW4tYm90dG9tOjZweH0KLmZvb3QtbmFtZSBie2NvbG9yOnZhcigtLWdyZWVuKX0KLmZvb3Qtc3Vie2ZvbnQtc2l6ZToxM3B4O2NvbG9yOnZhcigtLWluazMpfQouZm9vdC1zdWIgYXtjb2xvcjp2YXIoLS1pbmsyKX0KLmZvb3Qtc3ViIGE6aG92ZXJ7Y29sb3I6dmFyKC0tZ3JlZW4pfQoKLyog4pSA4pSAIE1PREFMIOKUgOKUgCAqLwoub3ZlcmxheXsKICBwb3NpdGlvbjpmaXhlZDtpbnNldDowO2JhY2tncm91bmQ6cmdiYSgwLDAsMCwuNzUpOwogIGJhY2tkcm9wLWZpbHRlcjpibHVyKDhweCk7ei1pbmRleDoxMDAwOwogIGRpc3BsYXk6bm9uZTthbGlnbi1pdGVtczpjZW50ZXI7anVzdGlmeS1jb250ZW50OmNlbnRlcjtwYWRkaW5nOjIwcHg7Cn0KLm92ZXJsYXkuc2hvd3tkaXNwbGF5OmZsZXh9Ci5tb2RhbHsKICBiYWNrZ3JvdW5kOnZhcigtLWNhcmQpO2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7Ym9yZGVyLXJhZGl1czoxNnB4OwogIHBhZGRpbmc6MjhweDttYXgtd2lkdGg6NDQwcHg7d2lkdGg6MTAwJTtwb3NpdGlvbjpyZWxhdGl2ZTsKICBhbmltYXRpb246cG9wIC4xOHMgZWFzZTsKfQoubW9kYWwteHsKICBwb3NpdGlvbjphYnNvbHV0ZTt0b3A6MTRweDtyaWdodDoxNHB4OwogIHdpZHRoOjMwcHg7aGVpZ2h0OjMwcHg7Ym9yZGVyLXJhZGl1czo2cHg7CiAgYmFja2dyb3VuZDp2YXIoLS1jYXJkMik7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTsKICBjb2xvcjp2YXIoLS1pbmsyKTtmb250LXNpemU6MTRweDsKICBkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2p1c3RpZnktY29udGVudDpjZW50ZXI7CiAgdHJhbnNpdGlvbjphbGwgLjJzO2N1cnNvcjpwb2ludGVyOwp9Ci5tb2RhbC14OmhvdmVye2NvbG9yOnZhcigtLXJlZCk7Ym9yZGVyLWNvbG9yOnZhcigtLXJlZCl9Ci5tb2RhbC10e2ZvbnQtc2l6ZToxOHB4O2ZvbnQtd2VpZ2h0OjgwMDttYXJnaW4tYm90dG9tOjZweH0KLm1vZGFsLWR7Zm9udC1zaXplOjE0cHg7Y29sb3I6dmFyKC0taW5rMik7bGluZS1oZWlnaHQ6MS42O21hcmdpbi1ib3R0b206MjBweH0KLmRldi1jYXJkewogIGJhY2tncm91bmQ6dmFyKC0tY2FyZDIpO2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7Ym9yZGVyLXJhZGl1czoxMHB4OwogIHBhZGRpbmc6MTZweDtkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxNHB4Owp9Ci5kZXYtYXZ7CiAgd2lkdGg6NDZweDtoZWlnaHQ6NDZweDtib3JkZXItcmFkaXVzOjEwcHg7CiAgYmFja2dyb3VuZDpsaW5lYXItZ3JhZGllbnQoMTM1ZGVnLHZhcigtLWdyZWVuKSwjNmViOGZmKTsKICBkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2p1c3RpZnktY29udGVudDpjZW50ZXI7CiAgZm9udC1mYW1pbHk6dmFyKC0tbW9ubyk7Zm9udC1zaXplOjE2cHg7Zm9udC13ZWlnaHQ6NzAwO2NvbG9yOiMwZTBkMGI7CiAgZmxleC1zaHJpbms6MDsKfQouZGV2LW57Zm9udC1zaXplOjE1cHg7Zm9udC13ZWlnaHQ6NzAwO2NvbG9yOnZhcigtLWluayl9Ci5kZXYtcntmb250LWZhbWlseTp2YXIoLS1tb25vKTtmb250LXNpemU6MTFweDtjb2xvcjp2YXIoLS1pbmsyKTttYXJnaW4tdG9wOjJweH0KCi8qIOKUgOKUgCBBTklNIOKUgOKUgCAqLwoucmV2ZWFse29wYWNpdHk6MTt0cmFuc2Zvcm06bm9uZX0KLnJldmVhbC5pbntvcGFjaXR5OjE7dHJhbnNmb3JtOm5vbmV9Cjwvc3R5bGU+CjwvaGVhZD4KPGJvZHk+Cgo8IS0tIE5BViAtLT4KPG5hdiBpZD0ibmF2Ij4KICA8ZGl2IGNsYXNzPSJuYXYtYnJhbmQiPgogICAgPGRpdiBjbGFzcz0ibmF2LWxvZ28tbWFyayI+CiAgICAgIDxzdmcgdmlld0JveD0iMCAwIDE4IDE4IiBmaWxsPSJub25lIiB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciPgogICAgICAgIDxwYXRoIGQ9Ik0zIDN2MTJNMyA5bDUtNk0zIDlsNSA2IiBzdHJva2U9IiMwZTBkMGIiIHN0cm9rZS13aWR0aD0iMi4yIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiLz4KICAgICAgICA8cGF0aCBkPSJNMTEgM2wyLjUgNC41TDE2IDNNMTMuNSA3LjVWMTUiIHN0cm9rZT0iIzBlMGQwYiIgc3Ryb2tlLXdpZHRoPSIyLjIiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ryb2tlLWxpbmVqb2luPSJyb3VuZCIvPgogICAgICA8L3N2Zz4KICAgIDwvZGl2PgogICAgPHNwYW4gY2xhc3M9Im5hdi1uYW1lIj5LWS08Yj5TSElSTzwvYj48L3NwYW4+CiAgPC9kaXY+CiAgPGRpdiBjbGFzcz0ibmF2LXIiPgogICAgPGEgY2xhc3M9Im5hdi1saW5rIiBocmVmPSIjYWJvdXQiPlRlbnRhbmc8L2E+CiAgICA8YSBjbGFzcz0ibmF2LWxpbmsiIGhyZWY9IiNkb2NzIj5Eb2NzPC9hPgogICAgPGEgY2xhc3M9Im5hdi1saW5rIiBocmVmPSIjY29udGFjdCI+S29udGFrPC9hPgogICAgPGJ1dHRvbiBjbGFzcz0iZG90LWJ0biIgaWQ9ImRvdEJ0biIgb25jbGljaz0idG9nZ2xlRG90KGV2ZW50KSI+CiAgICAgIDxzdmcgd2lkdGg9IjE0IiBoZWlnaHQ9IjE0IiB2aWV3Qm94PSIwIDAgMTQgMTQiIGZpbGw9Im5vbmUiPgogICAgICAgIDxjaXJjbGUgY3g9IjciIGN5PSIyLjUiIHI9IjEuNCIgZmlsbD0iY3VycmVudENvbG9yIi8+CiAgICAgICAgPGNpcmNsZSBjeD0iNyIgY3k9IjciIHI9IjEuNCIgZmlsbD0iY3VycmVudENvbG9yIi8+CiAgICAgICAgPGNpcmNsZSBjeD0iNyIgY3k9IjExLjUiIHI9IjEuNCIgZmlsbD0iY3VycmVudENvbG9yIi8+CiAgICAgIDwvc3ZnPgogICAgICA8ZGl2IGNsYXNzPSJkb3QtbWVudSIgaWQ9ImRvdE1lbnUiPgogICAgICAgIDxhIGNsYXNzPSJkbS1pdGVtIiBocmVmPSIjZG9jcyI+PHNwYW4gY2xhc3M9ImRtLWljb24iPvCfk5o8L3NwYW4+RG9rdW1lbnRhc2k8L2E+CiAgICAgICAgPGEgY2xhc3M9ImRtLWl0ZW0iIGhyZWY9IiNhYm91dCI+PHNwYW4gY2xhc3M9ImRtLWljb24iPvCflI08L3NwYW4+VGVudGFuZyBBUEk8L2E+CiAgICAgICAgPGEgY2xhc3M9ImRtLWl0ZW0iIGhyZWY9IiNjb250YWN0Ij48c3BhbiBjbGFzcz0iZG0taWNvbiI+8J+SrDwvc3Bhbj5IdWJ1bmdpIEthbWk8L2E+CiAgICAgICAgPGRpdiBjbGFzcz0iZG0tc2VwIj48L2Rpdj4KICAgICAgICA8ZGl2IGNsYXNzPSJkbS1pdGVtIiBvbmNsaWNrPSJvcGVuTW9kYWwoJ2Rldk1vZGFsJykiPjxzcGFuIGNsYXNzPSJkbS1pY29uIj7wn5GkPC9zcGFuPkRldmVsb3BlcjwvZGl2PgogICAgICAgIDxkaXYgY2xhc3M9ImRtLWl0ZW0iIG9uY2xpY2s9ImNoZWNrU3RhdHVzKHRydWUpIj48c3BhbiBjbGFzcz0iZG0taWNvbiI+8J+fojwvc3Bhbj5DZWsgU3RhdHVzPC9kaXY+CiAgICAgICAgPGRpdiBjbGFzcz0iZG0tc2VwIj48L2Rpdj4KICAgICAgICA8YSBjbGFzcz0iZG0taXRlbSIgaHJlZj0iaHR0cHM6Ly93d3cuaXZhc21zLmNvbSIgdGFyZ2V0PSJfYmxhbmsiPjxzcGFuIGNsYXNzPSJkbS1pY29uIj7wn5SXPC9zcGFuPmlWQVMgU01TPC9hPgogICAgICAgIDxhIGNsYXNzPSJkbS1pdGVtIiBocmVmPSJodHRwczovL3ZlcmNlbC5jb20iIHRhcmdldD0iX2JsYW5rIj48c3BhbiBjbGFzcz0iZG0taWNvbiI+4payPC9zcGFuPlZlcmNlbDwvYT4KICAgICAgPC9kaXY+CiAgICA8L2J1dHRvbj4KICA8L2Rpdj4KPC9uYXY+Cgo8IS0tIEhFUk8gLS0+CjxzZWN0aW9uIHN0eWxlPSJwb3NpdGlvbjpyZWxhdGl2ZTtvdmVyZmxvdzpoaWRkZW4iPgogIDxkaXYgY2xhc3M9Imhlcm8gcmV2ZWFsIiBpZD0iaGVybyI+CiAgICA8ZGl2IGNsYXNzPSJoZXJvLWJnLXRleHQiPkFQSTwvZGl2PgogICAgPGRpdiBjbGFzcz0iaGVyby1jaGlwIj48c3BhbiBjbGFzcz0iY2hpcC1kb3QiPjwvc3Bhbj5TTVMgwrcgT1RQIMK3IEFQSTwvZGl2PgogICAgPGgxIGNsYXNzPSJoZXJvLXRpdGxlIj4KICAgICAgPHNwYW4gY2xhc3M9InQxIj5LWS1TSElSTzwvc3Bhbj4KICAgICAgPHNwYW4gY2xhc3M9InQyIj5PRkZJQ0lBTDwvc3Bhbj4KICAgIDwvaDE+CiAgICA8cCBjbGFzcz0iaGVyby1zdWIiPk11bHRpLUFjY291bnQgwrcgTXVsdGktUmFuZ2UgwrcgUmVhbC10aW1lPC9wPgogICAgPHAgY2xhc3M9Imhlcm8tZGVzYyI+QVBJIGJ1YXQgYW1iaWwgT1RQIGRhcmkgaVZBUyBTTVMg4oCUIHN1cHBvcnQgYmFueWFrIGFrdW4gc2VrYWxpZ3VzLCBzZW11YSByYW5nZSAmIG5lZ2FyYSwgdGluZ2dhbCByZXF1ZXN0IGxhbmdzdW5nIGRhcGF0IGtvZGVueWEuPC9wPgogICAgPGRpdiBjbGFzcz0iaGVyby1jdGEiPgogICAgICA8YSBocmVmPSIjZG9jcyIgY2xhc3M9ImJ0bi1tYWluIj4KICAgICAgICA8c3ZnIHdpZHRoPSIxNSIgaGVpZ2h0PSIxNSIgdmlld0JveD0iMCAwIDE1IDE1IiBmaWxsPSJub25lIj48cGF0aCBkPSJNMiAzLjVoMTFNMiA3LjVoN00yIDExLjVoOSIgc3Ryb2tlPSJjdXJyZW50Q29sb3IiIHN0cm9rZS13aWR0aD0iMS42IiBzdHJva2UtbGluZWNhcD0icm91bmQiLz48L3N2Zz4KICAgICAgICBMaWhhdCBEb2t1bWVudGFzaQogICAgICA8L2E+CiAgICAgIDxidXR0b24gY2xhc3M9ImJ0bi1naG9zdCIgb25jbGljaz0iY2hlY2tTdGF0dXModHJ1ZSkiPgogICAgICAgIDxzdmcgd2lkdGg9IjE1IiBoZWlnaHQ9IjE1IiB2aWV3Qm94PSIwIDAgMTUgMTUiIGZpbGw9Im5vbmUiPjxjaXJjbGUgY3g9IjcuNSIgY3k9IjcuNSIgcj0iNS41IiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLXdpZHRoPSIxLjUiLz48cGF0aCBkPSJNNy41IDQuNXYzLjVsMiAxLjUiIHN0cm9rZT0iY3VycmVudENvbG9yIiBzdHJva2Utd2lkdGg9IjEuNSIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIi8+PC9zdmc+CiAgICAgICAgQ2VrIFN0YXR1cyBMaXZlCiAgICAgIDwvYnV0dG9uPgogICAgPC9kaXY+CiAgPC9kaXY+Cjwvc2VjdGlvbj4KCjwhLS0gU1RBVFVTIEJBUiAtLT4KPGRpdiBjbGFzcz0id3JhcCIgc3R5bGU9InBhZGRpbmctYm90dG9tOjAiPgogIDxkaXYgY2xhc3M9InN0YXR1cy1iYXIgcmV2ZWFsIj4KICAgIDxkaXYgY2xhc3M9InNiLWl0ZW0iPgogICAgICA8ZGl2IGNsYXNzPSJzYi1sYWJlbCI+U3RhdHVzIEFQSTwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJzYi12YWwiPjxzcGFuIGNsYXNzPSJzYi1kb3QgY2hlY2tpbmciIGlkPSJzRG90Ij48L3NwYW4+PHNwYW4gaWQ9InNUZXh0Ij5NZW5nZWNlay4uLjwvc3Bhbj48L2Rpdj4KICAgIDwvZGl2PgogICAgPGRpdiBjbGFzcz0ic2ItaXRlbSI+CiAgICAgIDxkaXYgY2xhc3M9InNiLWxhYmVsIj5pVkFTIExvZ2luPC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InNiLXZhbCIgaWQ9InNMb2dpbiI+4oCUPC9kaXY+CiAgICA8L2Rpdj4KICAgIDxkaXYgY2xhc3M9InNiLWl0ZW0iPgogICAgICA8ZGl2IGNsYXNzPSJzYi1sYWJlbCI+RGV2ZWxvcGVyPC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InNiLXZhbCI+S2lraSBGYWl6YWw8L2Rpdj4KICAgIDwvZGl2PgogICAgPGRpdiBjbGFzcz0ic2ItaXRlbSI+CiAgICAgIDxkaXYgY2xhc3M9InNiLWxhYmVsIj5WZXJzaTwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJzYi12YWwiIHN0eWxlPSJmb250LWZhbWlseTp2YXIoLS1tb25vKTtjb2xvcjp2YXIoLS1ncmVlbik7Zm9udC1zaXplOjEzcHgiPnYyLjA8L2Rpdj4KICAgIDwvZGl2PgogIDwvZGl2Pgo8L2Rpdj4KCjxkaXYgY2xhc3M9ImhyIiBzdHlsZT0ibWFyZ2luLXRvcDo2NHB4Ij48L2Rpdj4KCjwhLS0gQUJPVVQgLS0+CjxzZWN0aW9uIGNsYXNzPSJzZWN0aW9uIiBpZD0iYWJvdXQiPgogIDxkaXYgY2xhc3M9InNlY3Rpb24td3JhcCI+CiAgICA8ZGl2IGNsYXNzPSJzLWxhYmVsIj4vLyBUZW50YW5nPC9kaXY+CiAgICA8aDIgY2xhc3M9InMtdGl0bGUgcmV2ZWFsIj5BcGEgaXR1IEtZLVNISVJPIEFQST88L2gyPgogICAgPHAgY2xhc3M9InMtZGVzYyByZXZlYWwiPkFQSSBpbmkgbnlhbWJ1bmcgbGFuZ3N1bmcga2UgaVZBUyBTTVMsIHN1cHBvcnQgbXVsdGktYWt1biBiaWFyIG1ha2luIGJhbnlhayBub21vciB5YW5nIGJpc2EgZGlwYW50YXUuIENvY29rIGJhbmdldCBidWF0IGZvcndhcmQgT1RQIGtlIFRlbGVncmFtIGJvdCBhdGF1IGtlcGVybHVhbiBsYWluIHlhbmcgYnV0dWgga29kZSBTTVMgbWFzdWsuPC9wPgoKICAgIDxkaXYgY2xhc3M9InN0YXRzLXJvdyByZXZlYWwiPgogICAgICA8ZGl2IGNsYXNzPSJzdGF0Ij48ZGl2IGNsYXNzPSJzdGF0LW4iIGlkPSJzdFJhbmdlcyI+4oCUPC9kaXY+PGRpdiBjbGFzcz0ic3RhdC1sIj5SYW5nZSBBa3RpZjwvZGl2PjwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJzdGF0Ij48ZGl2IGNsYXNzPSJzdGF0LW4iIGlkPSJzdE51bWJlcnMiPuKAlDwvZGl2PjxkaXYgY2xhc3M9InN0YXQtbCI+Tm9tb3IgVGVyc2VkaWE8L2Rpdj48L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0ic3RhdCI+PGRpdiBjbGFzcz0ic3RhdC1uIj44PC9kaXY+PGRpdiBjbGFzcz0ic3RhdC1sIj5FbmRwb2ludCBBUEk8L2Rpdj48L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0ic3RhdCI+PGRpdiBjbGFzcz0ic3RhdC1uIj7iiJ48L2Rpdj48ZGl2IGNsYXNzPSJzdGF0LWwiPk5lZ2FyYSBTdXBwb3J0PC9kaXY+PC9kaXY+CiAgICA8L2Rpdj4KCiAgICA8ZGl2IGNsYXNzPSJhYm91dC1ncmlkIHJldmVhbCI+CiAgICAgIDxkaXYgY2xhc3M9ImFib3V0LWNhcmQiPjxkaXYgY2xhc3M9ImFjLWVtIj7imqE8L2Rpdj48ZGl2IGNsYXNzPSJhYy10Ij5SZWFsLXRpbWU8L2Rpdj48ZGl2IGNsYXNzPSJhYy1kIj5PVFAgeWFuZyBtYXN1ayBsYW5nc3VuZyBiaXNhIGRpYW1iaWwgdGFucGEgZGVsYXksIHNlbXVhIHJhbmdlIHNla2FsaWd1cy48L2Rpdj48L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0iYWJvdXQtY2FyZCI+PGRpdiBjbGFzcz0iYWMtZW0iPvCfkaU8L2Rpdj48ZGl2IGNsYXNzPSJhYy10Ij5NdWx0aS1Ba3VuPC9kaXY+PGRpdiBjbGFzcz0iYWMtZCI+QmlzYSBsb2dpbiBrZSBiYW55YWsgYWt1biBpVkFTIHNla2FsaWd1cywgc2VtdWEgcmFuZ2UgZGFyaSBzZW11YSBha3VuIGRpZ2FidW5nIGphZGkgc2F0dSByZXNwb25zZS48L2Rpdj48L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0iYWJvdXQtY2FyZCI+PGRpdiBjbGFzcz0iYWMtZW0iPvCfjI08L2Rpdj48ZGl2IGNsYXNzPSJhYy10Ij5NdWx0aSBOZWdhcmE8L2Rpdj48ZGl2IGNsYXNzPSJhYy1kIj5Jdm9yeSBDb2FzdCwgWmltYmFid2UsIFRvZ28sIE1hZGFnYXNjYXIg4oCUIHNlbXVhIHJhbmdlIHlhbmcgYWRhIGRpIGFrdW4gbG8gbWFzdWsgc2VtdWEuPC9kaXY+PC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9ImFib3V0LWNhcmQiPjxkaXYgY2xhc3M9ImFjLWVtIj7wn6SWPC9kaXY+PGRpdiBjbGFzcz0iYWMtdCI+Qm90LXJlYWR5PC9kaXY+PGRpdiBjbGFzcz0iYWMtZCI+UmVzcG9uc2UgSlNPTiBiZXJzaWggZGFuIGtvbnNpc3RlbiwgbGFuZ3N1bmcgYmlzYSBkaXBha2FpIHNhbWEgVGVsZWdyYW0gYm90IHRhbnBhIHByZXByb2Nlc3NpbmcuPC9kaXY+PC9kaXY+CiAgICA8L2Rpdj4KICA8L2Rpdj4KPC9zZWN0aW9uPgoKPGRpdiBjbGFzcz0iaHIiPjwvZGl2PgoKPCEtLSBET0NTIC0tPgo8c2VjdGlvbiBjbGFzcz0ic2VjdGlvbiIgaWQ9ImRvY3MiPgogIDxkaXYgY2xhc3M9InNlY3Rpb24td3JhcCI+CiAgICA8ZGl2IGNsYXNzPSJzLWxhYmVsIj4vLyBEb2t1bWVudGFzaTwvZGl2PgogICAgPGgyIGNsYXNzPSJzLXRpdGxlIHJldmVhbCI+U2VtdWEgRW5kcG9pbnQ8L2gyPgogICAgPHAgY2xhc3M9InMtZGVzYyByZXZlYWwiPkJhc2UgVVJMOiA8Y29kZSBzdHlsZT0iZm9udC1mYW1pbHk6dmFyKC0tbW9ubyk7Y29sb3I6dmFyKC0tZ3JlZW4pO2ZvbnQtc2l6ZToxM3B4O2JhY2tncm91bmQ6dmFyKC0tY2FyZCk7cGFkZGluZzoycHggOHB4O2JvcmRlci1yYWRpdXM6NXB4Ij5odHRwczovL2FwaWt5c2hpcm8udmVyY2VsLmFwcDwvY29kZT48L3A+CgogICAgPGRpdiBjbGFzcz0iZXAtbGlzdCByZXZlYWwiPgoKICAgICAgPCEtLSAvc21zIC0tPgogICAgICA8ZGl2IGNsYXNzPSJlcCI+CiAgICAgICAgPGRpdiBjbGFzcz0iZXAtaGVhZCIgb25jbGljaz0idG9nZ2xlRXAodGhpcykiPgogICAgICAgICAgPHNwYW4gY2xhc3M9ImVwLW1ldGhvZCBHRVQiPkdFVDwvc3Bhbj4KICAgICAgICAgIDxzcGFuIGNsYXNzPSJlcC1wYXRoIj4vc21zPC9zcGFuPgogICAgICAgICAgPHNwYW4gY2xhc3M9ImVwLXNob3J0Ij5BbWJpbCBPVFAgYmVyZGFzYXJrYW4gdGFuZ2dhbDwvc3Bhbj4KICAgICAgICAgIDxzcGFuIGNsYXNzPSJlcC1hcnJvdyI+4pa+PC9zcGFuPgogICAgICAgIDwvZGl2PgogICAgICAgIDxkaXYgY2xhc3M9ImVwLWJvZHkiPgogICAgICAgICAgPGRpdiBjbGFzcz0icHQiPlBhcmFtZXRlcjwvZGl2PgogICAgICAgICAgPHRhYmxlIGNsYXNzPSJwdGFibGUiPgogICAgICAgICAgICA8dHI+PHRoPk5hbWE8L3RoPjx0aD5UaXBlPC90aD48dGg+U3RhdHVzPC90aD48dGg+S2V0ZXJhbmdhbjwvdGg+PC90cj4KICAgICAgICAgICAgPHRyPjx0ZD5kYXRlPC90ZD48dGQ+c3RyaW5nPC90ZD48dGQ+PHNwYW4gY2xhc3M9ImJyIGJyLXIiPldBSklCPC9zcGFuPjwvdGQ+PHRkPkZvcm1hdCBERC9NTS9ZWVlZIOKAlCB0YW5nZ2FsIHlhbmcgZGljZWs8L3RkPjwvdHI+CiAgICAgICAgICAgIDx0cj48dGQ+bW9kZTwvdGQ+PHRkPnN0cmluZzwvdGQ+PHRkPjxzcGFuIGNsYXNzPSJiciBici1vIj5PUFNJT05BTDwvc3Bhbj48L3RkPjx0ZD48Y29kZT5yZWNlaXZlZDwvY29kZT4gLyA8Y29kZT5saXZlPC9jb2RlPiAvIDxjb2RlPmJvdGg8L2NvZGU+IOKAlCBkZWZhdWx0OiByZWNlaXZlZDwvdGQ+PC90cj4KICAgICAgICAgIDwvdGFibGU+CiAgICAgICAgICA8ZGl2IGNsYXNzPSJwdCI+Q29udG9oIFJlcXVlc3Q8L2Rpdj4KICAgICAgICAgIDxkaXYgY2xhc3M9ImNvZGUiIHN0eWxlPSJwb3NpdGlvbjpyZWxhdGl2ZSI+PGJ1dHRvbiBjbGFzcz0iY3AtYnRuIiBvbmNsaWNrPSJjcCh0aGlzKSI+Y29weTwvYnV0dG9uPkdFVCAvc21zPzxzcGFuIGNsYXNzPSJreSI+ZGF0ZTwvc3Bhbj49PHNwYW4gY2xhc3M9InMiPjA3LzAzLzIwMjY8L3NwYW4+JjxzcGFuIGNsYXNzPSJreSI+bW9kZTwvc3Bhbj49PHNwYW4gY2xhc3M9InMiPnJlY2VpdmVkPC9zcGFuPjwvZGl2PgogICAgICAgICAgPGRpdiBjbGFzcz0icHQiPkNvbnRvaCBSZXNwb25zZTwvZGl2PgogICAgICAgICAgPGRpdiBjbGFzcz0iY29kZSI+ewogIDxzcGFuIGNsYXNzPSJreSI+InN0YXR1cyI8L3NwYW4+OiA8c3BhbiBjbGFzcz0icyI+InN1Y2Nlc3MiPC9zcGFuPiwKICA8c3BhbiBjbGFzcz0ia3kiPiJtb2RlIjwvc3Bhbj46IDxzcGFuIGNsYXNzPSJzIj4icmVjZWl2ZWQiPC9zcGFuPiwKICA8c3BhbiBjbGFzcz0ia3kiPiJ0b3RhbCI8L3NwYW4+OiA8c3BhbiBjbGFzcz0idiI+NTwvc3Bhbj4sCiAgPHNwYW4gY2xhc3M9Imt5Ij4iYWNjb3VudHNfdXNlZCI8L3NwYW4+OiA8c3BhbiBjbGFzcz0idiI+Mjwvc3Bhbj4sCiAgPHNwYW4gY2xhc3M9Imt5Ij4ib3RwX21lc3NhZ2VzIjwvc3Bhbj46IFsKICAgIHsKICAgICAgPHNwYW4gY2xhc3M9Imt5Ij4icmFuZ2UiPC9zcGFuPjogPHNwYW4gY2xhc3M9InMiPiJJVk9SWSBDT0FTVCAzODc4Ijwvc3Bhbj4sCiAgICAgIDxzcGFuIGNsYXNzPSJreSI+InBob25lX251bWJlciI8L3NwYW4+OiA8c3BhbiBjbGFzcz0icyI+IjIyNTA3MTEyMjA5NzAiPC9zcGFuPiwKICAgICAgPHNwYW4gY2xhc3M9Imt5Ij4ib3RwX21lc3NhZ2UiPC9zcGFuPjogPHNwYW4gY2xhc3M9InMiPiJZb3VyIFdoYXRzQXBwIGNvZGU6IDMzOC02NDAiPC9zcGFuPiwKICAgICAgPHNwYW4gY2xhc3M9Imt5Ij4ic291cmNlIjwvc3Bhbj46IDxzcGFuIGNsYXNzPSJzIj4icmVjZWl2ZWQiPC9zcGFuPiwKICAgICAgPHNwYW4gY2xhc3M9Imt5Ij4iYWNjb3VudCI8L3NwYW4+OiA8c3BhbiBjbGFzcz0icyI+IjxhIGhyZWY9Ii9jZG4tY2dpL2wvZW1haWwtcHJvdGVjdGlvbiIgY2xhc3M9Il9fY2ZfZW1haWxfXyIgZGF0YS1jZmVtYWlsPSI1YjNhMzAyZTM1NmExYjNjMzYzYTMyMzc3NTM4MzQzNiI+W2VtYWlsJiMxNjA7cHJvdGVjdGVkXTwvYT4iPC9zcGFuPgogICAgfQogIF0KfTwvZGl2PgogICAgICAgIDwvZGl2PgogICAgICA8L2Rpdj4KCiAgICAgIDwhLS0gL2hlYWx0aCAtLT4KICAgICAgPGRpdiBjbGFzcz0iZXAiPgogICAgICAgIDxkaXYgY2xhc3M9ImVwLWhlYWQiIG9uY2xpY2s9InRvZ2dsZUVwKHRoaXMpIj4KICAgICAgICAgIDxzcGFuIGNsYXNzPSJlcC1tZXRob2QgR0VUIj5HRVQ8L3NwYW4+CiAgICAgICAgICA8c3BhbiBjbGFzcz0iZXAtcGF0aCI+L2hlYWx0aDwvc3Bhbj4KICAgICAgICAgIDxzcGFuIGNsYXNzPSJlcC1zaG9ydCI+Q2VrIHN0YXR1cyBsb2dpbiBzZW11YSBha3VuPC9zcGFuPgogICAgICAgICAgPHNwYW4gY2xhc3M9ImVwLWFycm93Ij7ilr48L3NwYW4+CiAgICAgICAgPC9kaXY+CiAgICAgICAgPGRpdiBjbGFzcz0iZXAtYm9keSI+CiAgICAgICAgICA8cCBzdHlsZT0iY29sb3I6dmFyKC0taW5rMik7Zm9udC1zaXplOjE0cHg7bWFyZ2luLXRvcDoxNHB4Ij5DZWsgYXBha2FoIEFQSSBiZXJoYXNpbCBsb2dpbiBrZSBpVkFTLiBLYWxhdSA8Y29kZSBzdHlsZT0iY29sb3I6dmFyKC0tZ3JlZW4pIj5sb2dpbjogInN1Y2Nlc3MiPC9jb2RlPiBiZXJhcnRpIHNpYXAgdGVyaW1hIHJlcXVlc3QuPC9wPgogICAgICAgICAgPGRpdiBjbGFzcz0icHQiPkNvbnRvaCBSZXNwb25zZTwvZGl2PgogICAgICAgICAgPGRpdiBjbGFzcz0iY29kZSI+ewogIDxzcGFuIGNsYXNzPSJreSI+InN0YXR1cyI8L3NwYW4+OiA8c3BhbiBjbGFzcz0icyI+Im9rIjwvc3Bhbj4sCiAgPHNwYW4gY2xhc3M9Imt5Ij4ibG9naW4iPC9zcGFuPjogPHNwYW4gY2xhc3M9InMiPiJzdWNjZXNzIjwvc3Bhbj4sCiAgPHNwYW4gY2xhc3M9Imt5Ij4iYWNjb3VudHNfb2siPC9zcGFuPjogPHNwYW4gY2xhc3M9InYiPjI8L3NwYW4+LAogIDxzcGFuIGNsYXNzPSJreSI+ImFjY291bnRzX3RvdGFsIjwvc3Bhbj46IDxzcGFuIGNsYXNzPSJ2Ij4yPC9zcGFuPiwKICA8c3BhbiBjbGFzcz0ia3kiPiJkZXRhaWxzIjwvc3Bhbj46IFsKICAgIHsgPHNwYW4gY2xhc3M9Imt5Ij4iZW1haWwiPC9zcGFuPjogPHNwYW4gY2xhc3M9InMiPiI8YSBocmVmPSIvY2RuLWNnaS9sL2VtYWlsLXByb3RlY3Rpb24iIGNsYXNzPSJfX2NmX2VtYWlsX18iIGRhdGEtY2ZlbWFpbD0iZWU4Zjg1OWI4MGRmYWU4OTgzOGY4NzgyYzA4ZDgxODMiPltlbWFpbCYjMTYwO3Byb3RlY3RlZF08L2E+Ijwvc3Bhbj4sIDxzcGFuIGNsYXNzPSJreSI+ImxvZ2luIjwvc3Bhbj46IDxzcGFuIGNsYXNzPSJzIj4ic3VjY2VzcyI8L3NwYW4+IH0sCiAgICB7IDxzcGFuIGNsYXNzPSJreSI+ImVtYWlsIjwvc3Bhbj46IDxzcGFuIGNsYXNzPSJzIj4iPGEgaHJlZj0iL2Nkbi1jZ2kvbC9lbWFpbC1wcm90ZWN0aW9uIiBjbGFzcz0iX19jZl9lbWFpbF9fIiBkYXRhLWNmZW1haWw9IjA2Njc2ZDczNjgzNDQ2NjE2YjY3NmY2YTI4NjU2OTZiIj5bZW1haWwmIzE2MDtwcm90ZWN0ZWRdPC9hPiI8L3NwYW4+LCA8c3BhbiBjbGFzcz0ia3kiPiJsb2dpbiI8L3NwYW4+OiA8c3BhbiBjbGFzcz0icyI+InN1Y2Nlc3MiPC9zcGFuPiB9CiAgXQp9PC9kaXY+CiAgICAgICAgPC9kaXY+CiAgICAgIDwvZGl2PgoKICAgICAgPCEtLSAvYWNjb3VudHMgLS0+CiAgICAgIDxkaXYgY2xhc3M9ImVwIj4KICAgICAgICA8ZGl2IGNsYXNzPSJlcC1oZWFkIiBvbmNsaWNrPSJ0b2dnbGVFcCh0aGlzKSI+CiAgICAgICAgICA8c3BhbiBjbGFzcz0iZXAtbWV0aG9kIEdFVCI+R0VUPC9zcGFuPgogICAgICAgICAgPHNwYW4gY2xhc3M9ImVwLXBhdGgiPi9hY2NvdW50czwvc3Bhbj4KICAgICAgICAgIDxzcGFuIGNsYXNzPSJlcC1zaG9ydCI+TGlzdCBha3VuIHRlcmRhZnRhcjwvc3Bhbj4KICAgICAgICAgIDxzcGFuIGNsYXNzPSJlcC1hcnJvdyI+4pa+PC9zcGFuPgogICAgICAgIDwvZGl2PgogICAgICAgIDxkaXYgY2xhc3M9ImVwLWJvZHkiPgogICAgICAgICAgPHAgc3R5bGU9ImNvbG9yOnZhcigtLWluazIpO2ZvbnQtc2l6ZToxNHB4O21hcmdpbi10b3A6MTRweCI+TGloYXQgYmVyYXBhIGFrdW4geWFuZyB0ZXJkYWZ0YXIgZGkgQVBJLiBQYXNzd29yZCB0aWRhayBkaXRhbXBpbGthbi48L3A+CiAgICAgICAgICA8ZGl2IGNsYXNzPSJwdCI+Q29udG9oIFJlc3BvbnNlPC9kaXY+CiAgICAgICAgICA8ZGl2IGNsYXNzPSJjb2RlIj57CiAgPHNwYW4gY2xhc3M9Imt5Ij4idG90YWwiPC9zcGFuPjogPHNwYW4gY2xhc3M9InYiPjI8L3NwYW4+LAogIDxzcGFuIGNsYXNzPSJreSI+ImFjY291bnRzIjwvc3Bhbj46IFsKICAgIHsgPHNwYW4gY2xhc3M9Imt5Ij4iaW5kZXgiPC9zcGFuPjogPHNwYW4gY2xhc3M9InYiPjE8L3NwYW4+LCA8c3BhbiBjbGFzcz0ia3kiPiJlbWFpbCI8L3NwYW4+OiA8c3BhbiBjbGFzcz0icyI+IjxhIGhyZWY9Ii9jZG4tY2dpL2wvZW1haWwtcHJvdGVjdGlvbiIgY2xhc3M9Il9fY2ZfZW1haWxfXyIgZGF0YS1jZmVtYWlsPSJiZGRjZDZjOGQzOGNmZGRhZDBkY2Q0ZDE5M2RlZDJkMCI+W2VtYWlsJiMxNjA7cHJvdGVjdGVkXTwvYT4iPC9zcGFuPiB9LAogICAgeyA8c3BhbiBjbGFzcz0ia3kiPiJpbmRleCI8L3NwYW4+OiA8c3BhbiBjbGFzcz0idiI+Mjwvc3Bhbj4sIDxzcGFuIGNsYXNzPSJreSI+ImVtYWlsIjwvc3Bhbj46IDxzcGFuIGNsYXNzPSJzIj4iPGEgaHJlZj0iL2Nkbi1jZ2kvbC9lbWFpbC1wcm90ZWN0aW9uIiBjbGFzcz0iX19jZl9lbWFpbF9fIiBkYXRhLWNmZW1haWw9IjlhZmJmMWVmZjRhOGRhZmRmN2ZiZjNmNmI0ZjlmNWY3Ij5bZW1haWwmIzE2MDtwcm90ZWN0ZWRdPC9hPiI8L3NwYW4+IH0KICBdCn08L2Rpdj4KICAgICAgICA8L2Rpdj4KICAgICAgPC9kaXY+CgogICAgICA8IS0tIC90ZXN0IC0tPgogICAgICA8ZGl2IGNsYXNzPSJlcCI+CiAgICAgICAgPGRpdiBjbGFzcz0iZXAtaGVhZCIgb25jbGljaz0idG9nZ2xlRXAodGhpcykiPgogICAgICAgICAgPHNwYW4gY2xhc3M9ImVwLW1ldGhvZCBHRVQiPkdFVDwvc3Bhbj4KICAgICAgICAgIDxzcGFuIGNsYXNzPSJlcC1wYXRoIj4vdGVzdDwvc3Bhbj4KICAgICAgICAgIDxzcGFuIGNsYXNzPSJlcC1zaG9ydCI+Q2VrIHNlbXVhIHJhbmdlICYgbm9tb3I8L3NwYW4+CiAgICAgICAgICA8c3BhbiBjbGFzcz0iZXAtYXJyb3ciPuKWvjwvc3Bhbj4KICAgICAgICA8L2Rpdj4KICAgICAgICA8ZGl2IGNsYXNzPSJlcC1ib2R5Ij4KICAgICAgICAgIDxkaXYgY2xhc3M9InB0Ij5QYXJhbWV0ZXI8L2Rpdj4KICAgICAgICAgIDx0YWJsZSBjbGFzcz0icHRhYmxlIj4KICAgICAgICAgICAgPHRyPjx0aD5OYW1hPC90aD48dGg+VGlwZTwvdGg+PHRoPlN0YXR1czwvdGg+PHRoPktldGVyYW5nYW48L3RoPjwvdHI+CiAgICAgICAgICAgIDx0cj48dGQ+ZGF0ZTwvdGQ+PHRkPnN0cmluZzwvdGQ+PHRkPjxzcGFuIGNsYXNzPSJiciBici1vIj5PUFNJT05BTDwvc3Bhbj48L3RkPjx0ZD5Gb3JtYXQgREQvTU0vWVlZWSDigJQgZGVmYXVsdDogaGFyaSBpbmk8L3RkPjwvdHI+CiAgICAgICAgICA8L3RhYmxlPgogICAgICAgICAgPGRpdiBjbGFzcz0icHQiPkNvbnRvaCBSZXF1ZXN0PC9kaXY+CiAgICAgICAgICA8ZGl2IGNsYXNzPSJjb2RlIiBzdHlsZT0icG9zaXRpb246cmVsYXRpdmUiPjxidXR0b24gY2xhc3M9ImNwLWJ0biIgb25jbGljaz0iY3AodGhpcykiPmNvcHk8L2J1dHRvbj5HRVQgL3Rlc3Q/PHNwYW4gY2xhc3M9Imt5Ij5kYXRlPC9zcGFuPj08c3BhbiBjbGFzcz0icyI+MDcvMDMvMjAyNjwvc3Bhbj48L2Rpdj4KICAgICAgICA8L2Rpdj4KICAgICAgPC9kaXY+CgogICAgICA8IS0tIC90ZXN0L3NtcyAtLT4KICAgICAgPGRpdiBjbGFzcz0iZXAiPgogICAgICAgIDxkaXYgY2xhc3M9ImVwLWhlYWQiIG9uY2xpY2s9InRvZ2dsZUVwKHRoaXMpIj4KICAgICAgICAgIDxzcGFuIGNsYXNzPSJlcC1tZXRob2QgR0VUIj5HRVQ8L3NwYW4+CiAgICAgICAgICA8c3BhbiBjbGFzcz0iZXAtcGF0aCI+L3Rlc3Qvc21zPC9zcGFuPgogICAgICAgICAgPHNwYW4gY2xhc3M9ImVwLXNob3J0Ij5DZWsgT1RQIHVudHVrIDEgbm9tb3Igc3Blc2lmaWs8L3NwYW4+CiAgICAgICAgICA8c3BhbiBjbGFzcz0iZXAtYXJyb3ciPuKWvjwvc3Bhbj4KICAgICAgICA8L2Rpdj4KICAgICAgICA8ZGl2IGNsYXNzPSJlcC1ib2R5Ij4KICAgICAgICAgIDxkaXYgY2xhc3M9InB0Ij5QYXJhbWV0ZXI8L2Rpdj4KICAgICAgICAgIDx0YWJsZSBjbGFzcz0icHRhYmxlIj4KICAgICAgICAgICAgPHRyPjx0aD5OYW1hPC90aD48dGg+VGlwZTwvdGg+PHRoPlN0YXR1czwvdGg+PHRoPktldGVyYW5nYW48L3RoPjwvdHI+CiAgICAgICAgICAgIDx0cj48dGQ+ZGF0ZTwvdGQ+PHRkPnN0cmluZzwvdGQ+PHRkPjxzcGFuIGNsYXNzPSJiciBici1vIj5PUFNJT05BTDwvc3Bhbj48L3RkPjx0ZD5Gb3JtYXQgREQvTU0vWVlZWTwvdGQ+PC90cj4KICAgICAgICAgICAgPHRyPjx0ZD5yYW5nZTwvdGQ+PHRkPnN0cmluZzwvdGQ+PHRkPjxzcGFuIGNsYXNzPSJiciBici1yIj5XQUpJQjwvc3Bhbj48L3RkPjx0ZD5OYW1hIHJhbmdlLCBjb250b2g6IElWT1JZIENPQVNUIDM4Nzg8L3RkPjwvdHI+CiAgICAgICAgICAgIDx0cj48dGQ+bnVtYmVyPC90ZD48dGQ+c3RyaW5nPC90ZD48dGQ+PHNwYW4gY2xhc3M9ImJyIGJyLXIiPldBSklCPC9zcGFuPjwvdGQ+PHRkPk5vbW9yIHRlbGVwb24sIGNvbnRvaDogMjI1MDcxMTIyMDk3MDwvdGQ+PC90cj4KICAgICAgICAgIDwvdGFibGU+CiAgICAgICAgICA8ZGl2IGNsYXNzPSJwdCI+Q29udG9oIFJlcXVlc3Q8L2Rpdj4KICAgICAgICAgIDxkaXYgY2xhc3M9ImNvZGUiIHN0eWxlPSJwb3NpdGlvbjpyZWxhdGl2ZSI+PGJ1dHRvbiBjbGFzcz0iY3AtYnRuIiBvbmNsaWNrPSJjcCh0aGlzKSI+Y29weTwvYnV0dG9uPkdFVCAvdGVzdC9zbXM/PHNwYW4gY2xhc3M9Imt5Ij5kYXRlPC9zcGFuPj08c3BhbiBjbGFzcz0icyI+MDcvMDMvMjAyNjwvc3Bhbj4mPHNwYW4gY2xhc3M9Imt5Ij5yYW5nZTwvc3Bhbj49PHNwYW4gY2xhc3M9InMiPklWT1JZIENPQVNUIDM4Nzg8L3NwYW4+JjxzcGFuIGNsYXNzPSJreSI+bnVtYmVyPC9zcGFuPj08c3BhbiBjbGFzcz0icyI+MjI1MDcxMTIyMDk3MDwvc3Bhbj48L2Rpdj4KICAgICAgICA8L2Rpdj4KICAgICAgPC9kaXY+CgogICAgICA8IS0tIC9kZWJ1ZyBlbmRwb2ludHMgLS0+CiAgICAgIDxkaXYgY2xhc3M9ImVwIj4KICAgICAgICA8ZGl2IGNsYXNzPSJlcC1oZWFkIiBvbmNsaWNrPSJ0b2dnbGVFcCh0aGlzKSI+CiAgICAgICAgICA8c3BhbiBjbGFzcz0iZXAtbWV0aG9kIEdFVCI+R0VUPC9zcGFuPgogICAgICAgICAgPHNwYW4gY2xhc3M9ImVwLXBhdGgiPi9kZWJ1Zy9yYW5nZXMtcmF3ICZuYnNwOyAvZGVidWcvbnVtYmVycyAmbmJzcDsgL2RlYnVnL3Ntczwvc3Bhbj4KICAgICAgICAgIDxzcGFuIGNsYXNzPSJlcC1zaG9ydCI+RGVidWcgZW5kcG9pbnRzPC9zcGFuPgogICAgICAgICAgPHNwYW4gY2xhc3M9ImVwLWFycm93Ij7ilr48L3NwYW4+CiAgICAgICAgPC9kaXY+CiAgICAgICAgPGRpdiBjbGFzcz0iZXAtYm9keSI+CiAgICAgICAgICA8cCBzdHlsZT0iY29sb3I6dmFyKC0taW5rMik7Zm9udC1zaXplOjE0cHg7bWFyZ2luLXRvcDoxNHB4Ij5UaWdhIGVuZHBvaW50IGtodXN1cyBidWF0IGRlYnVnIGthbGF1IGFkYSB5YW5nIHRpZGFrIGtlZGV0ZWtzaSBhdGF1IFNNUyB0aWRhayBtYXN1ay48L3A+CiAgICAgICAgICA8ZGl2IGNsYXNzPSJwdCI+RW5kcG9pbnQgRGVidWc8L2Rpdj4KICAgICAgICAgIDx0YWJsZSBjbGFzcz0icHRhYmxlIj4KICAgICAgICAgICAgPHRyPjx0aD5FbmRwb2ludDwvdGg+PHRoPlBhcmFtZXRlciBXYWppYjwvdGg+PHRoPkZ1bmdzaTwvdGg+PC90cj4KICAgICAgICAgICAgPHRyPjx0ZD4vZGVidWcvcmFuZ2VzLXJhdzwvdGQ+PHRkPmRhdGU8L3RkPjx0ZD5SYXcgSFRNTCBkYXJpIGlWQVMgYnVhdCBjZWsga2VuYXBhIHJhbmdlIHRpZGFrIG11bmN1bDwvdGQ+PC90cj4KICAgICAgICAgICAgPHRyPjx0ZD4vZGVidWcvbnVtYmVyczwvdGQ+PHRkPmRhdGUsIHJhbmdlPC90ZD48dGQ+Q2VrIG5vbW9yIGRhcmkgcmFuZ2UgdGVydGVudHUgYmVzZXJ0YSByYXcgcmVzcG9uc2U8L3RkPjwvdHI+CiAgICAgICAgICAgIDx0cj48dGQ+L2RlYnVnL3NtczwvdGQ+PHRkPmRhdGUsIHJhbmdlLCBudW1iZXI8L3RkPjx0ZD5DZWsgcmF3IHJlc3BvbnNlIFNNUyB1bnR1ayBub21vciB0ZXJ0ZW50dTwvdGQ+PC90cj4KICAgICAgICAgIDwvdGFibGU+CiAgICAgICAgPC9kaXY+CiAgICAgIDwvZGl2PgoKICAgIDwvZGl2PjwhLS0gZW5kIGVwLWxpc3QgLS0+CgogICAgPCEtLSBNdWx0aS1hY2NvdW50IGd1aWRlIC0tPgogICAgPGRpdiBzdHlsZT0ibWFyZ2luLXRvcDozMnB4O2JhY2tncm91bmQ6dmFyKC0tY2FyZCk7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtib3JkZXItcmFkaXVzOnZhcigtLXIpO3BhZGRpbmc6MjRweCIgY2xhc3M9InJldmVhbCI+CiAgICAgIDxkaXYgY2xhc3M9InMtbGFiZWwiIHN0eWxlPSJtYXJnaW4tYm90dG9tOjEwcHgiPi8vIENhcmEgVGFtYmFoIEFrdW48L2Rpdj4KICAgICAgPHAgc3R5bGU9ImNvbG9yOnZhcigtLWluazIpO2ZvbnQtc2l6ZToxNHB4O2xpbmUtaGVpZ2h0OjEuNzttYXJnaW4tYm90dG9tOjE0cHgiPkJ1a2EgPGNvZGUgc3R5bGU9ImZvbnQtZmFtaWx5OnZhcigtLW1vbm8pO2NvbG9yOnZhcigtLWdyZWVuKSI+YXBwLnB5PC9jb2RlPiwgY2FyaSBiYWdpYW4gPGNvZGUgc3R5bGU9ImZvbnQtZmFtaWx5OnZhcigtLW1vbm8pO2NvbG9yOnZhcigtLWdyZWVuKSI+bG9hZF9hY2NvdW50cygpPC9jb2RlPiwgdGFtYmFoIGFrdW4gYmFydSBkaSBsaXN0OjwvcD4KICAgICAgPGRpdiBjbGFzcz0iY29kZSI+cmV0dXJuIFsKICAgIHs8c3BhbiBjbGFzcz0ia3kiPiJlbWFpbCI8L3NwYW4+OiA8c3BhbiBjbGFzcz0icyI+IjxhIGhyZWY9Ii9jZG4tY2dpL2wvZW1haWwtcHJvdGVjdGlvbiIgY2xhc3M9Il9fY2ZfZW1haWxfXyIgZGF0YS1jZmVtYWlsPSJlNDg1OGY5MThhZDVhNDgzODk4NThkODhjYTg3OGI4OSI+W2VtYWlsJiMxNjA7cHJvdGVjdGVkXTwvYT4iPC9zcGFuPiwgPHNwYW4gY2xhc3M9Imt5Ij4icGFzc3dvcmQiPC9zcGFuPjogPHNwYW4gY2xhc3M9InMiPiJwYXNzd29yZDEiPC9zcGFuPn0sCiAgICB7PHNwYW4gY2xhc3M9Imt5Ij4iZW1haWwiPC9zcGFuPjogPHNwYW4gY2xhc3M9InMiPiI8YSBocmVmPSIvY2RuLWNnaS9sL2VtYWlsLXByb3RlY3Rpb24iIGNsYXNzPSJfX2NmX2VtYWlsX18iIGRhdGEtY2ZlbWFpbD0iMzE1MDVhNDQ1ZjAzNzE1NjVjNTA1ODVkMWY1MjVlNWMiPltlbWFpbCYjMTYwO3Byb3RlY3RlZF08L2E+Ijwvc3Bhbj4sIDxzcGFuIGNsYXNzPSJreSI+InBhc3N3b3JkIjwvc3Bhbj46IDxzcGFuIGNsYXNzPSJzIj4icGFzc3dvcmQyIjwvc3Bhbj59LCAgPHNwYW4gY2xhc3M9ImMiPiMg4oaQIHRhbWJhaCBkaSBzaW5pPC9zcGFuPgogICAgezxzcGFuIGNsYXNzPSJreSI+ImVtYWlsIjwvc3Bhbj46IDxzcGFuIGNsYXNzPSJzIj4iPGEgaHJlZj0iL2Nkbi1jZ2kvbC9lbWFpbC1wcm90ZWN0aW9uIiBjbGFzcz0iX19jZl9lbWFpbF9fIiBkYXRhLWNmZW1haWw9IjJmNGU0NDVhNDExYzZmNDg0MjRlNDY0MzAxNGM0MDQyIj5bZW1haWwmIzE2MDtwcm90ZWN0ZWRdPC9hPiI8L3NwYW4+LCA8c3BhbiBjbGFzcz0ia3kiPiJwYXNzd29yZCI8L3NwYW4+OiA8c3BhbiBjbGFzcz0icyI+InBhc3N3b3JkMyI8L3NwYW4+fSwgIDxzcGFuIGNsYXNzPSJjIj4jIOKGkCBhdGF1IGRpIHNpbmk8L3NwYW4+Cl08L2Rpdj4KICAgICAgPHAgc3R5bGU9ImNvbG9yOnZhcigtLWluazIpO2ZvbnQtc2l6ZToxM3B4O21hcmdpbi10b3A6MTJweCI+QXRhdSBwYWthaSBlbnZpcm9ubWVudCB2YXJpYWJsZSBkaSBWZXJjZWw6IDxjb2RlIHN0eWxlPSJmb250LWZhbWlseTp2YXIoLS1tb25vKTtjb2xvcjp2YXIoLS15ZWxsb3cpIj5JVkFTX0FDQ09VTlRTID0gZW1haWwxOnBhc3MxLGVtYWlsMjpwYXNzMjwvY29kZT48L3A+CiAgICA8L2Rpdj4KCiAgPC9kaXY+Cjwvc2VjdGlvbj4KCjxkaXYgY2xhc3M9ImhyIj48L2Rpdj4KCjwhLS0gQ09OVEFDVCAtLT4KPHNlY3Rpb24gY2xhc3M9InNlY3Rpb24iIGlkPSJjb250YWN0Ij4KICA8ZGl2IGNsYXNzPSJzZWN0aW9uLXdyYXAiPgogICAgPGRpdiBjbGFzcz0icy1sYWJlbCI+Ly8gSHVidW5naSBLYW1pPC9kaXY+CiAgICA8aDIgY2xhc3M9InMtdGl0bGUgcmV2ZWFsIj5BZGEgeWFuZyBtYXUgZGl0YW55YT88L2gyPgogICAgPHAgY2xhc3M9InMtZGVzYyByZXZlYWwiPkJ1ZywgcmVxdWVzdCBmaXR1ciwgYXRhdSBzZWtlZGFyIG1hdSBrZW5hbGFuIOKAlCBsYW5nc3VuZyBhamEga29udGFrIGRldmVsb3Blcm55YS48L3A+CiAgICA8ZGl2IGNsYXNzPSJjb250YWN0LWdyaWQgcmV2ZWFsIj4KICAgICAgPGEgaHJlZj0iaHR0cHM6Ly90Lm1lL3VzZXJuYW1lX2tpa2kiIHRhcmdldD0iX2JsYW5rIiBjbGFzcz0iY2MiPgogICAgICAgIDxkaXYgY2xhc3M9ImNjLWljb24gYmctdGciPuKciO+4jzwvZGl2PgogICAgICAgIDxkaXY+PGRpdiBjbGFzcz0iY2MtdCI+VGVsZWdyYW08L2Rpdj48ZGl2IGNsYXNzPSJjYy1zIj5AS2lraUZhaXphbDwvZGl2PjwvZGl2PgogICAgICA8L2E+CiAgICAgIDxhIGhyZWY9Imh0dHBzOi8vd2EubWUvNjJ4eHh4eHh4eCIgdGFyZ2V0PSJfYmxhbmsiIGNsYXNzPSJjYyI+CiAgICAgICAgPGRpdiBjbGFzcz0iY2MtaWNvbiBiZy13YSI+8J+SrDwvZGl2PgogICAgICAgIDxkaXY+PGRpdiBjbGFzcz0iY2MtdCI+V2hhdHNBcHA8L2Rpdj48ZGl2IGNsYXNzPSJjYy1zIj5DaGF0IHZpYSBXQTwvZGl2PjwvZGl2PgogICAgICA8L2E+CiAgICAgIDxkaXYgY2xhc3M9ImNjIiBvbmNsaWNrPSJvcGVuTW9kYWwoJ2Rldk1vZGFsJykiIHN0eWxlPSJjdXJzb3I6cG9pbnRlciI+CiAgICAgICAgPGRpdiBjbGFzcz0iY2MtaWNvbiBiZy1kZXYiPvCfkaQ8L2Rpdj4KICAgICAgICA8ZGl2PjxkaXYgY2xhc3M9ImNjLXQiPkRldmVsb3BlcjwvZGl2PjxkaXYgY2xhc3M9ImNjLXMiPktpa2kgRmFpemFsPC9kaXY+PC9kaXY+CiAgICAgIDwvZGl2PgogICAgPC9kaXY+CiAgPC9kaXY+Cjwvc2VjdGlvbj4KCjwhLS0gRk9PVEVSIC0tPgo8Zm9vdGVyPgogIDxkaXYgY2xhc3M9ImZvb3QtbmFtZSI+S1ktPGI+U0hJUk88L2I+IE9GRklDSUFMPC9kaXY+CiAgPGRpdiBjbGFzcz0iZm9vdC1zdWIiPgogICAgTWFkZSBieSA8YSBocmVmPSIjIj5LaWtpIEZhaXphbDwvYT4gJm5ic3A7wrcmbmJzcDsKICAgIFBvd2VyZWQgYnkgPGEgaHJlZj0iaHR0cHM6Ly93d3cuaXZhc21zLmNvbSIgdGFyZ2V0PSJfYmxhbmsiPmlWQVMgU01TPC9hPiAmbmJzcDvCtyZuYnNwOwogICAgSG9zdGVkIG9uIDxhIGhyZWY9Imh0dHBzOi8vdmVyY2VsLmNvbSIgdGFyZ2V0PSJfYmxhbmsiPlZlcmNlbDwvYT4KICA8L2Rpdj4KPC9mb290ZXI+Cgo8IS0tIE1PREFMIERFVkVMT1BFUiAtLT4KPGRpdiBjbGFzcz0ib3ZlcmxheSIgaWQ9ImRldk1vZGFsIiBvbmNsaWNrPSJpZihldmVudC50YXJnZXQ9PT10aGlzKWNsb3NlTW9kYWwoJ2Rldk1vZGFsJykiPgogIDxkaXYgY2xhc3M9Im1vZGFsIj4KICAgIDxidXR0b24gY2xhc3M9Im1vZGFsLXgiIG9uY2xpY2s9ImNsb3NlTW9kYWwoJ2Rldk1vZGFsJykiPuKclTwvYnV0dG9uPgogICAgPGRpdiBjbGFzcz0ibW9kYWwtdCI+RGV2ZWxvcGVyPC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJtb2RhbC1kIj5PcmFuZyBkaSBiYWxpayBLWS1TSElSTyBBUEkuIEthbGF1IGFkYSBtYXNhbGFoIGxhbmdzdW5nIHRlbWJhayBhamEuPC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJkZXYtY2FyZCI+CiAgICAgIDxkaXYgY2xhc3M9ImRldi1hdiI+S0Y8L2Rpdj4KICAgICAgPGRpdj4KICAgICAgICA8ZGl2IGNsYXNzPSJkZXYtbiI+S2lraSBGYWl6YWw8L2Rpdj4KICAgICAgICA8ZGl2IGNsYXNzPSJkZXYtciI+Ly8gQmFja2VuZCDCtyBBUEkgRW5naW5lZXI8L2Rpdj4KICAgICAgPC9kaXY+CiAgICA8L2Rpdj4KICA8L2Rpdj4KPC9kaXY+Cgo8IS0tIE1PREFMIFNUQVRVUyAtLT4KPGRpdiBjbGFzcz0ib3ZlcmxheSIgaWQ9InN0YXR1c01vZGFsIiBvbmNsaWNrPSJpZihldmVudC50YXJnZXQ9PT10aGlzKWNsb3NlTW9kYWwoJ3N0YXR1c01vZGFsJykiPgogIDxkaXYgY2xhc3M9Im1vZGFsIj4KICAgIDxidXR0b24gY2xhc3M9Im1vZGFsLXgiIG9uY2xpY2s9ImNsb3NlTW9kYWwoJ3N0YXR1c01vZGFsJykiPuKclTwvYnV0dG9uPgogICAgPGRpdiBjbGFzcz0ibW9kYWwtdCI+U3RhdHVzIExpdmU8L2Rpdj4KICAgIDxkaXYgaWQ9InN0YXR1c01vZGFsQm9keSIgc3R5bGU9ImNvbG9yOnZhcigtLWluazIpO2ZvbnQtc2l6ZToxNHB4Ij5NZW5nZWNlay4uLjwvZGl2PgogIDwvZGl2Pgo8L2Rpdj4KCjxzY3JpcHQgZGF0YS1jZmFzeW5jPSJmYWxzZSIgc3JjPSIvY2RuLWNnaS9zY3JpcHRzLzVjNWRkNzI4L2Nsb3VkZmxhcmUtc3RhdGljL2VtYWlsLWRlY29kZS5taW4uanMiPjwvc2NyaXB0PjxzY3JpcHQ+CmNvbnN0IEFQSSA9ICdodHRwczovL2FwaWt5c2hpcm8udmVyY2VsLmFwcCc7CgovLyDilIDilIAgTUVOVSBET1Qg4pSA4pSACmZ1bmN0aW9uIHRvZ2dsZURvdChlKXsKICBlLnN0b3BQcm9wYWdhdGlvbigpOwogIGNvbnN0IG0gPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnZG90TWVudScpOwogIG0uY2xhc3NMaXN0LnRvZ2dsZSgnc2hvdycpOwp9CmRvY3VtZW50LmFkZEV2ZW50TGlzdGVuZXIoJ2NsaWNrJywgZnVuY3Rpb24oKXsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnZG90TWVudScpLmNsYXNzTGlzdC5yZW1vdmUoJ3Nob3cnKTsKfSk7CgovLyDilIDilIAgTU9EQUwg4pSA4pSACmZ1bmN0aW9uIG9wZW5Nb2RhbChpZCl7CiAgY29uc3QgZWwgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZChpZCk7CiAgaWYoZWwpIGVsLmNsYXNzTGlzdC5hZGQoJ3Nob3cnKTsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnZG90TWVudScpLmNsYXNzTGlzdC5yZW1vdmUoJ3Nob3cnKTsKfQpmdW5jdGlvbiBjbG9zZU1vZGFsKGlkKXsKICBjb25zdCBlbCA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKGlkKTsKICBpZihlbCkgZWwuY2xhc3NMaXN0LnJlbW92ZSgnc2hvdycpOwp9CgovLyDilIDilIAgRU5EUE9JTlQgVE9HR0xFIOKUgOKUgApmdW5jdGlvbiB0b2dnbGVFcChoZWFkKXsKICBjb25zdCBib2R5ID0gaGVhZC5uZXh0RWxlbWVudFNpYmxpbmc7CiAgY29uc3QgYXJyICA9IGhlYWQucXVlcnlTZWxlY3RvcignLmVwLWFycm93Jyk7CiAgaWYoIWJvZHkgfHwgIWFycikgcmV0dXJuOwogIGJvZHkuY2xhc3NMaXN0LnRvZ2dsZSgnb3BlbicpOwogIGFyci5jbGFzc0xpc3QudG9nZ2xlKCdvcGVuJyk7Cn0KCi8vIOKUgOKUgCBDT1BZIENPREUg4pSA4pSACmZ1bmN0aW9uIGNwKGJ0bil7CiAgY29uc3QgYmxvY2sgPSBidG4uY2xvc2VzdCgnLmNvZGUnKTsKICBjb25zdCB0ZXh0ICA9IGJsb2NrLmlubmVyVGV4dC5yZXBsYWNlKC9eY29weVxuLywnJykucmVwbGFjZSgvXuKck1xuLywnJykudHJpbSgpOwogIG5hdmlnYXRvci5jbGlwYm9hcmQud3JpdGVUZXh0KHRleHQpLnRoZW4oZnVuY3Rpb24oKXsKICAgIGJ0bi50ZXh0Q29udGVudCA9ICfinJMnOwogICAgYnRuLnN0eWxlLmNvbG9yID0gJ3ZhcigtLWdyZWVuKSc7CiAgICBzZXRUaW1lb3V0KGZ1bmN0aW9uKCl7IGJ0bi50ZXh0Q29udGVudCA9ICdjb3B5JzsgYnRuLnN0eWxlLmNvbG9yID0gJyc7IH0sIDIwMDApOwogIH0pLmNhdGNoKGZ1bmN0aW9uKCl7fSk7Cn0KCi8vIOKUgOKUgCBUT0RBWSBTVFJJTkcg4pSA4pSACmZ1bmN0aW9uIHRvZGF5U3RyKCl7CiAgY29uc3QgZCA9IG5ldyBEYXRlKCk7CiAgcmV0dXJuIFN0cmluZyhkLmdldERhdGUoKSkucGFkU3RhcnQoMiwnMCcpICsgJy8nICsgU3RyaW5nKGQuZ2V0TW9udGgoKSsxKS5wYWRTdGFydCgyLCcwJykgKyAnLycgKyBkLmdldEZ1bGxZZWFyKCk7Cn0KCi8vIOKUgOKUgCBTVEFUVVMgQ0hFQ0sg4pSA4pSACmFzeW5jIGZ1bmN0aW9uIGNoZWNrU3RhdHVzKG9wZW5Qb3B1cCl7CiAgY29uc3QgZG90ICAgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnc0RvdCcpOwogIGNvbnN0IHR4dCAgID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3NUZXh0Jyk7CiAgY29uc3QgbG9naW4gPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnc0xvZ2luJyk7CiAgY29uc3QgYm9keSAgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnc3RhdHVzTW9kYWxCb2R5Jyk7CgogIC8vIFNldCBjaGVja2luZyBzdGF0ZQogIGlmKGRvdCkgICB7IGRvdC5jbGFzc05hbWUgPSAnc2ItZG90IGNoZWNraW5nJzsgfQogIGlmKHR4dCkgICB0eHQudGV4dENvbnRlbnQgPSAnTWVuZ2VjZWsuLi4nOwogIGlmKGxvZ2luKSBsb2dpbi50ZXh0Q29udGVudCA9ICcuLi4nOwogIGlmKGJvZHkpICBib2R5LmlubmVySFRNTCA9ICc8c3BhbiBzdHlsZT0iY29sb3I6dmFyKC0taW5rMikiPk1lbmdodWJ1bmdpIHNlcnZlci4uLjwvc3Bhbj4nOwoKICBpZihvcGVuUG9wdXApIG9wZW5Nb2RhbCgnc3RhdHVzTW9kYWwnKTsKCiAgdHJ5IHsKICAgIGNvbnN0IGNvbnRyb2xsZXIgPSBuZXcgQWJvcnRDb250cm9sbGVyKCk7CiAgICBjb25zdCB0aW1lciA9IHNldFRpbWVvdXQoZnVuY3Rpb24oKXsgY29udHJvbGxlci5hYm9ydCgpOyB9LCAxNTAwMCk7CiAgICBjb25zdCByZXMgID0gYXdhaXQgZmV0Y2goQVBJICsgJy9oZWFsdGgnLCB7IHNpZ25hbDogY29udHJvbGxlci5zaWduYWwgfSk7CiAgICBjbGVhclRpbWVvdXQodGltZXIpOwogICAgY29uc3QgZGF0YSA9IGF3YWl0IHJlcy5qc29uKCk7CiAgICBjb25zdCBvayAgID0gZGF0YS5sb2dpbiA9PT0gJ3N1Y2Nlc3MnIHx8IGRhdGEuc3RhdHVzID09PSAnb2snOwoKICAgIGlmKG9rKXsKICAgICAgaWYoZG90KSAgIGRvdC5jbGFzc05hbWUgPSAnc2ItZG90IG9ubGluZSc7CiAgICAgIGlmKHR4dCkgICB0eHQudGV4dENvbnRlbnQgPSAnT25saW5lJzsKICAgICAgaWYobG9naW4pIGxvZ2luLnRleHRDb250ZW50ID0gJ+KchSBMb2dpbiBPSyc7CgogICAgICBjb25zdCBhY2NvdW50c09rICAgID0gZGF0YS5hY2NvdW50c19vayB8fCAxOwogICAgICBjb25zdCBhY2NvdW50c1RvdGFsID0gZGF0YS5hY2NvdW50c190b3RhbCB8fCAxOwogICAgICBjb25zdCBkZXRhaWxzICAgICAgID0gKGRhdGEuZGV0YWlscyB8fCBbXSkubWFwKGZ1bmN0aW9uKGQpewogICAgICAgIHJldHVybiAnPGRpdiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6OHB4O3BhZGRpbmc6OHB4IDA7Ym9yZGVyLWJvdHRvbToxcHggc29saWQgdmFyKC0tbGluZSk7Zm9udC1zaXplOjEzcHgiPicKICAgICAgICAgICsgJzxzcGFuIHN0eWxlPSJ3aWR0aDo3cHg7aGVpZ2h0OjdweDtib3JkZXItcmFkaXVzOjUwJTtiYWNrZ3JvdW5kOicgKyAoZC5sb2dpbj09PSdzdWNjZXNzJz8ndmFyKC0tZ3JlZW4pJzondmFyKC0tcmVkKScpICsgJztkaXNwbGF5OmlubGluZS1ibG9jaztmbGV4LXNocmluazowIj48L3NwYW4+JwogICAgICAgICAgKyAnPHNwYW4gc3R5bGU9ImZvbnQtZmFtaWx5OnZhcigtLW1vbm8pO2NvbG9yOnZhcigtLWluazIpIj4nICsgZC5lbWFpbCArICc8L3NwYW4+JwogICAgICAgICAgKyAnPHNwYW4gc3R5bGU9Im1hcmdpbi1sZWZ0OmF1dG87Y29sb3I6JyArIChkLmxvZ2luPT09J3N1Y2Nlc3MnPyd2YXIoLS1ncmVlbiknOid2YXIoLS1yZWQpJykgKyAnO2ZvbnQtd2VpZ2h0OjcwMCI+JyArIChkLmxvZ2luPT09J3N1Y2Nlc3MnPydPSyc6J0dBR0FMJykgKyAnPC9zcGFuPicKICAgICAgICAgICsgJzwvZGl2Pic7CiAgICAgIH0pLmpvaW4oJycpOwoKICAgICAgaWYoYm9keSkgYm9keS5pbm5lckhUTUwgPQogICAgICAgICc8ZGl2IHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMHB4O3BhZGRpbmc6MTRweDtiYWNrZ3JvdW5kOnJnYmEoMTg0LDI1NSwxMTAsLjA2KTtib3JkZXI6MXB4IHNvbGlkIHJnYmEoMTg0LDI1NSwxMTAsLjE1KTtib3JkZXItcmFkaXVzOjlweDttYXJnaW4tYm90dG9tOjE0cHgiPicKICAgICAgICArICc8c3BhbiBjbGFzcz0ic2ItZG90IG9ubGluZSIgc3R5bGU9ImZsZXgtc2hyaW5rOjAiPjwvc3Bhbj4nCiAgICAgICAgKyAnPGRpdj48ZGl2IHN0eWxlPSJmb250LXdlaWdodDo3MDA7Y29sb3I6dmFyKC0tZ3JlZW4pIj5BUEkgT25saW5lIOKchTwvZGl2PicKICAgICAgICArICc8ZGl2IHN0eWxlPSJmb250LXNpemU6MTJweDtjb2xvcjp2YXIoLS1pbmsyKTttYXJnaW4tdG9wOjJweCI+JyArIGFjY291bnRzT2sgKyAnLycgKyBhY2NvdW50c1RvdGFsICsgJyBha3VuIGFrdGlmIMK3IGlWQVMgdGVyaHVidW5nPC9kaXY+PC9kaXY+PC9kaXY+JwogICAgICAgICsgJzxkaXY+JyArIGRldGFpbHMgKyAnPC9kaXY+JwogICAgICAgICsgJzxkaXYgc3R5bGU9ImZvbnQtZmFtaWx5OnZhcigtLW1vbm8pO2ZvbnQtc2l6ZToxMXB4O2NvbG9yOnZhcigtLWluazMpO21hcmdpbi10b3A6MTJweCI+RGljZWs6ICcgKyBuZXcgRGF0ZSgpLnRvTG9jYWxlVGltZVN0cmluZygnaWQtSUQnKSArICc8L2Rpdj4nOwoKICAgICAgLy8gVXBkYXRlIHN0YXRzIGZyb20gL3Rlc3QKICAgICAgdHJ5IHsKICAgICAgICBjb25zdCBjMiA9IG5ldyBBYm9ydENvbnRyb2xsZXIoKTsKICAgICAgICBjb25zdCB0MiA9IHNldFRpbWVvdXQoZnVuY3Rpb24oKXsgYzIuYWJvcnQoKTsgfSwgMjAwMDApOwogICAgICAgIGNvbnN0IHRkID0gYXdhaXQgZmV0Y2goQVBJICsgJy90ZXN0P2RhdGU9JyArIHRvZGF5U3RyKCksIHsgc2lnbmFsOiBjMi5zaWduYWwgfSk7CiAgICAgICAgY2xlYXJUaW1lb3V0KHQyKTsKICAgICAgICBjb25zdCBkZCA9IGF3YWl0IHRkLmpzb24oKTsKICAgICAgICBjb25zdCByICA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdzdFJhbmdlcycpOwogICAgICAgIGNvbnN0IG4gID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3N0TnVtYmVycycpOwogICAgICAgIGlmKHIgJiYgZGQudG90YWxfcmFuZ2VzICAhPT0gdW5kZWZpbmVkKSByLnRleHRDb250ZW50ID0gZGQudG90YWxfcmFuZ2VzOwogICAgICAgIGlmKG4gJiYgZGQudG90YWxfbnVtYmVycyAhPT0gdW5kZWZpbmVkKSBuLnRleHRDb250ZW50ID0gZGQudG90YWxfbnVtYmVyczsKICAgICAgfSBjYXRjaChlKSB7fQoKICAgIH0gZWxzZSB7CiAgICAgIHRocm93IG5ldyBFcnJvcignbG9naW4gZ2FnYWwnKTsKICAgIH0KCiAgfSBjYXRjaChlKSB7CiAgICBpZihkb3QpICAgZG90LmNsYXNzTmFtZSA9ICdzYi1kb3Qgb2ZmbGluZSc7CiAgICBpZih0eHQpICAgdHh0LnRleHRDb250ZW50ID0gJ09mZmxpbmUnOwogICAgaWYobG9naW4pIGxvZ2luLnRleHRDb250ZW50ID0gJ+KdjCBHYWdhbCc7CgogICAgaWYoYm9keSkgYm9keS5pbm5lckhUTUwgPQogICAgICAnPGRpdiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweDtwYWRkaW5nOjE0cHg7YmFja2dyb3VuZDpyZ2JhKDI1NSwxMDcsMTA3LC4wNik7Ym9yZGVyOjFweCBzb2xpZCByZ2JhKDI1NSwxMDcsMTA3LC4xNSk7Ym9yZGVyLXJhZGl1czo5cHgiPicKICAgICAgKyAnPHNwYW4gY2xhc3M9InNiLWRvdCBvZmZsaW5lIiBzdHlsZT0iZmxleC1zaHJpbms6MCI+PC9zcGFuPicKICAgICAgKyAnPGRpdj48ZGl2IHN0eWxlPSJmb250LXdlaWdodDo3MDA7Y29sb3I6dmFyKC0tcmVkKSI+QVBJIE9mZmxpbmUg4p2MPC9kaXY+JwogICAgICArICc8ZGl2IHN0eWxlPSJmb250LXNpemU6MTJweDtjb2xvcjp2YXIoLS1pbmsyKTttYXJnaW4tdG9wOjJweCI+R2FnYWwga29uZWsga2Ugc2VydmVyIGF0YXUgaVZBUyBsb2dvdXQ8L2Rpdj48L2Rpdj48L2Rpdj4nCiAgICAgICsgJzxkaXYgc3R5bGU9ImZvbnQtZmFtaWx5OnZhcigtLW1vbm8pO2ZvbnQtc2l6ZToxMXB4O2NvbG9yOnZhcigtLWluazMpO21hcmdpbi10b3A6MTJweCI+RGljZWs6ICcgKyBuZXcgRGF0ZSgpLnRvTG9jYWxlVGltZVN0cmluZygnaWQtSUQnKSArICc8L2Rpdj4nOwogIH0KfQoKLy8g4pSA4pSAIEFVVE8gU1RBVFVTIE9OIExPQUQg4pSA4pSACndpbmRvdy5hZGRFdmVudExpc3RlbmVyKCdsb2FkJywgZnVuY3Rpb24oKXsKICAvLyBDZWsgc3RhdHVzIG90b21hdGlzIHNhYXQgYnVrYQogIHNldFRpbWVvdXQoZnVuY3Rpb24oKXsgY2hlY2tTdGF0dXMoZmFsc2UpOyB9LCA4MDApOwogIC8vIEF1dG8gcmVmcmVzaCBzZXRpYXAgMzAgZGV0aWsKICBzZXRJbnRlcnZhbChmdW5jdGlvbigpeyBjaGVja1N0YXR1cyhmYWxzZSk7IH0sIDMwMDAwKTsKfSk7Cjwvc2NyaXB0PjxzY3JpcHQ+CmNvbnN0IEFQSSA9ICdodHRwczovL2FwaWt5c2hpcm8udmVyY2VsLmFwcCc7CgovLyDilIDilIAgTUVOVSBET1Qg4pSA4pSACmZ1bmN0aW9uIHRvZ2dsZURvdChlKXsKICBlLnN0b3BQcm9wYWdhdGlvbigpOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdkb3RNZW51JykuY2xhc3NMaXN0LnRvZ2dsZSgnc2hvdycpOwp9CmRvY3VtZW50LmFkZEV2ZW50TGlzdGVuZXIoJ2NsaWNrJywoKT0+ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2RvdE1lbnUnKS5jbGFzc0xpc3QucmVtb3ZlKCdzaG93JykpOwoKLy8g4pSA4pSAIE1PREFMIOKUgOKUgApmdW5jdGlvbiBvcGVuTW9kYWwoaWQpe2RvY3VtZW50LmdldEVsZW1lbnRCeUlkKGlkKS5jbGFzc0xpc3QuYWRkKCdzaG93Jyl9CmZ1bmN0aW9uIGNsb3NlTW9kYWwoaWQpe2RvY3VtZW50LmdldEVsZW1lbnRCeUlkKGlkKS5jbGFzc0xpc3QucmVtb3ZlKCdzaG93Jyl9CgovLyDilIDilIAgRU5EUE9JTlQgVE9HR0xFIOKUgOKUgApmdW5jdGlvbiB0b2dnbGVFcChoZWFkKXsKICBjb25zdCBib2R5PWhlYWQubmV4dEVsZW1lbnRTaWJsaW5nLCBhcnI9aGVhZC5xdWVyeVNlbGVjdG9yKCcuZXAtYXJyb3cnKTsKICBib2R5LmNsYXNzTGlzdC50b2dnbGUoJ29wZW4nKTsgYXJyLmNsYXNzTGlzdC50b2dnbGUoJ29wZW4nKTsKfQoKLy8g4pSA4pSAIENPUFkgQ09ERSDilIDilIAKZnVuY3Rpb24gY3AoYnRuKXsKICBjb25zdCBibG9jaz1idG4uY2xvc2VzdCgnLmNvZGUnKTsKICBjb25zdCB0ZXh0PWJsb2NrLmlubmVyVGV4dC5yZXBsYWNlKC9eY29weVxuLywnJykudHJpbSgpOwogIG5hdmlnYXRvci5jbGlwYm9hcmQud3JpdGVUZXh0KHRleHQpLnRoZW4oKCk9PnsKICAgIGJ0bi50ZXh0Q29udGVudD0n4pyTJzsgYnRuLnN0eWxlLmNvbG9yPSd2YXIoLS1ncmVlbiknOwogICAgc2V0VGltZW91dCgoKT0+e2J0bi50ZXh0Q29udGVudD0nY29weSc7YnRuLnN0eWxlLmNvbG9yPScnfSwyMDAwKTsKICB9KTsKfQoKLy8g4pSA4pSAIFNUQVRVUyBDSEVDSyDilIDilIAKYXN5bmMgZnVuY3Rpb24gY2hlY2tTdGF0dXMob3BlblBvcHVwPWZhbHNlKXsKICBjb25zdCBkb3Q9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3NEb3QnKTsKICBjb25zdCB0eHQ9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3NUZXh0Jyk7CiAgY29uc3QgbG9naW49ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3NMb2dpbicpOwogIGNvbnN0IGJvZHk9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3N0YXR1c01vZGFsQm9keScpOwoKICBkb3QuY2xhc3NOYW1lPSdzYi1kb3QgY2hlY2tpbmcnOyB0eHQudGV4dENvbnRlbnQ9J01lbmdlY2VrLi4uJzsgbG9naW4udGV4dENvbnRlbnQ9Jy4uLic7CiAgaWYoYm9keSkgYm9keS5pbm5lckhUTUw9JzxzcGFuIHN0eWxlPSJjb2xvcjp2YXIoLS1pbmsyKSI+TWVuZ2h1YnVuZ2kgc2VydmVyLi4uPC9zcGFuPic7CiAgaWYob3BlblBvcHVwKSBvcGVuTW9kYWwoJ3N0YXR1c01vZGFsJyk7CgogIHRyeSB7CiAgICBjb25zdCByZXM9YXdhaXQgZmV0Y2goQVBJKycvaGVhbHRoJyx7c2lnbmFsOkFib3J0U2lnbmFsLnRpbWVvdXQoMTQwMDApfSk7CiAgICBjb25zdCBkYXRhPWF3YWl0IHJlcy5qc29uKCk7CiAgICBjb25zdCBvaz1kYXRhLmxvZ2luPT09J3N1Y2Nlc3MnfHxkYXRhLnN0YXR1cz09PSdvayc7CgogICAgaWYob2spewogICAgICBkb3QuY2xhc3NOYW1lPSdzYi1kb3Qgb25saW5lJzsgdHh0LnRleHRDb250ZW50PSdPbmxpbmUnOyBsb2dpbi50ZXh0Q29udGVudD0n4pyFIExvZ2luIE9LJzsKICAgICAgY29uc3QgZGV0YWlscz0oZGF0YS5kZXRhaWxzfHxbXSkubWFwKGQ9PmAKICAgICAgICA8ZGl2IHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDo4cHg7cGFkZGluZzo2cHggMDtib3JkZXItYm90dG9tOjFweCBzb2xpZCB2YXIoLS1saW5lKTtmb250LXNpemU6MTNweCI+CiAgICAgICAgICA8c3BhbiBzdHlsZT0id2lkdGg6N3B4O2hlaWdodDo3cHg7Ym9yZGVyLXJhZGl1czo1MCU7YmFja2dyb3VuZDoke2QubG9naW49PT0nc3VjY2Vzcyc/J3ZhcigtLWdyZWVuKSc6J3ZhcigtLXJlZCknfTtkaXNwbGF5OmlubGluZS1ibG9jaztmbGV4LXNocmluazowIj48L3NwYW4+CiAgICAgICAgICA8c3BhbiBzdHlsZT0iZm9udC1mYW1pbHk6dmFyKC0tbW9ubyk7Y29sb3I6dmFyKC0taW5rMikiPiR7ZC5lbWFpbH08L3NwYW4+CiAgICAgICAgICA8c3BhbiBzdHlsZT0ibWFyZ2luLWxlZnQ6YXV0bztjb2xvcjoke2QubG9naW49PT0nc3VjY2Vzcyc/J3ZhcigtLWdyZWVuKSc6J3ZhcigtLXJlZCknfTtmb250LXdlaWdodDo2MDAiPiR7ZC5sb2dpbj09PSdzdWNjZXNzJz8nT0snOidHQUdBTCd9PC9zcGFuPgogICAgICAgIDwvZGl2PmApLmpvaW4oJycpOwogICAgICBpZihib2R5KSBib2R5LmlubmVySFRNTD1gCiAgICAgICAgPGRpdiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweDtwYWRkaW5nOjE0cHg7YmFja2dyb3VuZDpyZ2JhKDE4NCwyNTUsMTEwLC4wNik7Ym9yZGVyOjFweCBzb2xpZCByZ2JhKDE4NCwyNTUsMTEwLC4xNSk7Ym9yZGVyLXJhZGl1czo5cHg7bWFyZ2luLWJvdHRvbToxNHB4Ij4KICAgICAgICAgIDxzcGFuIHN0eWxlPSJ3aWR0aDoxMHB4O2hlaWdodDoxMHB4O2JvcmRlci1yYWRpdXM6NTAlO2JhY2tncm91bmQ6dmFyKC0tZ3JlZW4pO2FuaW1hdGlvbjpibGluayAycyBpbmZpbml0ZTtmbGV4LXNocmluazowIj48L3NwYW4+CiAgICAgICAgICA8ZGl2PjxkaXYgc3R5bGU9ImZvbnQtd2VpZ2h0OjcwMDtjb2xvcjp2YXIoLS1ncmVlbikiPkFQSSBPbmxpbmU8L2Rpdj48ZGl2IHN0eWxlPSJmb250LXNpemU6MTJweDtjb2xvcjp2YXIoLS1pbmsyKTttYXJnaW4tdG9wOjJweCI+JHtkYXRhLmFjY291bnRzX29rfHwxfSBha3VuIGFrdGlmIMK3IGlWQVMgdGVyaHVidW5nPC9kaXY+PC9kaXY+CiAgICAgICAgPC9kaXY+CiAgICAgICAgPGRpdj4ke2RldGFpbHN9PC9kaXY+CiAgICAgICAgPGRpdiBzdHlsZT0iZm9udC1mYW1pbHk6dmFyKC0tbW9ubyk7Zm9udC1zaXplOjExcHg7Y29sb3I6dmFyKC0taW5rMyk7bWFyZ2luLXRvcDoxMHB4Ij5DaGVja2VkOiAke25ldyBEYXRlKCkudG9Mb2NhbGVUaW1lU3RyaW5nKCdpZC1JRCcpfTwvZGl2PmA7CgogICAgICAvLyB1cGRhdGUgc3RhdHMKICAgICAgdHJ5ewogICAgICAgIGNvbnN0IHRkPWF3YWl0IGZldGNoKEFQSSsnL3Rlc3Q/ZGF0ZT0nK3RvZGF5U3RyKCkse3NpZ25hbDpBYm9ydFNpZ25hbC50aW1lb3V0KDIwMDAwKX0pOwogICAgICAgIGNvbnN0IGRkPWF3YWl0IHRkLmpzb24oKTsKICAgICAgICBpZihkZC50b3RhbF9yYW5nZXMpIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdzdFJhbmdlcycpLnRleHRDb250ZW50PWRkLnRvdGFsX3JhbmdlczsKICAgICAgICBpZihkZC50b3RhbF9udW1iZXJzKSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnc3ROdW1iZXJzJykudGV4dENvbnRlbnQ9ZGQudG90YWxfbnVtYmVyczsKICAgICAgfWNhdGNoKGUpe30KCiAgICB9IGVsc2UgdGhyb3cgbmV3IEVycm9yKCdnYWdhbCcpOwoKICB9IGNhdGNoKGUpewogICAgZG90LmNsYXNzTmFtZT0nc2ItZG90IG9mZmxpbmUnOyB0eHQudGV4dENvbnRlbnQ9J09mZmxpbmUnOyBsb2dpbi50ZXh0Q29udGVudD0n4p2MIEdhZ2FsJzsKICAgIGlmKGJvZHkpIGJvZHkuaW5uZXJIVE1MPWAKICAgICAgPGRpdiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweDtwYWRkaW5nOjE0cHg7YmFja2dyb3VuZDpyZ2JhKDI1NSwxMDcsMTA3LC4wNik7Ym9yZGVyOjFweCBzb2xpZCByZ2JhKDI1NSwxMDcsMTA3LC4xNSk7Ym9yZGVyLXJhZGl1czo5cHgiPgogICAgICAgIDxzcGFuIHN0eWxlPSJ3aWR0aDoxMHB4O2hlaWdodDoxMHB4O2JvcmRlci1yYWRpdXM6NTAlO2JhY2tncg==").decode("utf-8")
    return Response(html, mimetype="text/html")


@app.route("/health")
def health():
    """Cek status login semua akun."""
    sessions = login_all_accounts()
    account_status = []

    for acc in ACCOUNTS:
        session = next((s for s in sessions if s["email"] == acc["email"]), None)
        account_status.append({
            "email":  acc["email"],
            "login":  "success" if session else "failed",
        })

    total_ok = sum(1 for a in account_status if a["login"] == "success")
    return jsonify({
        "status":       "ok" if total_ok > 0 else "error",
        "login":        "success" if total_ok > 0 else "failed",
        "accounts_ok":  total_ok,
        "accounts_total": len(ACCOUNTS),
        "details":      account_status,
    }), 200 if total_ok > 0 else 500


@app.route("/accounts")
def list_accounts():
    """List akun yang terdaftar (password disembunyikan)."""
    return jsonify({
        "total": len(ACCOUNTS),
        "accounts": [
            {"index": i + 1, "email": acc["email"]}
            for i, acc in enumerate(ACCOUNTS)
        ],
    })


@app.route("/sms")
def get_sms_endpoint():
    date_str = request.args.get("date")
    mode     = request.args.get("mode", "received")

    if mode not in ("live", "received", "both"):
        return jsonify({"error": "mode harus: live, received, atau both"}), 400

    today = datetime.now().strftime("%d/%m/%Y")
    from_date = today
    to_date   = today

    if mode != "live":
        if not date_str:
            return jsonify({"error": "Parameter date wajib (DD/MM/YYYY)"}), 400
        try:
            datetime.strptime(date_str, "%d/%m/%Y")
            from_date = date_str
            to_date   = request.args.get("to_date", date_str)
        except ValueError:
            return jsonify({"error": "Format date tidak valid, gunakan DD/MM/YYYY"}), 400

    otp_messages, err = fetch_all_accounts(from_date, to_date, mode)
    if otp_messages is None:
        return jsonify({"error": err}), 500

    return jsonify({
        "status":       "success",
        "mode":         mode,
        "from_date":    from_date,
        "to_date":      to_date,
        "total":        len(otp_messages),
        "accounts_used": len(ACCOUNTS),
        "otp_messages": otp_messages,
    })


@app.route("/test")
def test_all():
    """Tampilkan semua range & nomor dari semua akun."""
    date_str = request.args.get("date", datetime.now().strftime("%d/%m/%Y"))
    sessions = login_all_accounts()

    if not sessions:
        return jsonify({"status": "error", "error": "Semua akun gagal login"}), 500

    all_ranges   = []
    total_numbers = 0

    for session in sessions:
        scraper  = session["scraper"]
        csrf     = session["csrf"]
        email    = session["email"]

        ranges = get_ranges(scraper, csrf, date_str, date_str)
        for rng in ranges:
            numbers = get_numbers(scraper, csrf, rng["name"], date_str, date_str)
            total_numbers += len(numbers)
            all_ranges.append({
                "account":       email,
                "range_name":    rng["name"],
                "range_id":      rng["id"],
                "total_numbers": len(numbers),
                "numbers":       numbers,
            })

    return jsonify({
        "status":         "ok",
        "date":           date_str,
        "accounts_ok":    len(sessions),
        "total_ranges":   len(all_ranges),
        "total_numbers":  total_numbers,
        "ranges":         all_ranges,
    })


@app.route("/test/sms")
def test_sms():
    """Cek OTP untuk 1 nomor spesifik."""
    date_str   = request.args.get("date", datetime.now().strftime("%d/%m/%Y"))
    range_name = request.args.get("range", "")
    number     = request.args.get("number", "")

    if not range_name or not number:
        return jsonify({
            "error":  "Parameter range dan number wajib",
            "contoh": "/test/sms?date=07/03/2026&range=IVORY COAST 3878&number=2250711220970"
        }), 400

    sessions = login_all_accounts()
    if not sessions:
        return jsonify({"error": "Semua akun gagal login"}), 500

    # Coba tiap akun sampai dapat hasilnya
    for session in sessions:
        msg = get_sms(session["scraper"], session["csrf"], number, range_name, date_str, date_str)
        if msg:
            return jsonify({
                "status":      "ok",
                "otp_found":   True,
                "account":     session["email"],
                "range_name":  range_name,
                "number":      number,
                "otp_message": msg,
            })

    return jsonify({
        "status":      "ok",
        "otp_found":   False,
        "range_name":  range_name,
        "number":      number,
        "otp_message": "(tidak ada SMS untuk nomor ini hari ini)",
    })


@app.route("/debug/ranges-raw")
def debug_ranges_raw():
    """Raw HTML ranges dari iVAS untuk debug."""
    date_str = request.args.get("date", datetime.now().strftime("%d/%m/%Y"))
    sessions = login_all_accounts()

    if not sessions:
        return jsonify({"error": "Semua akun gagal login"}), 500

    session = sessions[0]
    try:
        resp = session["scraper"].post(
            f"{BASE_URL}/portal/sms/received/getsms",
            data={"from": to_ivas_date(date_str), "to": to_ivas_date(date_str), "_token": session["csrf"]},
            headers=ajax_hdrs(),
            timeout=15,
        )
        html           = decode_response(resp)
        parsed_ranges  = get_ranges(session["scraper"], session["csrf"], date_str, date_str)

        # Kumpulkan semua onclick
        soup      = BeautifulSoup(html, "html.parser")
        onclicks  = [el.get("onclick", "") for el in soup.find_all(onclick=True)]
        rng_divs  = [
            {"class": d.get("class"), "onclick": d.get("onclick",""), "text": d.get_text(strip=True)[:100]}
            for d in soup.select("div.rng, div.pointer")
        ]

        return jsonify({
            "status":        "ok",
            "account":       session["email"],
            "date":          date_str,
            "http_status":   resp.status_code,
            "html_length":   len(html),
            "parsed_ranges": parsed_ranges,
            "rng_divs":      rng_divs,
            "all_onclicks":  onclicks[:40],
            "html_preview":  html[:5000],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/debug/numbers")
def debug_numbers():
    """Debug: nomor dari range tertentu."""
    date_str   = request.args.get("date", datetime.now().strftime("%d/%m/%Y"))
    range_name = request.args.get("range", "")

    if not range_name:
        return jsonify({"error": "Parameter range wajib"}), 400

    sessions = login_all_accounts()
    if not sessions:
        return jsonify({"error": "Semua akun gagal login"}), 500

    session = sessions[0]
    try:
        resp = session["scraper"].post(
            f"{BASE_URL}/portal/sms/received/getsms/number",
            data={"_token": session["csrf"], "start": to_ivas_date(date_str),
                  "end": to_ivas_date(date_str), "range": range_name},
            headers=ajax_hdrs(),
            timeout=15,
        )
        html    = decode_response(resp)
        numbers = get_numbers(session["scraper"], session["csrf"], range_name, date_str, date_str)

        return jsonify({
            "status":      "ok",
            "date":        date_str,
            "range_name":  range_name,
            "http_status": resp.status_code,
            "html_length": len(html),
            "numbers_found": numbers,
            "total":       len(numbers),
            "html_preview": html[:3000],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/debug/sms")
def debug_sms():
    """Debug: raw SMS response untuk nomor tertentu."""
    date_str   = request.args.get("date", datetime.now().strftime("%d/%m/%Y"))
    range_name = request.args.get("range", "")
    number     = request.args.get("number", "")

    if not range_name or not number:
        return jsonify({"error": "Parameter range dan number wajib"}), 400

    sessions = login_all_accounts()
    if not sessions:
        return jsonify({"error": "Semua akun gagal login"}), 500

    session = sessions[0]
    try:
        resp = session["scraper"].post(
            f"{BASE_URL}/portal/sms/received/getsms/number/sms",
            data={"_token": session["csrf"], "start": to_ivas_date(date_str),
                  "end": to_ivas_date(date_str), "Number": number, "Range": range_name},
            headers=ajax_hdrs(),
            timeout=15,
        )
        html = decode_response(resp)
        msg  = get_sms(session["scraper"], session["csrf"], number, range_name, date_str, date_str)

        return jsonify({
            "status":        "ok",
            "date":          date_str,
            "range_name":    range_name,
            "number":        number,
            "http_status":   resp.status_code,
            "html_length":   len(html),
            "message_found": msg,
            "html_preview":  html[:3000],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)

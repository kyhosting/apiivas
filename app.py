# KY-SHIRO API — Multi-Account iVAS SMS
# Developer: Kiki Faizal

from flask import Flask, request, jsonify, Response
from datetime import datetime
import requests
from bs4 import BeautifulSoup
import logging
import os
import gzip
import re
import random
import threading
import html as html_lib
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

    Priority:
    1. Env var IVAS_ACCOUNTS = "email1:pass1,email2:pass2,..."
       → dipakai kalau diset, TAMBAH ke default (tidak replace)
    2. Default 4 akun hardcoded di bawah

    PENTING: Jangan set IVAS_ACCOUNTS dengan 1 akun saja di Vercel
    kalau mau multi-akun. Gunakan format lengkap semua akun,
    atau biarkan kosong supaya pakai default 4 akun di bawah.
    """
    # 4 akun default — selalu ada
    defaults = [
        {"email": "ceptampan58@gmail.com",   "password": "Encep12345"},
        {"email": "kicenofficial@gmail.com",  "password": "@Kiki2008"},
        {"email": "kikiridwan1983@gmail.com", "password": "@Kiki2008"},
        {"email": "xeviermassie83@gmail.com", "password": "@Kiki2008"},
    ]

    env = os.getenv("IVAS_ACCOUNTS", "").strip()
    if env:
        # Kalau env var diset → pakai env var SAJA (full override)
        accounts = []
        for pair in env.split(","):
            pair = pair.strip()
            if ":" in pair:
                parts = pair.split(":", 1)
                email = parts[0].strip()
                pwd   = parts[1].strip()
                if email and pwd:
                    accounts.append({"email": email, "password": pwd})
        if accounts:
            logger.info(f"[CONFIG] {len(accounts)} akun dari env IVAS_ACCOUNTS")
            return accounts
        else:
            logger.warning("[CONFIG] IVAS_ACCOUNTS diset tapi format salah, pakai default")

    logger.info(f"[CONFIG] Pakai {len(defaults)} akun default")
    return defaults

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

def build_scraper():
    """Buat scraper dengan UA acak dan headers realistis."""
    s  = requests.Session()
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
    Login satu akun. Auto re-login kalau session expired.
    Return dict: {ok, scraper, csrf, live_html, email} atau {ok: False, error, email}
    """
    email    = account["email"]
    password = account["password"]
    scraper  = build_scraper()

    try:

        # Ambil halaman login → dapat _token
        login_page = scraper.get(LOGIN_URL)
        soup       = BeautifulSoup(login_page.text, "html.parser")
        tok_el     = soup.find("input", {"name": "_token"})
        if not tok_el:
            return {"ok": False, "error": "_token tidak ditemukan", "email": email}
        tok = tok_el["value"]


        # POST login
        resp = scraper.post(
            LOGIN_URL,
            data={"email": email, "password": password, "_token": tok},
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Referer": LOGIN_URL, "Origin": BASE_URL},
            allow_redirects=True,
        )

        if "/login" in resp.url:
            return {"ok": False, "error": "Email/password salah", "email": email}


        # Ambil halaman live → dapat csrf terbaru
        portal = scraper.get(LIVE_URL)
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



# Session cache — diinisialisasi sebelum login functions
_session_cache: dict = {}
_session_lock  = threading.Lock()

def login_all_accounts():
    """Login semua akun secara paralel. Return list session yang berhasil. Simpan ke cache."""
    sessions = []
    with ThreadPoolExecutor(max_workers=max(len(ACCOUNTS), 1)) as ex:
        futures = {ex.submit(login_account, acc): acc for acc in ACCOUNTS}
        for future in as_completed(futures):
            result = future.result()
            # Simpan ke cache — termasuk yang gagal (ok=False) supaya get_session tidak re-login sia-sia
            with _session_lock:
                _session_cache[result["email"]] = result
            if result["ok"]:
                sessions.append(result)
            else:
                logger.warning(f"[LOGIN] Gagal: {result['email']} — {result.get('error','')}")
    logger.info(f"[LOGIN] {len(sessions)}/{len(ACCOUNTS)} akun berhasil")
    return sessions


# ════════════════════════════════════════════════════════
# LIVE SMS
# ════════════════════════════════════════════════════════



def _is_session_expired(response):
    """Deteksi apakah iVAS sudah logout / session habis."""
    if response is None:
        return True
    url = getattr(response, 'url', '') or ''
    if '/login' in url:
        return True
    try:
        snippet = response.text[:3000].lower()
        if any(k in snippet for k in ('forgot your password', 'login to your account')):
            return True
    except Exception:
        pass
    return False


def get_session(account, force=False):
    """
    Kembalikan session aktif untuk akun ini (dari cache).
    Kalau belum ada, expired, atau force=True → login ulang otomatis.
    """
    email = account["email"]
    with _session_lock:
        cached = _session_cache.get(email)
        if not force and cached and cached.get("ok"):
            return cached
        result = login_account(account)
        _session_cache[email] = result
        return result


def do_request(account, method, url, data=None, headers=None):
    """
    Buat satu request POST/GET untuk akun. Kalau session expired → login ulang
    otomatis, lalu retry. No delay, no timeout. Auto re-login kalau session expired.
    """
    data  = dict(data) if data else {}
    email = account["email"]
    for attempt in range(3):
        session = get_session(account, force=(attempt > 0))
        if not session or not session.get("ok"):
            logger.error(f"[REQ] Login gagal {email} attempt {attempt+1}")
            continue  # langsung retry, no sleep

        scraper        = session["scraper"]
        csrf           = session["csrf"]
        data["_token"] = csrf

        try:
            if method.upper() == "GET":
                resp = scraper.get(url, headers=headers)
            else:
                resp = scraper.post(url, data=data, headers=headers)

            if _is_session_expired(resp):
                logger.warning(f"[REQ] Expired {email} attempt {attempt+1}, re-login...")
                with _session_lock:
                    _session_cache[email] = {"ok": False}
                continue  # langsung re-login, no sleep

            return resp, csrf

        except Exception as e:
            logger.error(f"[REQ] Error {email} attempt {attempt+1}: {e}")
            # langsung retry, no sleep

    return None, None




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

def get_ranges(account, from_date, to_date):
    """
    Level 1 — Ambil daftar range dari /portal/sms/received/getsms.

    Dari debug HTML iVAS, struktur yang dikonfirmasi:
      <div class="rng" onclick="toggleRange('ZIMBABWE 188','ZIMBABWE_188')">
        <span class="rname">ZIMBABWE 188</span>
      </div>
      <div class="sub" id="sp_ZIMBABWE_188"></div>

    toggleRange(name, id) → name=display name, id=safe key (spasi→underscore)
    Step 2 butuh RANGE ID (bukan nama) sebagai payload.
    Return: [{"name": "ZIMBABWE 188", "id": "ZIMBABWE_188"}, ...]
    """
    resp, _ = do_request(
        account, "POST",
        f"{BASE_URL}/portal/sms/received/getsms",
        data={"from": to_ivas_date(from_date), "to": to_ivas_date(to_date)},
        headers=ajax_hdrs(),
    )
    if resp is None or resp.status_code != 200:
        logger.warning(f"[RANGES] HTTP {resp.status_code if resp else 'None'}")
        return []

    html   = decode_response(resp)
    result = []

    def _add(name, rid):
        name = name.strip()
        rid  = rid.strip() if rid else name.replace(" ", "_")
        if name and not any(r["name"] == name for r in result):
            result.append({"name": name, "id": rid})

    # ── Pass 1 (utama): toggleRange('NAME','ID') dari onclick ──────────────
    # Confirmed dari debug: toggleRange('ZIMBABWE 188','ZIMBABWE_188')
    for m in re.finditer(r"toggleRange\s*\(\s*'([^']+)'\s*,\s*'([^']+)'\s*\)", html):
        _add(m.group(1), m.group(2))

    # ── Pass 2: toggleRange dengan double-quote ─────────────────────────────
    if not result:
        for m in re.finditer(r'toggleRange\s*\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\)', html):
            _add(m.group(1), m.group(2))

    # ── Pass 3: fallback BeautifulSoup div.rng ─────────────────────────────
    if not result:
        soup = BeautifulSoup(html, "html.parser")
        for div in soup.select("div.rng"):
            rname_el = div.select_one("span.rname")
            name     = rname_el.get_text(strip=True) if rname_el else ""
            if not name:
                name = div.get_text(separator="|", strip=True).split("|")[0]
            oc  = div.get("onclick", "")
            m   = re.search(r"toggleRange[^(]*\(\s*'([^']+)'\s*,\s*'([^']+)'", oc)
            rid = m.group(2) if m else ""
            if not rid:
                sub = div.select_one("[id^='sp_']")
                rid = sub["id"].replace("sp_", "") if sub else name.replace(" ", "_")
            _add(name, rid)

    if not result:
        logger.warning(f"[RANGES] 0 ranges. HTML({len(html)}): {html[:500]}")
    else:
        logger.info(f"[RANGES] {len(result)} ranges: {[(r['name'], r['id']) for r in result]}")
    return result


def get_numbers(account, range_name, from_date, to_date, range_id=None):
    """
    Level 2 — Ambil nomor di range dari /portal/sms/received/getsms/number.

    CONFIRMED dari debug iVAS:
      Response: div.nrow onclick="toggleNumXXX('NOMOR','NOMOR_ID')"

    Coba 2 parameter: range=NAMA dulu, lalu range=ID kalau gagal.
    Return: [{"number": "263784490048", "num_id": "263784490048_178138689"}, ...]
    """
    rid = range_id or range_name.replace(" ", "_")

    def _parse_numbers(html):
        nums = []
        def _add(num, num_id=""):
            d = re.sub(r'\D', '', str(num))
            if 7 <= len(d) <= 15 and not any(n["number"] == d for n in nums):
                nums.append({"number": d, "num_id": num_id or d})
        # Pass 1: toggleNum...('NOMOR','ID')
        for m in re.finditer(r"toggleNum\w+\s*\(\s*'(\d{7,15})'\s*,\s*'([^']+)'\s*\)", html):
            _add(m.group(1), m.group(2))
        if not nums:
            for m in re.finditer(r'toggleNum\w+\s*\(\s*"(\d{7,15})"\s*,\s*"([^"]+)"\s*\)', html):
                _add(m.group(1), m.group(2))
        # Pass 2: span.nnum
        if not nums:
            from bs4 import BeautifulSoup as _BS
            soup = _BS(html, "html.parser")
            for el in soup.select("span.nnum"):
                _add(re.sub(r'\D', '', el.get_text(strip=True)))
        # Pass 3: angka dalam quotes
        if not nums:
            for m in re.finditer(r"'(\d{7,15})'", html):
                _add(m.group(1))
        return nums

    # Coba dengan range NAMA (spasi) dulu
    resp, _ = do_request(
        account, "POST",
        f"{BASE_URL}/portal/sms/received/getsms/number",
        data={"start": to_ivas_date(from_date), "end": to_ivas_date(to_date), "range": range_name},
        headers=ajax_hdrs(),
    )
    if resp and resp.status_code == 200:
        html = decode_response(resp)
        numbers = _parse_numbers(html)
        if numbers:
            logger.info(f"[NUMBERS] {range_name} (by nama) → {[n['number'] for n in numbers]}")
            return numbers
        logger.info(f"[NUMBERS] {range_name} by nama → 0, coba by id={rid}")

    # Coba dengan range ID (underscore)
    resp2, _ = do_request(
        account, "POST",
        f"{BASE_URL}/portal/sms/received/getsms/number",
        data={"start": to_ivas_date(from_date), "end": to_ivas_date(to_date), "range": rid},
        headers=ajax_hdrs(),
    )
    if resp2 and resp2.status_code == 200:
        html2 = decode_response(resp2)
        numbers2 = _parse_numbers(html2)
        if numbers2:
            logger.info(f"[NUMBERS] {range_name} (by id={rid}) → {[n['number'] for n in numbers2]}")
            return numbers2

    logger.warning(f"[NUMBERS] '{range_name}' 0 nomor (coba nama & id keduanya gagal)")
    return []


def get_sms(account, phone_number, range_name, from_date, to_date):
    """
    Level 3 — Ambil isi SMS untuk 1 nomor dari /portal/sms/received/getsms/number/sms.

    CONFIRMED dari debug iVAS:
      Payload: start, end, Number=nomor, Range=NAMA_RANGE  ← Range pakai NAMA bukan ID
      Response: <table> dengan <td><div class="msg-text">PESAN</div></td>
      Pesan berisi HTML entities: &lt;#&gt; = <#>
    """

    resp, _ = do_request(
        account, "POST",
        f"{BASE_URL}/portal/sms/received/getsms/number/sms",
        data={
            "start":  to_ivas_date(from_date),
            "end":    to_ivas_date(to_date),
            "Number": phone_number,
            "Range":  range_name,          # ← nama range, bukan ID
        },
        headers=ajax_hdrs(),
    )
    if resp is None or resp.status_code != 200:
        return None

    raw  = decode_response(resp)
    soup = BeautifulSoup(raw, "html.parser")

    def _clean(t):
        """Unescape HTML entities dan bersihkan whitespace."""
        return html_lib.unescape(t).strip()

    # ── Pass 1 (UTAMA): div.msg-text ← confirmed dari debug ──────────────
    el = soup.select_one("div.msg-text")
    if el:
        t = _clean(el.get_text(separator="\n", strip=True))
        if len(t) > 3:
            logger.info(f"[SMS] {phone_number} ✓ div.msg-text")
            return t

    # ── Pass 2: CSS selectors lain ────────────────────────────────────────
    for sel in [
        "td.msg-text", "p.msg-text", "span.msg-text",
        "div.smsg", "p.smsg",
        "div.sms-message", "p.sms-message",
        "div.message-content", "div.msg-body",
        ".col-9.col-sm-6 p", ".col-9 p",
    ]:
        el = soup.select_one(sel)
        if el:
            t = _clean(el.get_text(separator="\n", strip=True))
            if len(t) > 3:
                logger.info(f"[SMS] {phone_number} ✓ CSS '{sel}'")
                return t

    # ── Pass 3: kolom Message di <table> ─────────────────────────────────
    # Confirmed: <th>Sender</th><th>Message</th><th>Time</th><th>Revenue</th>
    for tbl in soup.find_all("table"):
        ths  = [th.get_text(strip=True).lower() for th in tbl.find_all("th")]
        col  = None
        for kw in ("message", "content", "sms", "text", "body"):
            for i, h in enumerate(ths):
                if kw in h:
                    col = i
                    break
            if col is not None:
                break
        if col is None:
            continue
        for tr in tbl.select("tbody tr"):
            tds = tr.find_all("td")
            if len(tds) > col:
                # Cek inner div.msg-text dulu
                inner = tds[col].select_one("div.msg-text, .msg-text")
                if inner:
                    t = _clean(inner.get_text(separator="\n", strip=True))
                else:
                    t = _clean(tds[col].get_text(separator="\n", strip=True))
                if t and len(t) > 3 and not t.isdigit():
                    logger.info(f"[SMS] {phone_number} ✓ tabel col={col}")
                    return t

    # ── Pass 4: scoring leaf elements ─────────────────────────────────────
    best_score, best_txt = 0, None
    for el in soup.find_all(["p", "div", "span", "td", "li"]):
        if el.find_all(True):
            continue
        t = _clean(el.get_text(separator=" ", strip=True))
        if len(t) < 5:
            continue
        sc  = 0
        sc += 4 if re.search(r"\d{4,8}", t) else 0
        sc += 3 if len(t) > 20 else (1 if len(t) > 8 else 0)
        sc += 2 if re.search(r"[a-zA-Z]{3,}", t) else 0
        if sc > best_score:
            best_score, best_txt = sc, t
    if best_score >= 4 and best_txt:
        logger.info(f"[SMS] {phone_number} ✓ scoring={best_score}")
        return best_txt

    # ── Pass 5: full text fallback ────────────────────────────────────────
    for el in soup(["script", "style", "noscript"]):
        el.decompose()
    for line in soup.get_text(separator="\n", strip=True).splitlines():
        line = _clean(line)
        if len(line) >= 8 and re.search(r"\d{4,}", line) and re.search(r"[a-zA-Z]", line):
            logger.info(f"[SMS] {phone_number} ✓ full-text fallback")
            return line

    logger.warning(f"[SMS] {phone_number}@{range_name} GAGAL. HTML({len(raw)}): {raw[:300]}")
    return None



def fetch_received_from_session(session, from_date, to_date):
    """Ambil semua received SMS dari 1 akun. Return list OTP."""
    email   = session["email"]
    account = next((a for a in ACCOUNTS if a["email"] == email), None)
    if not account:
        return []

    ranges = get_ranges(account, from_date, to_date)
    if not ranges:
        logger.info(f"[RECV] {email}: tidak ada range")
        return []

    # Kumpulkan tasks: (number_str, num_id, range_name)
    # get_numbers sekarang return [{"number": "...", "num_id": "..."}]
    tasks = []
    for rng in ranges:
        num_list = get_numbers(account, rng["name"], from_date, to_date, range_id=rng["id"])
        for n in num_list:
            # Support both format: dict {number,num_id} atau string lama
            if isinstance(n, dict):
                tasks.append((n["number"], rng["name"]))
            else:
                tasks.append((str(n), rng["name"]))

    if not tasks:
        logger.info(f"[RECV] {email}: tidak ada nomor di semua range")
        return []

    results = []

    def _fetch(args):
        num, rng_name = args
        msg = get_sms(account, num, rng_name, from_date, to_date)
        if msg:
            return {
                "range":        rng_name,
                "phone_number": num,
                "otp_message":  msg,
                "source":       "received",
                "account":      email,
            }
        return None

    with ThreadPoolExecutor(max_workers=max(len(tasks), 1)) as ex:
        futures = [ex.submit(_fetch, t) for t in tasks]
        for future in as_completed(futures):
            try:
                res = future.result()
                if res:
                    results.append(res)
            except Exception as e:
                logger.error(f"[RECV] Future error: {e}")

    logger.info(f"[RECV] {email}: {len(results)}/{len(tasks)} SMS berhasil")
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
        with ThreadPoolExecutor(max_workers=max(len(sessions), 1)) as ex:
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

        acc_t = next((a for a in ACCOUNTS if a["email"] == email), None)
        ranges = get_ranges(acc_t, date_str, date_str)
        for rng in ranges:
            numbers = get_numbers(acc_t, rng["name"], date_str, date_str)
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
        acc_ts = next((a for a in ACCOUNTS if a["email"] == session["email"]), None)
        msg = get_sms(acc_ts, number, range_name, date_str, date_str)
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


def _raw_post(acc, url, data):
    """Helper: POST request, return (resp, body_text)."""
    resp, _ = do_request(acc, "POST", url, data=data, headers=ajax_hdrs(RECV_URL))
    if resp is None:
        return None, "NULL RESPONSE"
    body = decode_response(resp)
    return resp, body


def _req_info(resp, body):
    """Satu blok info header untuk debug output."""
    if resp is None:
        return "  Status  : NULL\n  Body    : (no response)\n"
    return (
        f"  Status       : {resp.status_code}\n"
        f"  Final URL    : {getattr(resp, 'url', '?')}\n"
        f"  Content-Type : {resp.headers.get('Content-Type', '?')}\n"
        f"  Body Length  : {len(body)} chars\n"
    )


@app.route("/debug/full")
def debug_full():
    """
    DEBUG LENGKAP — intercept semua 3 level request sekaligus.
    Tampilkan raw body tiap step TANPA dipotong.

    Usage : /debug/full?date=06/03/2026
    Output: plain text — 3 blok (ranges / numbers / sms)

    Setelah deploy, buka URL ini di browser, kirim hasilnya untuk analisis.
    """
    date_str = request.args.get("date", datetime.now().strftime("%d/%m/%Y"))
    ivas_date = to_ivas_date(date_str)

    sessions = login_all_accounts()
    if not sessions:
        return "ERROR: Semua akun gagal login\n", 500

    acc   = next((a for a in ACCOUNTS if a["email"] == sessions[0]["email"]), None)
    email = sessions[0]["email"]
    out   = []
    SEP   = "=" * 70

    out.append(f"{SEP}")
    out.append(f"  KY-SHIRO DEBUG FULL — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    out.append(f"  Account  : {email}")
    out.append(f"  Date     : {date_str}  →  iVAS format: {ivas_date}")
    out.append(f"{SEP}\n")

    # ── STEP 1: POST /portal/sms/received/getsms ──────────
    out.append(f"{'─'*70}")
    out.append(f"STEP 1 — POST /portal/sms/received/getsms")
    out.append(f"  Payload: from={ivas_date}  to={ivas_date}")
    out.append(f"{'─'*70}")

    r1, b1 = _raw_post(acc,
        f"{BASE_URL}/portal/sms/received/getsms",
        {"from": ivas_date, "to": ivas_date},
    )
    out.append(_req_info(r1, b1))
    out.append("--- RAW BODY START ---")
    out.append(b1)
    out.append("--- RAW BODY END ---\n")

    # Parse ranges dari response step 1
    ranges = get_ranges(acc, date_str, date_str)
    out.append(f"  Parsed Ranges: {ranges}\n")

    if not ranges:
        out.append("STOP: Tidak ada range ditemukan dari Step 1.")
        out.append("Kemungkinan: (1) tidak ada SMS hari ini, atau (2) format response berbeda.\n")
        return Response("\n".join(out), mimetype="text/plain; charset=utf-8")

    # ── STEP 2: POST /portal/sms/received/getsms/number ───
    # Ambil range PERTAMA yang ada untuk contoh
    first_range = ranges[0]["name"]
    out.append(f"{'─'*70}")
    out.append(f"STEP 2 — POST /portal/sms/received/getsms/number")
    out.append(f"  Payload: start={ivas_date}  end={ivas_date}  range={first_range}")
    out.append(f"{'─'*70}")

    r2, b2 = _raw_post(acc,
        f"{BASE_URL}/portal/sms/received/getsms/number",
        {"start": ivas_date, "end": ivas_date, "range": first_range},
    )
    out.append(_req_info(r2, b2))
    out.append("--- RAW BODY START ---")
    out.append(b2)
    out.append("--- RAW BODY END ---\n")

    numbers = get_numbers(acc, first_range, date_str, date_str)
    out.append(f"  Parsed Numbers: {numbers}\n")

    if not numbers:
        out.append("STOP: Tidak ada nomor ditemukan dari Step 2.")
        out.append("Kemungkinan: parameter 'range' salah, atau format HTML berbeda.\n")
        return Response("\n".join(out), mimetype="text/plain; charset=utf-8")

    # ── STEP 3: POST /portal/sms/received/getsms/number/sms
    # Ambil nomor PERTAMA untuk contoh
    first_num = numbers[0]
    out.append(f"{'─'*70}")
    out.append(f"STEP 3 — POST /portal/sms/received/getsms/number/sms")
    out.append(f"  Payload: start={ivas_date}  end={ivas_date}")
    out.append(f"           Number={first_num}  Range={first_range}")
    out.append(f"{'─'*70}")

    r3, b3 = _raw_post(acc,
        f"{BASE_URL}/portal/sms/received/getsms/number/sms",
        {"start": ivas_date, "end": ivas_date, "Number": first_num, "Range": first_range},
    )
    out.append(_req_info(r3, b3))
    out.append("--- RAW BODY START ---")
    out.append(b3)
    out.append("--- RAW BODY END ---\n")

    msg = get_sms(acc, first_num, first_range, date_str, date_str)
    out.append(f"  Parsed Message: {repr(msg)}\n")

    # ── STEP 3b: coba variasi parameter ───────────────────
    # Kadang iVAS butuh 'number' kecil atau 'range_id' bukan nama
    out.append(f"{'─'*70}")
    out.append(f"STEP 3b — Variasi parameter (lowercase keys)")
    out.append(f"{'─'*70}")

    r3b, b3b = _raw_post(acc,
        f"{BASE_URL}/portal/sms/received/getsms/number/sms",
        {"start": ivas_date, "end": ivas_date, "number": first_num, "range": first_range},
    )
    out.append(_req_info(r3b, b3b))
    out.append("--- RAW BODY START ---")
    out.append(b3b)
    out.append("--- RAW BODY END ---\n")

    # ── STEP 3c: coba dengan range_id ─────────────────────
    first_range_id = ranges[0].get("id", first_range)
    if first_range_id != first_range:
        out.append(f"{'─'*70}")
        out.append(f"STEP 3c — Pakai range_id='{first_range_id}' bukan nama range")
        out.append(f"{'─'*70}")

        r3c, b3c = _raw_post(acc,
            f"{BASE_URL}/portal/sms/received/getsms/number/sms",
            {"start": ivas_date, "end": ivas_date, "Number": first_num, "Range": first_range_id},
        )
        out.append(_req_info(r3c, b3c))
        out.append("--- RAW BODY START ---")
        out.append(b3c)
        out.append("--- RAW BODY END ---\n")

    out.append(f"{SEP}")
    out.append("END OF DEBUG")
    out.append(f"{SEP}")

    return Response("\n".join(out), mimetype="text/plain; charset=utf-8")


@app.route("/debug/ranges-raw")
def debug_ranges_raw():
    """Raw Step 1 only. Pakai /debug/full untuk debug lengkap."""
    date_str  = request.args.get("date", datetime.now().strftime("%d/%m/%Y"))
    sessions  = login_all_accounts()
    if not sessions:
        return "ERROR: Login gagal\n", 500
    acc       = next((a for a in ACCOUNTS if a["email"] == sessions[0]["email"]), None)
    ivas_date = to_ivas_date(date_str)
    r, b      = _raw_post(acc, f"{BASE_URL}/portal/sms/received/getsms",
                          {"from": ivas_date, "to": ivas_date})
    parsed    = get_ranges(acc, date_str, date_str)
    hdr = (f"ACCOUNT: {sessions[0]['email']} | DATE: {date_str} | "
           f"STATUS: {r.status_code if r else 'NULL'} | "
           f"LEN: {len(b)} | PARSED: {parsed}\n{'='*60}\n\n")
    return Response(hdr + b, mimetype="text/plain; charset=utf-8")


@app.route("/debug/numbers")
def debug_numbers():
    """Raw Step 2 only. Pakai /debug/full untuk debug lengkap."""
    date_str   = request.args.get("date", datetime.now().strftime("%d/%m/%Y"))
    range_name = request.args.get("range", "")
    if not range_name:
        return "ERROR: ?range= wajib\nContoh: /debug/numbers?range=ZIMBABWE 188&date=06/03/2026\n", 400
    sessions   = login_all_accounts()
    if not sessions:
        return "ERROR: Login gagal\n", 500
    acc        = next((a for a in ACCOUNTS if a["email"] == sessions[0]["email"]), None)
    ivas_date  = to_ivas_date(date_str)
    r, b       = _raw_post(acc, f"{BASE_URL}/portal/sms/received/getsms/number",
                           {"start": ivas_date, "end": ivas_date, "range": range_name})
    numbers    = get_numbers(acc, range_name, date_str, date_str)
    hdr = (f"ACCOUNT: {sessions[0]['email']} | DATE: {date_str} | RANGE: {range_name}\n"
           f"STATUS: {r.status_code if r else 'NULL'} | LEN: {len(b)} | NUMBERS: {numbers}\n"
           f"{'='*60}\n\n")
    return Response(hdr + b, mimetype="text/plain; charset=utf-8")


@app.route("/debug/sms")
def debug_sms():
    """Raw Step 3 only + variasi parameter. Pakai /debug/full untuk debug lengkap."""
    date_str   = request.args.get("date", datetime.now().strftime("%d/%m/%Y"))
    range_name = request.args.get("range", "")
    number     = request.args.get("number", "")
    if not range_name or not number:
        return (
            "ERROR: ?range= dan ?number= wajib\n"
            "Contoh: /debug/sms?range=ZIMBABWE 188&number=263784490048&date=06/03/2026\n"
        ), 400
    sessions   = login_all_accounts()
    if not sessions:
        return "ERROR: Login gagal\n", 500
    acc        = next((a for a in ACCOUNTS if a["email"] == sessions[0]["email"]), None)
    ivas_date  = to_ivas_date(date_str)
    out        = []

    # Variasi A: Number + Range (kapital)
    rA, bA = _raw_post(acc, f"{BASE_URL}/portal/sms/received/getsms/number/sms",
                       {"start": ivas_date, "end": ivas_date, "Number": number, "Range": range_name})
    msgA   = get_sms(acc, number, range_name, date_str, date_str)
    out.append(f"=== VARIASI A: Number={number} Range={range_name} (kapital) ===")
    out.append(f"Status: {rA.status_code if rA else 'NULL'} | Len: {len(bA)} | Parsed: {repr(msgA)}")
    out.append("--- BODY ---"); out.append(bA); out.append("")

    # Variasi B: number + range (lowercase)
    rB, bB = _raw_post(acc, f"{BASE_URL}/portal/sms/received/getsms/number/sms",
                       {"start": ivas_date, "end": ivas_date, "number": number, "range": range_name})
    out.append(f"=== VARIASI B: number+range lowercase ===")
    out.append(f"Status: {rB.status_code if rB else 'NULL'} | Len: {len(bB)}")
    out.append("--- BODY ---"); out.append(bB); out.append("")

    # Variasi C: phone_number
    rC, bC = _raw_post(acc, f"{BASE_URL}/portal/sms/received/getsms/number/sms",
                       {"start": ivas_date, "end": ivas_date, "phone_number": number, "range_name": range_name})
    out.append(f"=== VARIASI C: phone_number + range_name ===")
    out.append(f"Status: {rC.status_code if rC else 'NULL'} | Len: {len(bC)}")
    out.append("--- BODY ---"); out.append(bC); out.append("")

    return Response("\n".join(out), mimetype="text/plain; charset=utf-8")


@app.route("/debug/live-raw")
def debug_live_raw():
    """HTML mentah halaman live SMS."""
    sessions = login_all_accounts()
    if not sessions:
        return "ERROR: Login gagal\n", 500
    acc  = next((a for a in ACCOUNTS if a["email"] == sessions[0]["email"]), None)
    sess = get_session(acc)
    html = (sess.get("live_html") or "") if sess and sess.get("ok") else ""
    if not html:
        r, _ = do_request(acc, "GET", LIVE_URL, headers={"Referer": BASE_URL})
        html = decode_response(r) if r else "EMPTY"
    hdr = f"ACCOUNT: {sessions[0]['email']} | LEN: {len(html)}\n{'='*60}\n\n"
    return Response(hdr + html, mimetype="text/plain; charset=utf-8")


# NUMBER MANAGEMENT
# Confirmed dari raw-debug:
#   Data  : GET  /portal/numbers/test  (DataTables JSON, header XHR wajib)
#   Add   : POST /portal/numbers/termination/number/add
#   Delete: POST /portal/numbers/termination/details  {id: number_id}
#   MyNum : GET  /portal/numbers  (DataTables JSON sama)
#   Row fields: {id, range, test_number, A2P, term, Limit_Range,
#                limit_did_a2p, limit_cli_did_a2p, created_at, action}
#   number_id = row["id"]  atau parse dari onclick="TerminationDetials('ID')"
# ════════════════════════════════════════════════════════

def _fetch_datatables(account, base_url, search="", length=100,
                      col_data=None, col_name=None, fallback_fields=None):
    """
    Fetch DataTables JSON dari iVAS.
    col_data / col_name: list string nama kolom (harus sama panjang).
    Return (list_of_rows_as_dict, recordsTotal).
    """
    if col_data is None:
        col_data = ["range", "test_number"]
        col_name = ["terminations.range", "terminations.test_number"]
    if fallback_fields is None:
        fallback_fields = ["range","test_number","term","A2P","Limit_Range",
                           "limit_did_a2p","limit_cli_did_a2p","created_at","action"]
    col_qs = "".join(
        f"&columns[{i}][data]={d}&columns[{i}][name]={n}"
        for i, (d, n) in enumerate(zip(col_data, col_name))
    )
    qs = (
        f"draw=1{col_qs}"
        "&order[0][column]=0&order[0][dir]=asc"
        f"&start=0&length={length}"
        f"&search[value]={search}&search[regex]=false"
    )
    hdrs = {
        "Accept":           "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Referer":          base_url,
    }
    resp, _ = do_request(account, "GET", f"{base_url}?{qs}", headers=hdrs)
    if resp is None or resp.status_code != 200:
        return [], 0
    try:
        data  = resp.json()
        rows  = data.get("data", [])
        total = data.get("recordsTotal", len(rows))
        if rows and isinstance(rows[0], list):
            rows = [dict(zip(fallback_fields, r)) for r in rows]
        return rows, total
    except Exception:
        return [], 0


def _fetch_my_numbers(account, search="", length=100):
    """
    Fetch My Numbers dari /portal/numbers.
    Confirmed dari debug: field Number (kapital), range, A2P, LimitA2P,
    limit_did_a2p, limit_cli_a2p. number_id dari ReturnNumberToSystem(ID).
    """
    col_data = ["Number", "range", "A2P", "LimitA2P", "limit_did_a2p", "limit_cli_a2p", "number_id", "action"]
    col_name = ["Number", "range", "A2P",  "LimitA2P", "limit_did_a2p", "limit_cli_a2p", "number_id", "action"]
    fallback = ["Number", "range", "A2P",  "LimitA2P", "limit_did_a2p", "limit_cli_a2p", "number_id", "action"]
    rows, total = _fetch_datatables(
        account, f"{BASE_URL}/portal/numbers",
        search=search, length=length,
        col_data=col_data, col_name=col_name, fallback_fields=fallback,
    )
    return rows, total


def _get_number_id(row):
    """Ambil number_id dari row. Confirmed: ReturnNumberToSystem(ID) di field action."""
    for key in ("number_id", "id", "DT_RowId"):
        v = str(row.get(key, "")).strip()
        if v and v.isdigit():
            return v
    action = str(row.get("action", "") or "")
    m = re.search(r"ReturnNumberToSystem\s*\(\s*[^)]*?(\d+)[^)]*?\)", action)
    if m:
        return m.group(1)
    m = re.search(r"TerminationDetials\s*\(\s*[^)]*?(\d+)[^)]*?\)", action)
    if m:
        return m.group(1)
    m = re.search(r"data-id=.{0,2}(\d+).{0,2}", action)
    if m:
        return m.group(1)
    return ""


# ════════════════════════════════════════════════════════
# HELPER — parse iVAS response jadi success/message
# ════════════════════════════════════════════════════════

def _parse_ivas_resp(resp):
    """Return (success:bool, message:str, raw:str)"""
    if resp is None:
        return False, "No response", ""
    raw = decode_response(resp)
    try:
        jr      = resp.json()
        message = jr.get("message", jr.get("msg", jr.get("error", str(jr))))
        st      = jr.get("status", jr.get("success", ""))
        success = str(st).lower() in ("success","ok","true","1") or st is True or st == 1
        return success, str(message), raw
    except Exception:
        if any(k in raw.lower() for k in ("success","berhasil","added","returned","deleted")):
            return True, "OK", raw
        return resp.status_code in (200, 201), f"HTTP {resp.status_code}", raw


def _get_account(email):
    return next((a for a in ACCOUNTS if a["email"] == email), None)


# ════════════════════════════════════════════════════════
# /numbers/test-list — semua akun paralel
# ════════════════════════════════════════════════════════

@app.route("/numbers/test-list")
def numbers_test_list():
    """
    GET /numbers/test-list
    List Test Numbers dari /portal/numbers/test — SEMUA akun paralel.
    Params: search, limit (default 100), account (opsional, filter 1 akun)
    """
    sessions = login_all_accounts()
    if not sessions:
        return jsonify({"error": "Login gagal semua akun"}), 500

    search    = request.args.get("search", "")
    limit     = int(request.args.get("limit", 100))
    acc_email = request.args.get("account", "")
    targets   = [s for s in sessions if s["email"] == acc_email] if acc_email else sessions

    all_numbers, errors = [], []
    lock = __import__("threading").Lock()

    def _fetch_one(session):
        email   = session["email"]
        account = _get_account(email)
        if not account:
            return
        try:
            rows, total = _fetch_datatables(
                account, f"{BASE_URL}/portal/numbers/test",
                search=search, length=limit
            )
            result = []
            for row in rows:
                test_num = re.sub(r"<[^>]+>", "", str(row.get("test_number",""))).strip()
                if not test_num:
                    continue
                result.append({
                    "account":           email,
                    "number_id":         _get_number_id(row),
                    "range_name":        re.sub(r"<[^>]+>","",str(row.get("range",""))).strip(),
                    "test_number":       test_num,
                    "term":              str(row.get("term","")),
                    "rate_a2p":          str(row.get("A2P","")),
                    "limit_range":       str(row.get("Limit_Range","")),
                    "limit_did_a2p":     str(row.get("limit_did_a2p","")),
                    "limit_cli_did_a2p": str(row.get("limit_cli_did_a2p","")),
                    "created_at":        str(row.get("created_at","")),
                })
            with lock:
                all_numbers.extend(result)
            logger.info(f"[TEST-LIST] {email}: {len(rows)} nomor (total iVAS: {total})")
        except Exception as e:
            with lock:
                errors.append({"account": email, "error": str(e)})
            logger.error(f"[TEST-LIST] Error {email}: {e}")

    with ThreadPoolExecutor(max_workers=len(targets)) as ex:
        list(ex.map(_fetch_one, targets))

    return jsonify({
        "status":       "ok",
        "accounts_ok":  len(targets) - len(errors),
        "accounts_fail": len(errors),
        "total":        len(all_numbers),
        "numbers":      all_numbers,
        "errors":       errors,
    })


# ════════════════════════════════════════════════════════
# /numbers/my-list — semua akun paralel
# ════════════════════════════════════════════════════════

@app.route("/numbers/my-list")
def numbers_my_list():
    """
    GET /numbers/my-list
    List My Numbers dari /portal/numbers — SEMUA akun paralel.
    Params: search, limit (default 100), account (opsional)
    """
    sessions = login_all_accounts()
    if not sessions:
        return jsonify({"error": "Login gagal semua akun"}), 500

    search    = request.args.get("search", "")
    limit     = int(request.args.get("limit", 100))
    acc_email = request.args.get("account", "")
    targets   = [s for s in sessions if s["email"] == acc_email] if acc_email else sessions

    all_numbers, errors = [], []
    lock = __import__("threading").Lock()

    def _fetch_one(session):
        email   = session["email"]
        account = _get_account(email)
        if not account:
            return
        try:
            rows, total = _fetch_my_numbers(account, search=search, length=limit)
            result = []
            for row in rows:
                raw_num    = re.sub(r"<[^>]+>","",str(row.get("Number", row.get("number","")))).strip()
                range_name = re.sub(r"<[^>]+>","",str(row.get("range",""))).strip()
                if not raw_num:
                    continue
                result.append({
                    "account":       email,
                    "number_id":     _get_number_id(row),
                    "number":        raw_num,
                    "range_name":    range_name,
                    "rate_a2p":      str(row.get("A2P","")).strip(),
                    "limit_range":   str(row.get("LimitA2P", row.get("Limit_Range",""))).strip(),
                    "limit_did_a2p": str(row.get("limit_did_a2p","")).strip(),
                    "limit_cli_a2p": str(row.get("limit_cli_a2p","")).strip(),
                    "created_at":    str(row.get("created_at","")).strip(),
                })
            with lock:
                all_numbers.extend(result)
            logger.info(f"[MY-LIST] {email}: {len(rows)} nomor (total iVAS: {total})")
        except Exception as e:
            with lock:
                errors.append({"account": email, "error": str(e)})
            logger.error(f"[MY-LIST] Error {email}: {e}")

    with ThreadPoolExecutor(max_workers=len(targets)) as ex:
        list(ex.map(_fetch_one, targets))

    return jsonify({
        "status":        "ok",
        "accounts_ok":   len(targets) - len(errors),
        "accounts_fail": len(errors),
        "total":         len(all_numbers),
        "numbers":       all_numbers,
        "errors":        errors,
    })


# ════════════════════════════════════════════════════════
# /numbers/add — tambah nomor ke SEMUA akun atau akun tertentu
# ════════════════════════════════════════════════════════

@app.route("/numbers/add", methods=["GET","POST"])
def add_number():
    """
    Tambah nomor dari Test Numbers ke My Numbers (re-add from termination).

    CONFIRMED dari discovery: endpoint butuh 'id' (termination_id dari row
    DataTable Test Numbers) + 'range_name', BUKAN nomor telepon langsung.

    Params:
      termination_id : ID dari row Test Numbers (dari /numbers/test-list field number_id)
      range_name     : nama range, misal "PAKISTAN 34"
      number         : (opsional) nomor telepon — dipakai untuk resolve termination_id otomatis
      account        : (opsional) filter ke 1 akun, default: akun pertama
    Contoh:
      /numbers/add?termination_id=82774&range_name=PAKISTAN 34
      /numbers/add?number=923008264692&range_name=PAKISTAN 34
    """
    if request.method == "GET":
        termination_id = request.args.get("termination_id","").strip()
        number         = request.args.get("number","").strip()
        range_name     = request.args.get("range_name","").strip()
        acc_email      = request.args.get("account","").strip()
    else:
        d              = request.get_json(silent=True) or {}
        termination_id = (d.get("termination_id","") or request.form.get("termination_id","")).strip()
        number         = (d.get("number","")         or request.form.get("number","")).strip()
        range_name     = (d.get("range_name","")     or request.form.get("range_name","")).strip()
        acc_email      = (d.get("account","")        or request.form.get("account","")).strip()

    if not termination_id and not number:
        return jsonify({
            "error": "Parameter termination_id (atau number) dan range_name wajib",
            "contoh_1": "/numbers/add?termination_id=82774&range_name=PAKISTAN 34",
            "contoh_2": "/numbers/add?number=923008264692&range_name=PAKISTAN 34",
            "tip": "Cek /numbers/test-list untuk lihat termination_id (field number_id) dan range_name",
        }), 400

    if not range_name:
        return jsonify({
            "error": "Parameter range_name wajib",
            "tip":   "Cek /numbers/test-list untuk lihat range_name yang valid",
        }), 400

    sessions = login_all_accounts()
    if not sessions:
        return jsonify({"error": "Login gagal semua akun"}), 500

    # Pilih 1 akun
    if acc_email:
        target = next((s for s in sessions if s["email"] == acc_email), None)
        if not target:
            return jsonify({"error": f"Akun '{acc_email}' tidak ditemukan"}), 404
    else:
        target = sessions[0]

    email   = target["email"]
    account = _get_account(email)

    # ── Resolve termination_id dari nomor kalau belum ada ────────────────────
    if not termination_id and number:
        rows, _ = _fetch_datatables(
            account, f"{BASE_URL}/portal/numbers/test",
            search=number, length=200
        )
        for row in rows:
            raw_num = re.sub(r"<[^>]+>","",str(row.get("test_number",""))).strip()
            if re.sub(r"\D","",raw_num) == re.sub(r"\D","",number):
                nid = _get_number_id(row)
                if nid:
                    termination_id = nid
                    if not range_name:
                        range_name = re.sub(r"<[^>]+>","",str(row.get("range",""))).strip()
                    break

        if not termination_id:
            return jsonify({
                "status": "error",
                "error":  f"termination_id tidak ditemukan untuk number={number}",
                "tip":    "Cek /numbers/test-list untuk lihat termination_id",
            }), 404

    # ── POST add ─────────────────────────────────────────────────────────────
    # CONFIRMED: POST /portal/numbers/termination/number/add
    #            data: { id: termination_id, range_name: range_name }
    try:
        resp, _ = do_request(
            account, "POST",
            f"{BASE_URL}/portal/numbers/termination/number/add",
            data={"id": termination_id, "range_name": range_name},
            headers={
                "Accept":           "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
                "Referer":          f"{BASE_URL}/portal/numbers/test",
                "Origin":           BASE_URL,
            },
        )
        success, message, raw = _parse_ivas_resp(resp)
        logger.info(f"[ADD] {email}: termination_id={termination_id} success={success} msg={message}")

        return jsonify({
            "status":         "ok" if success else "error",
            "success":        success,
            "message":        message,
            "account":        email,
            "termination_id": termination_id,
            "range_name":     range_name,
            "number":         number or "",
            "http_status":    resp.status_code if resp else None,
        }), 200 if success else 400

    except Exception as e:
        logger.error(f"[ADD] Error: {e}")
        return jsonify({"status":"error","error":str(e)}), 500



# ════════════════════════════════════════════════════════
# /numbers/delete — return number to system (CONFIRMED working)
# ════════════════════════════════════════════════════════

@app.route("/numbers/delete", methods=["GET","POST"])
def delete_number():
    """
    Return/hapus nomor ke sistem dari My Numbers.

    CONFIRMED dari discovery:
      POST /portal/numbers/termination/details
      data: { id: number_id }  ← number_id dari row My Numbers

    Params:
      number_id : ID dari row My Numbers (dari /numbers/my-list field number_id)
      number    : (opsional) nomor telepon — dipakai resolve number_id otomatis
      account   : (opsional) filter 1 akun, default: semua akun
    Contoh:
      /numbers/delete?number_id=3490323892
      /numbers/delete?number=51910550499
      /numbers/delete?number_id=3490323892&account=email@x.com
    """
    if request.method == "GET":
        number_id = request.args.get("number_id","").strip()
        number    = request.args.get("number","").strip()
        acc_email = request.args.get("account","").strip()
    else:
        d         = request.get_json(silent=True) or {}
        number_id = (d.get("number_id","") or request.form.get("number_id","")).strip()
        number    = (d.get("number","")    or request.form.get("number","")).strip()
        acc_email = (d.get("account","")   or request.form.get("account","")).strip()

    if not number_id and not number:
        return jsonify({
            "error":    "Parameter number_id atau number wajib",
            "contoh_1": "/numbers/delete?number_id=3490323892",
            "contoh_2": "/numbers/delete?number=51910550499",
            "tip":      "Cek /numbers/my-list untuk lihat number_id",
        }), 400

    sessions = login_all_accounts()
    if not sessions:
        return jsonify({"error": "Login gagal semua akun"}), 500

    targets = [s for s in sessions if s["email"] == acc_email] if acc_email else sessions
    found_map = {}  # {email: {"number_id": str, "range_name": str}}
    lock = threading.Lock()

    # ── Resolve number_id dari nomor telepon ─────────────────────────────────
    if number_id:
        for s in targets:
            found_map[s["email"]] = {"number_id": number_id, "range_name": ""}
    else:
        def _search(session):
            email   = session["email"]
            account = _get_account(email)
            if not account:
                return
            # Cari di My Numbers
            rows, _ = _fetch_my_numbers(account, search=number, length=500)
            for row in rows:
                raw_num = re.sub(r"<[^>]+>","",str(row.get("Number",row.get("number","")))).strip()
                if re.sub(r"\D","",raw_num) == re.sub(r"\D","",number):
                    nid = _get_number_id(row)
                    if nid:
                        with lock:
                            found_map[email] = {
                                "number_id":  nid,
                                "range_name": re.sub(r"<[^>]+>","",str(row.get("range",""))).strip(),
                            }
                        return
        with ThreadPoolExecutor(max_workers=max(len(targets),1)) as ex:
            list(ex.map(_search, targets))

    if not found_map:
        return jsonify({
            "status": "error",
            "error":  f"number_id tidak ditemukan untuk number={number}. Cek /numbers/my-list",
        }), 404

    # ── POST delete ───────────────────────────────────────────────────────────
    # CONFIRMED: POST /portal/numbers/termination/details  data: { id: number_id }
    results, errors = [], []

    def _delete(session):
        email   = session["email"]
        account = _get_account(email)
        info    = found_map.get(email)
        if not account or not info:
            return
        nid = info["number_id"]
        try:
            resp, _ = do_request(
                account, "POST",
                f"{BASE_URL}/portal/numbers/termination/details",
                data={"id": nid},
                headers={
                    "Accept":           "application/json, text/javascript, */*; q=0.01",
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer":          f"{BASE_URL}/portal/numbers",
                    "Origin":           BASE_URL,
                },
            )
            success, message, raw = _parse_ivas_resp(resp)
            entry = {
                "account":    email,
                "success":    success,
                "number":     number or "",
                "number_id":  nid,
                "range_name": info.get("range_name",""),
                "message":    message,
            }
            with lock:
                (results if success else errors).append(entry)
            logger.info(f"[DELETE] {email}: nid={nid} success={success} msg={message}")
        except Exception as e:
            with lock:
                errors.append({"account":email,"success":False,"number_id":nid,"error":str(e)})

    delete_targets = [s for s in targets if s["email"] in found_map]
    with ThreadPoolExecutor(max_workers=max(len(delete_targets),1)) as ex:
        list(ex.map(_delete, delete_targets))

    return jsonify({
        "status":        "ok" if results else "error",
        "deleted_count": len(results),
        "failed_count":  len(errors),
        "number":        number or number_id,
        "results":       results,
        "errors":        errors,
    })


# ════════════════════════════════════════════════════════
# /numbers/export — export Test Numbers ke file (Excel via iVAS)
# ════════════════════════════════════════════════════════

@app.route("/numbers/export", methods=["GET","POST"])
def numbers_export():
    """
    POST /portal/numbers/test/export  ← CONFIRMED 200 dari discovery
    Download file export Test Numbers langsung dari iVAS.
    Params: account (opsional)
    Contoh: /numbers/export
    """
    acc_email = request.args.get("account","").strip()
    sessions  = login_all_accounts()
    if not sessions:
        return jsonify({"error": "Login gagal"}), 500

    target  = next((s for s in sessions if s["email"] == acc_email), sessions[0])
    account = _get_account(target["email"])

    try:
        resp, _ = do_request(
            account, "POST",
            f"{BASE_URL}/portal/numbers/test/export",
            data={},
            headers={
                "Accept":           "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
                "Referer":          f"{BASE_URL}/portal/numbers/test",
                "Origin":           BASE_URL,
            },
        )
        if resp is None:
            return jsonify({"error": "Request gagal"}), 500

        ct   = resp.headers.get("Content-Type","")
        body = decode_response(resp)

        # iVAS mungkin return URL file atau langsung file
        try:
            j = resp.json()
            # Kalau ada URL download di response
            dl_url = j.get("url") or j.get("file") or j.get("path") or j.get("download")
            if dl_url:
                # Fetch file dari URL tersebut
                file_resp = account  # placeholder
                return jsonify({"status":"ok","message":j.get("message",""),"download_url":dl_url})
            return jsonify({"status":"ok","response":j})
        except Exception:
            # Response langsung file
            if "spreadsheet" in ct or "excel" in ct or "octet-stream" in ct:
                from datetime import datetime as _dt
                ts  = _dt.now().strftime("%Y%m%d_%H%M%S")
                ext = "xlsx" if "spreadsheet" in ct else "bin"
                return Response(
                    resp.content,
                    mimetype=ct,
                    headers={"Content-Disposition": f"attachment; filename=test_numbers_{ts}.{ext}"},
                )
            return jsonify({"status":"ok","body":body[:500],"content_type":ct})

    except Exception as e:
        return jsonify({"error": str(e)}), 500



# ════════════════════════════════════════════════════════
# /numbers/raw-debug — debug SEMUA akun
# ════════════════════════════════════════════════════════

@app.route("/numbers/raw-debug")
def numbers_raw_debug():
    """
    Debug: coba fetch test-numbers dari semua akun.
    Param: account (opsional, filter 1 akun)
    """
    sessions = login_all_accounts()
    if not sessions:
        return jsonify({"error": "Login gagal semua akun"}), 500

    acc_email = request.args.get("account","")
    targets   = [s for s in sessions if s["email"] == acc_email] if acc_email else sessions

    all_results = {}
    lock = __import__("threading").Lock()

    def _debug_one(session):
        email   = session["email"]
        account = _get_account(email)
        if not account:
            return
        res = {}

        # Test 1: plain GET /portal/numbers/test
        try:
            r, _ = do_request(account, "GET", f"{BASE_URL}/portal/numbers/test",
                              headers={"Referer": f"{BASE_URL}/portal/numbers/test"})
            h = decode_response(r) if r else ""
            res["t1_plain_get"] = {"status": r.status_code if r else None, "preview": h[:300]}
        except Exception as e:
            res["t1_plain_get"] = {"error": str(e)}

        # Test 2: DataTables XHR /portal/numbers/test
        try:
            qs = ("draw=1&columns[0][data]=range&columns[0][name]=terminations.range"
                  "&columns[1][data]=test_number&columns[1][name]=terminations.test_number"
                  "&order[0][column]=0&order[0][dir]=asc&start=0&length=5"
                  "&search[value]=&search[regex]=false")
            r, _ = do_request(account, "GET", f"{BASE_URL}/portal/numbers/test?{qs}",
                              headers={"Accept":"application/json, text/javascript, */*; q=0.01",
                                       "X-Requested-With":"XMLHttpRequest",
                                       "Referer": f"{BASE_URL}/portal/numbers/test"})
            h = decode_response(r) if r else ""
            try: parsed = r.json() if r else {}
            except Exception: parsed = {}
            res["t2_test_datatable"] = {
                "status": r.status_code if r else None,
                "recordsTotal": parsed.get("recordsTotal"),
                "data_count": len(parsed.get("data",[])),
                "first_row": (parsed.get("data") or [None])[0],
            }
        except Exception as e:
            res["t2_test_datatable"] = {"error": str(e)}

        # Test 3: My Numbers /portal/numbers
        try:
            qs2 = ("draw=1&columns[0][data]=Number&columns[0][name]=Number"
                   "&columns[1][data]=range&columns[1][name]=range"
                   "&order[0][column]=0&order[0][dir]=asc&start=0&length=5"
                   "&search[value]=&search[regex]=false")
            r, _ = do_request(account, "GET", f"{BASE_URL}/portal/numbers?{qs2}",
                              headers={"Accept":"application/json, text/javascript, */*; q=0.01",
                                       "X-Requested-With":"XMLHttpRequest",
                                       "Referer": f"{BASE_URL}/portal/numbers"})
            h = decode_response(r) if r else ""
            try: parsed = r.json() if r else {}
            except Exception: parsed = {}
            res["t3_my_numbers"] = {
                "status": r.status_code if r else None,
                "recordsTotal": parsed.get("recordsTotal"),
                "data_count": len(parsed.get("data",[])),
                "first_row": (parsed.get("data") or [None])[0],
            }
        except Exception as e:
            res["t3_my_numbers"] = {"error": str(e)}

        with lock:
            all_results[email] = res

    with ThreadPoolExecutor(max_workers=len(targets)) as ex:
        list(ex.map(_debug_one, targets))

    return jsonify({
        "accounts_tested": len(targets),
        "results": all_results,
    })


# ════════════════════════════════════════════════════════
# /numbers/my-list-debug — debug kolom /portal/numbers
# ════════════════════════════════════════════════════════

@app.route("/numbers/my-list-debug")
def numbers_my_list_debug():
    """
    Debug: lihat raw response /portal/numbers dari semua akun.
    Param: account (opsional)
    """
    sessions = login_all_accounts()
    if not sessions:
        return jsonify({"error": "Login gagal"}), 500

    acc_email = request.args.get("account","")
    targets   = [s for s in sessions if s["email"] == acc_email] if acc_email else sessions

    all_results = {}
    lock = __import__("threading").Lock()

    def _debug_one(session):
        email   = session["email"]
        account = _get_account(email)
        if not account:
            return
        res = {}

        for label, col_d, col_n in [
            ("testA_prefix",   "Number", "numbers.Number"),
            ("testB_no_prefix","Number", "Number"),
            ("testC_did",      "did",    "dids.did"),
        ]:
            try:
                qs = (f"draw=1&columns[0][data]={col_d}&columns[0][name]={col_n}"
                      "&columns[1][data]=range&columns[1][name]=range"
                      "&order[0][column]=0&order[0][dir]=asc&start=0&length=5"
                      "&search[value]=&search[regex]=false")
                r, _ = do_request(account, "GET", f"{BASE_URL}/portal/numbers?{qs}",
                                  headers={"Accept":"application/json, text/javascript, */*; q=0.01",
                                           "X-Requested-With":"XMLHttpRequest",
                                           "Referer": f"{BASE_URL}/portal/numbers"})
                try: parsed = r.json() if r else {}
                except Exception: parsed = {}
                res[label] = {
                    "status": r.status_code if r else None,
                    "recordsTotal": parsed.get("recordsTotal"),
                    "data_count": len(parsed.get("data",[])),
                    "first_row": (parsed.get("data") or [None])[0],
                }
            except Exception as e:
                res[label] = {"error": str(e)}

        with lock:
            all_results[email] = res

    with ThreadPoolExecutor(max_workers=len(targets)) as ex:
        list(ex.map(_debug_one, targets))

    return jsonify({
        "accounts_tested": len(targets),
        "results": all_results,
    })




@app.route("/diag")
def diag():
    """
    Diagnosis cepat — jalankan flow step by step dan cetak hasilnya.
    Usage: /diag?date=08/03/2026
    Output: plain text, mudah dibaca di browser.
    """
    from datetime import datetime as _dt

    date_str = request.args.get("date", _dt.now().strftime("%d/%m/%Y"))
    lines = [
        f"=== KY-SHIRO DIAG === {_dt.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Date: {date_str}",
        f"Accounts configured: {len(ACCOUNTS)}",
        "",
    ]

    # Parse date
    try:
        d = _dt.strptime(date_str, "%d/%m/%Y")
        from_date = date_str
        to_date   = date_str
        lines.append(f"✅ Date parsed: {d.strftime('%Y-%m-%d')}")
    except Exception as e:
        lines.append(f"❌ Date parse error: {e}")
        return Response("\n".join(lines), mimetype="text/plain")

    # Test each account
    for acc in ACCOUNTS:
        email = acc["email"]
        lines.append(f"\n{'='*60}")
        lines.append(f"ACCOUNT: {email}")
        lines.append(f"{'='*60}")

        # Step 1: Login
        session = get_session(acc, force=True)
        if not session or not session.get("ok"):
            lines.append(f"❌ LOGIN GAGAL: {session.get('error','?') if session else 'None'}")
            continue
        lines.append(f"✅ LOGIN OK")

        # Step 2: Get ranges
        ranges = get_ranges(acc, from_date, to_date)
        lines.append(f"\n--- RANGES ({len(ranges)}) ---")
        if not ranges:
            lines.append("❌ 0 ranges — cek /debug/ranges-raw")
        else:
            for r in ranges:
                lines.append(f"  ✅ {r['name']}  (id={r['id']})")

            # Step 3: Get numbers per range
            for rng in ranges:
                nums = get_numbers(acc, rng["name"], from_date, to_date, range_id=rng["id"])
                lines.append(f"\n  --- NUMBERS in {rng['name']} ({len(nums)}) ---")
                if not nums:
                    lines.append(f"  ❌ 0 nomor")
                else:
                    for n in nums[:5]:  # max 5
                        num_str = n["number"] if isinstance(n, dict) else str(n)
                        lines.append(f"    ✅ {num_str}")

                        # Step 4: Get SMS
                        msg = get_sms(acc, num_str, rng["name"], from_date, to_date)
                        if msg:
                            lines.append(f"    📨 SMS: {msg[:120]}")
                        else:
                            lines.append(f"    ⚠️  SMS: tidak ada / tidak terdeteksi")

    lines.append(f"\n{'='*60}")
    lines.append("END DIAG")
    lines.append(f"{'='*60}")
    return Response("\n".join(lines), mimetype="text/plain")



# ════════════════════════════════════════════════════════
# /discover — iVAS Endpoint Discovery
# Crawl semua halaman iVAS, extract AJAX URL + payload dari JS/HTML
# ════════════════════════════════════════════════════════

@app.route("/discover")
def discover():
    """
    GET /discover
    Crawl iVAS pages dan extract semua endpoint + payload.
    Params:
      page : scan 1 halaman spesifik (opsional)
      test : 1=jalankan endpoint tests (default:1), 0=skip
      fmt  : json (default) | text
    Contoh:
      /discover
      /discover?fmt=text
      /discover?page=/portal/numbers/test
      /discover?test=0&page=/portal/sms/received
    """
    from datetime import datetime as _dt

    page_filter = request.args.get("page", "")
    run_tests   = request.args.get("test", "1") != "0"
    fmt         = request.args.get("fmt", "json")

    sessions = login_all_accounts()
    if not sessions:
        return jsonify({"error": "Login gagal semua akun"}), 500

    sess_data = sessions[0]
    email     = sess_data["email"]
    scraper   = sess_data["scraper"]
    csrf      = sess_data["csrf"]

    PAGES = [
        "/portal/sms/received",
        "/portal/sms/live",
        "/portal/live/my_sms",
        "/portal/numbers",
        "/portal/numbers/test",
        "/portal/number/test",
        "/portal/number/my",
        "/portal/dashboard",
    ]
    pages_to_scan = [page_filter] if page_filter else PAGES

    # ── Extractor helpers ───────────────────────────────────────────────────

    def _ajax(html, page_url):
        """Extract $.ajax() calls"""
        out = []
        for block in re.findall(r'\$\.ajax\s*\(\s*\{([\s\S]{20,800}?)\}\s*\)', html):
            e = {"source": "$.ajax", "page": page_url}
            m = re.search(r'url\s*:\s*["\']([^"\']+)["\']', block)
            if m: e["url"] = m.group(1)
            m = re.search(r'type\s*:\s*["\'](\w+)["\']', block, re.I)
            e["method"] = m.group(1).upper() if m else "POST"
            # data keys
            m = re.search(r'data\s*:\s*(\{[^{}]{0,500}\})', block)
            if m:
                keys = re.findall(r'["\'](\w[\w_-]*)["\']', m.group(1))
                e["data_keys"] = [k for k in keys if k not in ("_token",)]
                # sample values
                pairs = re.findall(r'["\'](\w[\w_-]*)["\']:\s*["\']([^"\']{0,60})["\']', m.group(1))
                if pairs: e["data_sample"] = dict(pairs)
            if e.get("url"): out.append(e)
        return out

    def _datatables(html, page_url):
        """Extract DataTables AJAX config"""
        out = []
        for block in re.findall(r'DataTable\s*\(\s*\{([\s\S]{20,3000}?)\}\s*\)', html):
            e = {"source": "DataTable", "page": page_url}
            m = re.search(r'ajax\s*:\s*\{[^{}]*url\s*:\s*["\']([^"\']+)["\']', block)
            if not m: m = re.search(r'ajax\s*:\s*["\']([^"\']+)["\']', block)
            if m: e["url"] = m.group(1)
            e["columns"] = re.findall(r'data\s*:\s*["\']([^"\']+)["\']', block)
            m = re.search(r'type\s*:\s*["\'](\w+)["\']', block, re.I)
            e["method"] = m.group(1).upper() if m else "GET"
            if e.get("url"): out.append(e)
        return out

    def _js_funcs(html, page_url):
        """Extract JS functions yang berisi AJAX/portal URLs"""
        out = []
        seen = set()
        # Cari function + body
        pattern = re.compile(
            r'function\s+(\w+)\s*\(([^)]*)\)\s*\{([\s\S]{30,2000}?)\}(?=\s*(?:function|\$|//|var|let|const|<|$))',
            re.MULTILINE
        )
        for m in pattern.finditer(html):
            fname = m.group(1)
            params = [p.strip() for p in m.group(2).split(",") if p.strip()]
            body  = m.group(3)
            if fname in seen: continue
            seen.add(fname)
            if not any(k in body.lower() for k in ("ajax","fetch","xmlhttprequest","/portal/")):
                continue
            urls = list(set(re.findall(r'["\'](/portal/[^"\'?]+|https://www\.ivasms\.com/[^"\'?]+)["\']', body)))
            keys = list(set(re.findall(r'["\'](\w[\w_-]{1,30})["\'](?=\s*:)', body)))
            keys = [k for k in keys if k not in
                    ("url","type","data","method","success","error","dataType",
                     "beforeSend","complete","timeout","async","cache","_token")]
            out.append({
                "source":        "js_function",
                "function_name": fname,
                "params":        params,
                "urls":          urls[:10],
                "data_keys":     keys[:20],
                "page":          page_url,
            })
        return out

    def _onclick(html, page_url):
        """Extract onclick handlers"""
        soup = BeautifulSoup(html, "html.parser")
        out, seen = [], set()
        for el in soup.find_all(onclick=True):
            oc = el.get("onclick","").strip()
            m  = re.match(r'(\w+)\s*\(([^)]*)\)', oc)
            if not m: continue
            fname  = m.group(1)
            params_raw = m.group(2)
            params = [p.strip().strip("'\"") for p in params_raw.split(",") if p.strip()]
            if fname in seen: continue
            seen.add(fname)
            out.append({
                "source":        "onclick",
                "function":      fname,
                "sample_params": params[:6],
                "element_tag":   el.name,
                "element_text":  el.get_text(strip=True)[:60],
            })
        return out

    def _forms(html, page_url):
        """Extract HTML forms"""
        soup = BeautifulSoup(html, "html.parser")
        out  = []
        for form in soup.find_all("form"):
            action = form.get("action","")
            method = form.get("method","GET").upper()
            if action:
                from urllib.parse import urljoin
                action = urljoin(BASE_URL, action)
            inputs = {}
            for inp in form.find_all(["input","select","textarea"]):
                name = inp.get("name","")
                if name and name != "_token":
                    inputs[name] = inp.get("value","")[:60] or inp.get("type","text")
            if inputs or action:
                out.append({"source":"form","url":action or page_url,
                            "method":method,"inputs":inputs})
        return out

    # ── Scan each page ──────────────────────────────────────────────────────
    pages_result = {}
    all_urls = set()

    for path in pages_to_scan:
        url = f"{BASE_URL}{path}" if path.startswith("/") else path
        pdata = {"url": url}
        try:
            resp  = scraper.get(url, timeout=20)
            html  = decode_response(resp)
            pdata["status"]  = resp.status_code
            pdata["length"]  = len(html)
            pdata["final_url"] = resp.url

            if "/login" in resp.url:
                pdata["error"] = "redirect_to_login"
            else:
                ajax  = _ajax(html, url)
                dts   = _datatables(html, url)
                funcs = _js_funcs(html, url)
                ocs   = _onclick(html, url)
                forms = _forms(html, url)

                pdata["ajax_calls"]     = ajax
                pdata["datatable_urls"] = dts
                pdata["js_functions"]   = funcs
                pdata["onclick_funcs"]  = ocs
                pdata["forms"]          = forms
                pdata["summary"] = {
                    "ajax":       len(ajax),
                    "datatables": len(dts),
                    "js_funcs":   len(funcs),
                    "onclick":    len(ocs),
                    "forms":      len(forms),
                }
                for x in ajax + dts:
                    if x.get("url"): all_urls.add(x["url"])
                for fn in funcs:
                    all_urls.update(fn.get("urls",""))

        except Exception as e:
            pdata["error"] = str(e)

        pages_result[path] = pdata

    # ── Known endpoint tests ────────────────────────────────────────────────
    today  = _dt.now()
    date_m = f"{today.month}/{today.day}/{today.year}"
    known_tests = []

    if run_tests:
        def _test(name, method, url, data=None, params=None):
            hdrs = {
                "X-Requested-With": "XMLHttpRequest",
                "Accept":           "application/json, text/javascript, */*; q=0.01",
                "Referer":          BASE_URL,
                "Origin":           BASE_URL,
            }
            d = dict(data or {})
            d["_token"] = csrf
            try:
                if method == "POST":
                    resp = scraper.post(url, data=d, headers=hdrs, timeout=15)
                else:
                    full = f"{url}?{params}" if params else url
                    resp = scraper.get(full, headers=hdrs, timeout=15)
                body = decode_response(resp)
                try:
                    pj    = resp.json()
                    rtype = "json"
                    jkeys = list(pj.keys()) if isinstance(pj,dict) else f"array[{len(pj)}]"
                    preview = ({k:str(v)[:80] for k,v in list(pj.items())[:5]}
                               if isinstance(pj,dict) else pj[:3] if isinstance(pj,list) else str(pj)[:200])
                except Exception:
                    rtype   = "html" if "<html" in body[:200].lower() else "text"
                    jkeys   = None
                    preview = body[:400].strip()
                return {
                    "name": name, "url": url, "method": method,
                    "status": resp.status_code, "response_type": rtype,
                    "json_keys": jkeys, "preview": preview,
                    "redirect_login": "/login" in resp.url,
                    "content_type": resp.headers.get("Content-Type",""),
                }
            except Exception as ex:
                return {"name":name,"url":url,"method":method,"error":str(ex)}

        known_tests = [
            # ── SMS Received ──────────────────────────────────────────────
            _test("SMS Received → Ranges",
                  "POST", f"{BASE_URL}/portal/sms/received/getsms",
                  {"from": date_m, "to": date_m}),

            _test("SMS Received → Numbers in Range",
                  "POST", f"{BASE_URL}/portal/sms/received/getsms/number",
                  {"start": date_m, "end": date_m, "range": "ZIMBABWE 188"}),

            _test("SMS Received → SMS per Number",
                  "POST", f"{BASE_URL}/portal/sms/received/getsms/number/sms",
                  {"start": date_m, "end": date_m, "Number": "263784490048", "Range": "ZIMBABWE 188"}),

            # ── Numbers Test List ─────────────────────────────────────────
            _test("Numbers Test List (DataTable GET)",
                  "GET", f"{BASE_URL}/portal/numbers/test",
                  params=("draw=1&columns[0][data]=range&columns[0][name]=terminations.range"
                          "&columns[1][data]=test_number&columns[1][name]=terminations.test_number"
                          "&start=0&length=5&search[value]=&search[regex]=false")),

            # ── My Numbers ────────────────────────────────────────────────
            _test("My Numbers (DataTable GET)",
                  "GET", f"{BASE_URL}/portal/numbers",
                  params=("draw=1&columns[0][data]=Number&columns[0][name]=Number"
                          "&columns[1][data]=range&columns[1][name]=range"
                          "&start=0&length=5&search[value]=&search[regex]=false")),

            # ── Add Number variants ───────────────────────────────────────
            _test("Add Number v1 (termination/number/add)",
                  "POST", f"{BASE_URL}/portal/numbers/termination/number/add",
                  {"number": "99999999999", "range_name": "PERU 543"}),

            _test("Add Number v2 (number/test/add)",
                  "POST", f"{BASE_URL}/portal/number/test/add",
                  {"number": "99999999999", "range": "PERU 543"}),

            _test("Add Number v3 (range + test_number)",
                  "POST", f"{BASE_URL}/portal/numbers/termination/number/add",
                  {"test_number": "99999999999", "range_name": "PERU 543"}),

            # ── Delete Number variants ────────────────────────────────────
            _test("Delete/Return Number v1 (termination/details)",
                  "POST", f"{BASE_URL}/portal/numbers/termination/details",
                  {"id": "999999999"}),

            _test("Delete/Return Number v2 (number/my/delete)",
                  "POST", f"{BASE_URL}/portal/number/my/delete",
                  {"id": "999999999"}),

            # ── Range details ─────────────────────────────────────────────
            _test("Range Details GET",
                  "GET", f"{BASE_URL}/portal/numbers/termination/range",
                  params="range_name=PERU+543"),

            _test("Range Details POST",
                  "POST", f"{BASE_URL}/portal/numbers/termination/range",
                  {"range_name": "PERU 543"}),

            # ── Bulk return ───────────────────────────────────────────────
            _test("Bulk Return All Numbers",
                  "POST", f"{BASE_URL}/portal/numbers/termination/bulk", {}),

            # ── Live SMS ──────────────────────────────────────────────────
            _test("Live SMS /portal/live/my_sms",
                  "GET", f"{BASE_URL}/portal/live/my_sms"),

            _test("Live SMS /portal/sms/live",
                  "GET", f"{BASE_URL}/portal/sms/live"),
        ]

    report = {
        "timestamp":             _dt.now().isoformat(),
        "account":               email,
        "pages_scanned":         len(pages_result),
        "all_discovered_urls":   sorted(all_urls),
        "pages":                 pages_result,
        "known_endpoint_tests":  known_tests,
    }

    # ── Text format ──────────────────────────────────────────────────────────
    if fmt == "text":
        lines = [
            "=" * 70,
            "  iVAS ENDPOINT DISCOVERY — KY-SHIRO",
            f"  Account : {email}",
            f"  Time    : {_dt.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 70,
            "",
            f"DISCOVERED URLS ({len(all_urls)}):",
        ]
        for u in sorted(all_urls):
            lines.append(f"  {u}")

        lines += ["", "=" * 70, f"KNOWN ENDPOINT TESTS ({len(known_tests)}):", "=" * 70]
        for ep in known_tests:
            status = ep.get("status", "ERR")
            rtype  = ep.get("response_type", "?")
            redir  = " ← REDIRECT LOGIN" if ep.get("redirect_login") else ""
            err    = ep.get("error", "")
            lines.append(f"\n  [{status}] {ep['name']}")
            lines.append(f"    URL    : {ep['url']}")
            lines.append(f"    Method : {ep['method']}")
            if err:
                lines.append(f"    ERROR  : {err}")
            else:
                lines.append(f"    Type   : {rtype}{redir}")
                if ep.get("json_keys"):
                    lines.append(f"    Keys   : {ep['json_keys']}")
                preview = ep.get("preview","")
                if preview:
                    lines.append(f"    Preview: {str(preview)[:200]}")

        lines += ["", "=" * 70, "  DETAIL PER PAGE", "=" * 70]
        for path, pdata in pages_result.items():
            lines.append(f"\n[{pdata.get('status','?')}] {path} ({pdata.get('length',0)} chars)")
            if pdata.get("error"):
                lines.append(f"  ERROR: {pdata['error']}")
                continue
            s = pdata.get("summary",{})
            lines.append(f"  ajax:{s.get('ajax',0)} datatables:{s.get('datatables',0)} "
                         f"js_funcs:{s.get('js_funcs',0)} onclick:{s.get('onclick',0)}")
            for aj in pdata.get("ajax_calls",[]):
                lines.append(f"  $.ajax  [{aj.get('method','?')}] {aj.get('url','')}  data_keys={aj.get('data_keys','')}")
            for dt in pdata.get("datatable_urls",[]):
                lines.append(f"  DataTable [{dt.get('method','?')}] {dt.get('url','')}  cols={dt.get('columns',[])} ")
            for fn in pdata.get("js_functions",[]):
                if fn.get("urls"):
                    lines.append(f"  function {fn['function_name']}({','.join(fn.get('params',[]))}) → {fn['urls']}")
            for oc in pdata.get("onclick_funcs",[])[:5]:
                lines.append(f"  onclick  {oc['function']}({', '.join(repr(p) for p in oc['sample_params'])}) on <{oc['element_tag']}>")

        return Response("\n".join(lines), mimetype="text/plain; charset=utf-8")

    return jsonify(report)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)


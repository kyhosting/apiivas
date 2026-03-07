#Copyright @Arslan-MD
#Updates Channel t.me/arslanmd
#
# ═══════════════════════════════════════════════════════════════
# FIX: Vercel serverless = stateless. Tidak bisa simpan session
# di memory antar request. Solusi: login fresh setiap request /sms
# menggunakan credentials dari environment variable.
# ═══════════════════════════════════════════════════════════════

from flask import Flask, request, jsonify
from datetime import datetime
import cloudscraper
import json
from bs4 import BeautifulSoup
import logging
import os
import gzip
import brotli

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── CREDENTIALS — set di Vercel Environment Variables ──────────
IVASMS_USERNAME = os.getenv("IVASMS_USERNAME", "ceptampan58@gmail.com")
IVASMS_PASSWORD = os.getenv("IVASMS_PASSWORD", "Encep12345")
IVASMS_BASE_URL = "https://www.ivasms.com"
IVASMS_LOGIN_URL = "https://www.ivasms.com/login"


# ════════════════════════════════════════════════════════════════
# CORE: Login fresh + ambil semua OTP dalam satu fungsi
# (stateless — cocok untuk Vercel serverless)
# ════════════════════════════════════════════════════════════════

def build_scraper():
    s = cloudscraper.create_scraper()
    s.headers.update({
        'User-Agent':                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept':                    'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language':           'en-US,en;q=0.9',
        'Accept-Encoding':           'gzip, deflate, br',
        'Connection':                'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest':            'document',
        'Sec-Fetch-Mode':            'navigate',
        'Sec-Fetch-Site':            'none',
        'Sec-Fetch-User':            '?1',
        'Cache-Control':             'max-age=0',
    })
    return s


def decompress_response(response):
    encoding = response.headers.get('Content-Encoding', '').lower()
    content  = response.content
    try:
        if encoding == 'gzip':
            content = gzip.decompress(content)
        elif encoding == 'br':
            content = brotli.decompress(content)
        return content.decode('utf-8', errors='replace')
    except Exception as e:
        logger.error(f"Decompress error: {e}")
        return response.text


def do_login(scraper):
    """
    Login ke ivasms.com dengan username+password.
    Return: csrf_token (str) jika berhasil, None jika gagal.
    """
    try:
        # STEP 1: GET halaman login → ambil _token
        logger.info("[LOGIN] GET halaman login...")
        login_page = scraper.get(IVASMS_LOGIN_URL, timeout=20)
        if login_page.status_code != 200:
            logger.error(f"[LOGIN] Status {login_page.status_code}")
            return None

        soup = BeautifulSoup(login_page.text, 'html.parser')
        token_input = soup.find('input', {'name': '_token'})
        if not token_input:
            logger.error("[LOGIN] _token tidak ditemukan")
            return None
        csrf_token = token_input.get('value')

        # STEP 2: POST form login
        logger.info(f"[LOGIN] POST login sebagai {IVASMS_USERNAME}...")
        login_resp = scraper.post(
            IVASMS_LOGIN_URL,
            data={
                'email':    IVASMS_USERNAME,
                'password': IVASMS_PASSWORD,
                '_token':   csrf_token,
            },
            headers={
                'Content-Type': 'application/x-www-form-urlencoded',
                'Referer':      IVASMS_LOGIN_URL,
                'Origin':       IVASMS_BASE_URL,
            },
            timeout=20,
            allow_redirects=True,
        )

        # STEP 3: Verifikasi tidak balik ke /login
        final_url = login_resp.url
        if '/login' in final_url:
            logger.error("[LOGIN] Gagal — masih di /login. Cek username/password.")
            return None

        # STEP 4: GET portal → ambil CSRF terbaru
        portal_resp = scraper.get(
            f"{IVASMS_BASE_URL}/portal/sms/received",
            timeout=20
        )
        portal_html = decompress_response(portal_resp)
        portal_soup = BeautifulSoup(portal_html, 'html.parser')

        # Prioritas: meta csrf-token → input hidden _token
        meta_csrf  = portal_soup.find('meta', {'name': 'csrf-token'})
        input_csrf = portal_soup.find('input', {'name': '_token'})

        if meta_csrf:
            fresh_csrf = meta_csrf.get('content')
        elif input_csrf:
            fresh_csrf = input_csrf.get('value')
        else:
            fresh_csrf = csrf_token  # fallback ke token login

        if not fresh_csrf:
            logger.error("[LOGIN] Tidak bisa ambil CSRF token dari portal")
            return None

        logger.info(f"[LOGIN] Login berhasil ✓  CSRF: {fresh_csrf[:12]}...")
        return fresh_csrf

    except Exception as e:
        logger.error(f"[LOGIN] Exception: {e}")
        return None


def ajax_headers(referer=None):
    return {
        'Accept':           'text/html, */*; q=0.01',
        'Content-Type':     'application/x-www-form-urlencoded; charset=UTF-8',
        'X-Requested-With': 'XMLHttpRequest',
        'Origin':           IVASMS_BASE_URL,
        'Referer':          referer or f"{IVASMS_BASE_URL}/portal/sms/received",
    }


def get_ranges(scraper, csrf, from_date, to_date):
    """STEP 1: POST /getsms → list range (div.item)"""
    try:
        resp = scraper.post(
            f"{IVASMS_BASE_URL}/portal/sms/received/getsms",
            data={'from': from_date, 'to': to_date, '_token': csrf},
            headers=ajax_headers(),
            timeout=15,
        )
        if resp.status_code != 200:
            logger.error(f"[RANGES] Status {resp.status_code}")
            return []

        soup   = BeautifulSoup(decompress_response(resp), 'html.parser')
        ranges = []
        for item in soup.select("div.item"):
            col = item.select_one(".col-sm-4")
            if col:
                ranges.append(col.text.strip())

        logger.info(f"[RANGES] {len(ranges)} range ditemukan: {ranges}")
        return ranges
    except Exception as e:
        logger.error(f"[RANGES] Error: {e}")
        return []


def get_numbers(scraper, csrf, phone_range, from_date, to_date):
    """STEP 2: POST /getsms/number → list nomor (div.card.card-body)"""
    try:
        resp = scraper.post(
            f"{IVASMS_BASE_URL}/portal/sms/received/getsms/number",
            data={'_token': csrf, 'start': from_date, 'end': to_date, 'range': phone_range},
            headers=ajax_headers(),
            timeout=15,
        )
        if resp.status_code != 200:
            logger.error(f"[NUMBERS] Status {resp.status_code} for {phone_range}")
            return []

        soup    = BeautifulSoup(decompress_response(resp), 'html.parser')
        numbers = []
        for item in soup.select("div.card.card-body"):
            col = item.select_one(".col-sm-4")
            if col:
                num = col.text.strip()
                if num:
                    numbers.append(num)

        logger.info(f"[NUMBERS] {phone_range} → {len(numbers)} nomor")
        return numbers
    except Exception as e:
        logger.error(f"[NUMBERS] Error for {phone_range}: {e}")
        return []


def get_sms(scraper, csrf, phone_number, phone_range, from_date, to_date):
    """STEP 3: POST /getsms/number/sms → isi pesan"""
    try:
        resp = scraper.post(
            f"{IVASMS_BASE_URL}/portal/sms/received/getsms/number/sms",
            data={
                '_token': csrf, 'start': from_date, 'end': to_date,
                'Number': phone_number, 'Range': phone_range,
            },
            headers=ajax_headers(),
            timeout=15,
        )
        if resp.status_code != 200:
            logger.error(f"[SMS] Status {resp.status_code} for {phone_number}")
            return None

        soup = BeautifulSoup(decompress_response(resp), 'html.parser')
        el   = soup.select_one(".col-9.col-sm-6 p")
        msg  = el.text.strip() if el else None
        logger.info(f"[SMS] {phone_number} → {msg}")
        return msg
    except Exception as e:
        logger.error(f"[SMS] Error for {phone_number}: {e}")
        return None


def fetch_all_otp(from_date, to_date, limit=None):
    """
    Login fresh → ambil semua OTP dalam satu sesi.
    Return: list of { range, phone_number, otp_message }
    """
    scraper = build_scraper()

    # Login
    csrf = do_login(scraper)
    if not csrf:
        return None, "Login gagal — cek username/password di env variable"

    # STEP 1: ranges
    ranges = get_ranges(scraper, csrf, from_date, to_date)
    if not ranges:
        return [], None

    all_otp = []

    # STEP 2 & 3: loop ranges → numbers → sms
    for phone_range in ranges:
        numbers = get_numbers(scraper, csrf, phone_range, from_date, to_date)

        for phone_number in numbers:
            if limit is not None and len(all_otp) >= limit:
                logger.info(f"[FETCH] Limit {limit} tercapai, berhenti.")
                return all_otp, None

            msg = get_sms(scraper, csrf, phone_number, phone_range, from_date, to_date)
            if msg:
                all_otp.append({
                    'range':        phone_range,
                    'phone_number': phone_number,
                    'otp_message':  msg,
                })

    logger.info(f"[FETCH] Total OTP terkumpul: {len(all_otp)}")
    return all_otp, None


# ════════════════════════════════════════════════════════════════
# FLASK APP
# ════════════════════════════════════════════════════════════════
app = Flask(__name__)


@app.route('/')
def welcome():
    return jsonify({
        'message':   'Welcome to the IVAS SMS API',
        'status':    'API is alive',
        'note':      'Stateless mode — login fresh setiap request (Vercel compatible)',
        'endpoints': {
            '/sms':    'GET — Params: date=DD/MM/YYYY, limit=N (opsional), to_date=DD/MM/YYYY (opsional)',
            '/health': 'GET — Test login & koneksi ke ivasms',
        }
    })


@app.route('/health')
def health_check():
    """Test login ke ivasms — berguna untuk debugging."""
    scraper = build_scraper()
    csrf    = do_login(scraper)
    if csrf:
        return jsonify({
            'status':  'ok',
            'login':   'success',
            'message': 'Berhasil login ke ivasms.com',
            'csrf_preview': csrf[:12] + '...',
        })
    else:
        return jsonify({
            'status':  'error',
            'login':   'failed',
            'message': 'Gagal login — cek IVASMS_USERNAME dan IVASMS_PASSWORD di env',
        }), 500


@app.route('/sms')
def get_sms_endpoint():
    date_str = request.args.get('date')
    limit    = request.args.get('limit')

    if not date_str:
        return jsonify({'error': 'Parameter date wajib diisi (format: DD/MM/YYYY)'}), 400

    try:
        datetime.strptime(date_str, '%d/%m/%Y')
        from_date = date_str
        to_date   = request.args.get('to_date', '')
        if to_date:
            datetime.strptime(to_date, '%d/%m/%Y')
    except ValueError:
        return jsonify({'error': 'Format tanggal tidak valid. Gunakan DD/MM/YYYY'}), 400

    if limit:
        try:
            limit = int(limit)
            if limit <= 0:
                return jsonify({'error': 'Limit harus integer positif'}), 400
        except ValueError:
            return jsonify({'error': 'Limit harus berupa angka'}), 400
    else:
        limit = None

    logger.info(f"[/sms] from={from_date} to={to_date or 'same'} limit={limit}")

    # Login fresh + fetch OTP dalam satu call
    otp_messages, err = fetch_all_otp(from_date, to_date or from_date, limit)

    if otp_messages is None:
        return jsonify({'error': err or 'Gagal fetch OTP dari ivasms'}), 500

    return jsonify({
        'status':       'success',
        'from_date':    from_date,
        'to_date':      to_date or from_date,
        'limit':        limit if limit is not None else 'Not specified',
        'total':        len(otp_messages),
        'otp_messages': otp_messages,
    })


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)

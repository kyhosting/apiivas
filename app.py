#Copyright @Arslan-MD
#Updates Channel t.me/arslanmd
from flask import Flask, request, jsonify
from datetime import datetime
import cloudscraper
import json
from bs4 import BeautifulSoup
import logging
import os
import gzip
from io import BytesIO
import brotli
import time
import threading

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# CONFIG — Ganti username & password ivasms kamu di sini
#          atau set environment variable IVASMS_USERNAME / IVASMS_PASSWORD
# ═══════════════════════════════════════════════════════════════
IVASMS_USERNAME   = os.getenv("IVASMS_USERNAME", "ceptampan58@gmail.com")
IVASMS_PASSWORD   = os.getenv("IVASMS_PASSWORD", "Encep12345")
COOKIES_FILE      = os.getenv("COOKIES_FILE", "cookies.json")
SESSION_TTL_SECS  = 2 * 60 * 60           # 2 jam
SESSION_REFRESH_BEFORE_SECS = 10 * 60     # refresh 10 menit sebelum expire


class IVASSMSClient:
    def __init__(self):
        self.base_url   = "https://www.ivasms.com"
        self.login_url  = "https://www.ivasms.com/login"
        self.logged_in  = False
        self.csrf_token = None
        self._lock      = threading.Lock()   # thread-safe re-login
        self._login_at  = 0                  # unix timestamp login terakhir (0 = belum)

        self.scraper = self._build_scraper()

    # ──────────────────────────────────────────────────────────
    # HTTP CLIENT
    # ──────────────────────────────────────────────────────────
    def _build_scraper(self):
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

    # ──────────────────────────────────────────────────────────
    # SESSION EXPIRE CHECK
    # ──────────────────────────────────────────────────────────
    def _is_session_expired(self):
        """Return True jika session sudah 2 jam atau belum pernah login."""
        if not self.logged_in or self._login_at == 0:
            return True
        return (time.time() - self._login_at) >= SESSION_TTL_SECS

    # ──────────────────────────────────────────────────────────
    # ENSURE SESSION — dipanggil sebelum setiap request API
    # ──────────────────────────────────────────────────────────
    def ensure_session(self):
        """
        Pastikan session masih valid.
        Jika expired (> 2 jam) → auto re-login dan update cookies.json.
        Thread-safe.
        """
        if not self._is_session_expired():
            return True

        with self._lock:
            # Double-check setelah acquire lock (hindari race condition)
            if not self._is_session_expired():
                return True

            logger.info("[SESSION] Session expired atau belum ada. Melakukan login...")
            return self._do_login()

    # ──────────────────────────────────────────────────────────
    # DO LOGIN — login dengan username + password ke ivasms.com
    # ──────────────────────────────────────────────────────────
    def _do_login(self):
        """
        Login ke ivasms.com pakai username & password.
        Setelah berhasil:
          ✅ Auto-save cookies ke cookies.json
          ✅ Update self._login_at (timestamp login baru)
          ✅ Set self.logged_in = True
        """
        try:
            # STEP A: GET halaman login → ambil CSRF _token
            logger.debug("[LOGIN] GET halaman login...")
            self.scraper = self._build_scraper()   # reset scraper agar cookie bersih
            login_page   = self.scraper.get(self.login_url, timeout=15)

            if login_page.status_code != 200:
                logger.error(f"[LOGIN] Gagal GET login page. Status: {login_page.status_code}")
                return False

            soup = BeautifulSoup(login_page.text, 'html.parser')
            token_input = soup.find('input', {'name': '_token'})
            if not token_input:
                logger.error("[LOGIN] _token tidak ditemukan di halaman login")
                return False

            csrf_token = token_input.get('value')
            logger.debug(f"[LOGIN] _token: {csrf_token[:12]}...")

            # STEP B: POST form login
            logger.debug(f"[LOGIN] POST login sebagai {IVASMS_USERNAME}...")
            login_resp = self.scraper.post(
                self.login_url,
                data={
                    'email':    IVASMS_USERNAME,
                    'password': IVASMS_PASSWORD,
                    '_token':   csrf_token,
                },
                headers={
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'Referer':      self.login_url,
                    'Origin':       self.base_url,
                },
                timeout=15,
                allow_redirects=True,
            )

            # STEP C: Verifikasi — pastikan tidak balik ke /login lagi
            final_url = login_resp.url
            if '/login' in final_url:
                logger.error("[LOGIN] Login GAGAL — masih redirect ke /login. Cek username/password.")
                return False

            logger.info(f"[LOGIN] Redirect sukses ke: {final_url}")

            # STEP D: GET portal → ambil CSRF terbaru
            portal_resp = self.scraper.get(
                f"{self.base_url}/portal/sms/received",
                timeout=15
            )
            portal_html = self.decompress_response(portal_resp)
            portal_soup = BeautifulSoup(portal_html, 'html.parser')

            # Prioritas: meta csrf-token, lalu input hidden _token
            meta_csrf = portal_soup.find('meta', {'name': 'csrf-token'})
            if meta_csrf:
                self.csrf_token = meta_csrf.get('content')
            else:
                input_csrf = portal_soup.find('input', {'name': '_token'})
                self.csrf_token = input_csrf.get('value') if input_csrf else csrf_token

            logger.info(f"[LOGIN] Login berhasil ✓  CSRF: {self.csrf_token[:12]}...")

            # STEP E: Simpan cookies ke cookies.json
            self._save_cookies()

            # Update state session
            self.logged_in = True
            self._login_at = time.time()
            return True

        except Exception as e:
            logger.error(f"[LOGIN] Exception: {e}")
            return False

    # ──────────────────────────────────────────────────────────
    # SAVE COOKIES
    # ──────────────────────────────────────────────────────────
    def _save_cookies(self):
        """Ambil cookies dari scraper session → simpan ke cookies.json"""
        try:
            cookie_dict = {}
            for cookie in self.scraper.cookies:
                cookie_dict[cookie.name] = cookie.value

            with open(COOKIES_FILE, 'w') as f:
                json.dump(cookie_dict, f, indent=2)

            login_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            logger.info(f"[COOKIES] {len(cookie_dict)} cookies disimpan ke {COOKIES_FILE} ({login_time})")
            return True
        except Exception as e:
            logger.error(f"[COOKIES] Gagal simpan: {e}")
            return False

    # ──────────────────────────────────────────────────────────
    # LOAD COOKIES (dari file / env)
    # ──────────────────────────────────────────────────────────
    def load_cookies(self, file_path=None):
        """Muat cookies dari COOKIES_JSON env atau file cookies.json."""
        file_path = file_path or COOKIES_FILE
        try:
            if os.getenv("COOKIES_JSON"):
                cookies_raw = json.loads(os.getenv("COOKIES_JSON"))
                logger.debug("[COOKIES] Loaded from env COOKIES_JSON")
            else:
                with open(file_path, 'r') as f:
                    cookies_raw = json.load(f)
                logger.debug(f"[COOKIES] Loaded from {file_path}")

            if isinstance(cookies_raw, dict):
                return cookies_raw
            elif isinstance(cookies_raw, list):
                return {c['name']: c['value'] for c in cookies_raw if 'name' in c and 'value' in c}
            else:
                raise ValueError("Format cookies tidak didukung.")
        except FileNotFoundError:
            logger.warning(f"[COOKIES] {file_path} tidak ditemukan — akan login ulang")
            return None
        except json.JSONDecodeError:
            logger.error("[COOKIES] JSON cookies tidak valid")
            return None
        except Exception as e:
            logger.error(f"[COOKIES] Error: {e}")
            return None

    # ──────────────────────────────────────────────────────────
    # LOGIN WITH COOKIES — coba cookies dulu, fallback ke login
    # ──────────────────────────────────────────────────────────
    def login_with_cookies(self, cookies_file=None):
        """
        Inisialisasi session:
        1. Coba login pakai cookies tersimpan
        2. Jika gagal/expired → login ulang dengan username+password
        3. Cookies baru otomatis disimpan ke cookies.json
        """
        logger.info("[SESSION] Inisialisasi session...")

        cookies = self.load_cookies(cookies_file)
        if cookies:
            logger.debug("[SESSION] Mencoba login dengan cookies lama...")
            for name, value in cookies.items():
                self.scraper.cookies.set(name, value, domain="www.ivasms.com")

            try:
                resp = self.scraper.get(f"{self.base_url}/portal/sms/received", timeout=10)
                if resp.status_code == 200:
                    html = self.decompress_response(resp)
                    soup = BeautifulSoup(html, 'html.parser')

                    meta_csrf  = soup.find('meta', {'name': 'csrf-token'})
                    csrf_input = soup.find('input', {'name': '_token'})

                    if meta_csrf or csrf_input:
                        self.csrf_token = (
                            meta_csrf.get('content') if meta_csrf
                            else csrf_input.get('value')
                        )
                        self.logged_in = True
                        self._login_at = time.time()
                        logger.info("[SESSION] Login via cookies berhasil ✓")
                        return True

                logger.warning("[SESSION] Cookies sudah tidak valid / expired")
            except Exception as e:
                logger.error(f"[SESSION] Error saat coba cookies: {e}")

        # Fallback: login dengan username + password
        logger.info("[SESSION] Fallback → login dengan username & password...")
        return self._do_login()

    # ──────────────────────────────────────────────────────────
    # DECOMPRESS
    # ──────────────────────────────────────────────────────────
    def decompress_response(self, response):
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

    # ──────────────────────────────────────────────────────────
    # CHECK OTPs
    # ──────────────────────────────────────────────────────────
    def check_otps(self, from_date="", to_date=""):
        if not self.ensure_session():
            logger.error("[OTP] Gagal ensure session")
            return None

        logger.debug(f"[OTP] Checking: {from_date} → {to_date}")
        try:
            payload = {'from': from_date, 'to': to_date, '_token': self.csrf_token}
            headers = {
                'Accept':           'text/html, */*; q=0.01',
                'Content-Type':     'application/x-www-form-urlencoded; charset=UTF-8',
                'X-Requested-With': 'XMLHttpRequest',
                'Origin':           self.base_url,
                'Referer':          f"{self.base_url}/portal/sms/received",
            }

            response = self.scraper.post(
                f"{self.base_url}/portal/sms/received/getsms",
                data=payload, headers=headers, timeout=10
            )

            if response.status_code == 200:
                html_content = self.decompress_response(response)
                soup         = BeautifulSoup(html_content, 'html.parser')

                count_sms   = soup.select_one("#CountSMS").text   if soup.select_one("#CountSMS")   else '0'
                paid_sms    = soup.select_one("#PaidSMS").text    if soup.select_one("#PaidSMS")    else '0'
                unpaid_sms  = soup.select_one("#UnpaidSMS").text  if soup.select_one("#UnpaidSMS")  else '0'
                revenue_sms = (soup.select_one("#RevenueSMS").text.replace(' USD', '')
                               if soup.select_one("#RevenueSMS") else '0')

                sms_details = []
                for item in soup.select("div.item"):
                    country_number = item.select_one(".col-sm-4").text.strip()
                    count   = item.select_one(".col-3:nth-child(2) p").text.strip()
                    paid    = item.select_one(".col-3:nth-child(3) p").text.strip()
                    unpaid  = item.select_one(".col-3:nth-child(4) p").text.strip()
                    revenue = item.select_one(".col-3:nth-child(5) p span.currency_cdr").text.strip()
                    sms_details.append({
                        'country_number': country_number,
                        'count': count, 'paid': paid,
                        'unpaid': unpaid, 'revenue': revenue
                    })

                result = {
                    'count_sms': count_sms, 'paid_sms': paid_sms,
                    'unpaid_sms': unpaid_sms, 'revenue': revenue_sms,
                    'sms_details': sms_details, 'raw_response': html_content,
                }
                logger.debug(f"[OTP] {len(sms_details)} records")
                return result

            logger.error(f"[OTP] Status {response.status_code}")
            return None
        except Exception as e:
            logger.error(f"[OTP] Error: {e}")
            return None

    # ──────────────────────────────────────────────────────────
    # GET SMS DETAILS
    # ──────────────────────────────────────────────────────────
    def get_sms_details(self, phone_range, from_date="", to_date=""):
        if not self.ensure_session():
            return None

        try:
            payload = {'_token': self.csrf_token, 'start': from_date, 'end': to_date, 'range': phone_range}
            headers = {
                'Accept': 'text/html, */*; q=0.01',
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'X-Requested-With': 'XMLHttpRequest',
                'Origin': self.base_url,
                'Referer': f"{self.base_url}/portal/sms/received",
            }

            response = self.scraper.post(
                f"{self.base_url}/portal/sms/received/getsms/number",
                data=payload, headers=headers, timeout=10
            )

            if response.status_code == 200:
                html_content   = self.decompress_response(response)
                soup           = BeautifulSoup(html_content, 'html.parser')
                number_details = []

                for item in soup.select("div.card.card-body"):
                    phone_number = item.select_one(".col-sm-4").text.strip()
                    cols    = item.select(".col-3")
                    count   = cols[0].find('p').text.strip()   if len(cols) > 0 else '0'
                    paid    = cols[1].find('p').text.strip()   if len(cols) > 1 else '0'
                    unpaid  = cols[2].find('p').text.strip()   if len(cols) > 2 else '0'
                    revenue_el = cols[3].find('p').find('span', class_='currency_cdr') if len(cols) > 3 else None
                    revenue = revenue_el.text.strip() if revenue_el else '0'
                    onclick   = item.select_one(".col-sm-4").get('onclick', '')
                    id_number = onclick.split("'")[3] if onclick else ''

                    number_details.append({
                        'phone_number': phone_number, 'count': count,
                        'paid': paid, 'unpaid': unpaid,
                        'revenue': revenue, 'id_number': id_number
                    })

                logger.debug(f"[SMSD] {len(number_details)} numbers for {phone_range}")
                return number_details

            logger.error(f"[SMSD] Status {response.status_code} for {phone_range}")
            return None
        except Exception as e:
            logger.error(f"[SMSD] Error for {phone_range}: {e}")
            return None

    # ──────────────────────────────────────────────────────────
    # GET OTP MESSAGE
    # ──────────────────────────────────────────────────────────
    def get_otp_message(self, phone_number, phone_range, from_date="", to_date=""):
        if not self.ensure_session():
            return None

        try:
            payload = {
                '_token': self.csrf_token, 'start': from_date, 'end': to_date,
                'Number': phone_number, 'Range': phone_range
            }
            headers = {
                'Accept': 'text/html, */*; q=0.01',
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'X-Requested-With': 'XMLHttpRequest',
                'Origin': self.base_url,
                'Referer': f"{self.base_url}/portal/sms/received",
            }

            response = self.scraper.post(
                f"{self.base_url}/portal/sms/received/getsms/number/sms",
                data=payload, headers=headers, timeout=10
            )

            if response.status_code == 200:
                html_content = self.decompress_response(response)
                soup         = BeautifulSoup(html_content, 'html.parser')
                el = soup.select_one(".col-9.col-sm-6 p")
                return el.text.strip() if el else None

            logger.error(f"[MSG] Status {response.status_code} for {phone_number}")
            return None
        except Exception as e:
            logger.error(f"[MSG] Error for {phone_number}: {e}")
            return None

    # ──────────────────────────────────────────────────────────
    # GET ALL OTP MESSAGES
    # ──────────────────────────────────────────────────────────
    def get_all_otp_messages(self, sms_details, from_date="", to_date="", limit=None):
        all_otp_messages = []

        for detail in sms_details:
            phone_range    = detail['country_number']
            number_details = self.get_sms_details(phone_range, from_date, to_date)

            if number_details:
                for number_detail in number_details:
                    if limit is not None and len(all_otp_messages) >= limit:
                        logger.debug(f"Reached limit {limit}, stopping")
                        return all_otp_messages

                    phone_number = number_detail['phone_number']
                    otp_message  = self.get_otp_message(phone_number, phone_range, from_date, to_date)

                    if otp_message:
                        all_otp_messages.append({
                            'range':        phone_range,
                            'phone_number': phone_number,
                            'otp_message':  otp_message,
                        })
            else:
                logger.warning(f"No number details for range: {phone_range}")

        logger.debug(f"Collected {len(all_otp_messages)} OTP messages")
        return all_otp_messages

    # ──────────────────────────────────────────────────────────
    # SESSION INFO
    # ──────────────────────────────────────────────────────────
    def session_info(self):
        if not self.logged_in or self._login_at == 0:
            return {'status': 'not_logged_in', 'expires_in_secs': 0}

        elapsed   = time.time() - self._login_at
        remaining = max(0, SESSION_TTL_SECS - elapsed)
        return {
            'status':          'active' if remaining > 0 else 'expired',
            'logged_in_secs':  int(elapsed),
            'expires_in_secs': int(remaining),
            'expires_in_mins': round(remaining / 60, 1),
            'login_at':        datetime.fromtimestamp(self._login_at).strftime('%Y-%m-%d %H:%M:%S'),
        }


# ═══════════════════════════════════════════════════════════════
# FLASK APP
# ═══════════════════════════════════════════════════════════════
app    = Flask(__name__)
client = IVASSMSClient()

with app.app_context():
    # Startup: coba pakai cookies lama, fallback ke login langsung
    if not client.login_with_cookies():
        logger.error("[APP] Gagal inisialisasi session saat startup — pastikan credentials benar di config")


@app.route('/')
def welcome():
    return jsonify({
        'message': 'Welcome to the IVAS SMS API',
        'status':  'API is alive',
        'session': client.session_info(),
        'endpoints': {
            '/sms':     'GET — OTP messages. Params: date=DD/MM/YYYY, limit=N (opsional), to_date=DD/MM/YYYY (opsional)',
            '/session': 'GET — Status & info session aktif',
            '/relogin': 'POST — Force re-login manual, update cookies.json',
        }
    })


@app.route('/session')
def session_status():
    """Lihat status session: kapan login, berapa lama lagi expire, info cookies."""
    info = client.session_info()

    cookies_info = {}
    try:
        with open(COOKIES_FILE, 'r') as f:
            ck = json.load(f)
        cookies_info = {
            'file':   COOKIES_FILE,
            'exists': True,
            'count':  len(ck),
            'keys':   list(ck.keys()),
        }
    except FileNotFoundError:
        cookies_info = {'file': COOKIES_FILE, 'exists': False}
    except Exception as e:
        cookies_info = {'file': COOKIES_FILE, 'error': str(e)}

    return jsonify({
        'session': info,
        'cookies': cookies_info,
        'config': {
            'username':          IVASMS_USERNAME,
            'session_ttl_hours': SESSION_TTL_SECS / 3600,
            'cookies_file':      COOKIES_FILE,
        }
    })


@app.route('/relogin', methods=['POST'])
def force_relogin():
    """Force re-login manual — update cookies.json dengan session baru."""
    logger.info("[RELOGIN] Force re-login diminta...")

    # Reset state supaya trigger login ulang
    client.logged_in = False
    client._login_at = 0

    if client._do_login():
        return jsonify({
            'status':  'success',
            'message': 'Re-login berhasil. cookies.json sudah diupdate.',
            'session': client.session_info(),
        })
    else:
        return jsonify({
            'status':  'error',
            'message': 'Re-login gagal. Cek log server untuk detail.',
        }), 500


@app.route('/sms')
def get_sms():
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

    # Auto-ensure session (re-login + update cookies jika expired)
    if not client.ensure_session():
        return jsonify({'error': 'Gagal autentikasi ke ivasms — cek credentials di config'}), 401

    logger.debug(f"[/sms] from={from_date} to={to_date or 'empty'} limit={limit}")
    result = client.check_otps(from_date=from_date, to_date=to_date)

    if not result:
        return jsonify({'error': 'Gagal fetch data OTP dari ivasms'}), 500

    otp_messages = client.get_all_otp_messages(
        result.get('sms_details', []),
        from_date=from_date,
        to_date=to_date,
        limit=limit
    )

    return jsonify({
        'status':    'success',
        'from_date': from_date,
        'to_date':   to_date or 'Not specified',
        'limit':     limit if limit is not None else 'Not specified',
        'session':   client.session_info(),
        'sms_stats': {
            'count_sms':  result['count_sms'],
            'paid_sms':   result['paid_sms'],
            'unpaid_sms': result['unpaid_sms'],
            'revenue':    result['revenue'],
        },
        'otp_messages': otp_messages,
    })


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)

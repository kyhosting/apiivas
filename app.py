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
import brotli
import re

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

IVASMS_USERNAME  = os.getenv("IVASMS_USERNAME", "ceptampan58@gmail.com")
IVASMS_PASSWORD  = os.getenv("IVASMS_PASSWORD", "Encep12345")
IVASMS_BASE_URL  = "https://www.ivasms.com"
IVASMS_LOGIN_URL = "https://www.ivasms.com/login"
IVASMS_LIVE_URL  = "https://www.ivasms.com/portal/live/my_sms"
IVASMS_RECV_URL  = "https://www.ivasms.com/portal/sms/received"


def to_ivas_date(date_str):
    """
    Konversi dari input user DD/MM/YYYY → format ivasms M/D/YYYY
    Contoh: "07/03/2026" → "3/7/2026"
    """
    try:
        d = datetime.strptime(date_str, '%d/%m/%Y')
        return f"{d.month}/{d.day}/{d.year}"
    except:
        return date_str  # fallback: kembalikan apa adanya


# ════════════════════════════════════════════════════════════════
# HTTP
# ════════════════════════════════════════════════════════════════
def build_scraper():
    s = cloudscraper.create_scraper()
    s.headers.update({
        'User-Agent':                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept':                    'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language':           'en-US,en;q=0.9',
        'Accept-Encoding':           'gzip, deflate, br',
        'Connection':                'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    })
    return s


def decompress_response(response):
    enc     = response.headers.get('Content-Encoding', '').lower()
    content = response.content
    try:
        if enc == 'gzip':
            content = gzip.decompress(content)
        elif enc == 'br':
            content = brotli.decompress(content)
        return content.decode('utf-8', errors='replace')
    except:
        return response.text


def ajax_headers(referer=None):
    return {
        'Accept':           'text/html, */*; q=0.01',
        'Content-Type':     'application/x-www-form-urlencoded; charset=UTF-8',
        'X-Requested-With': 'XMLHttpRequest',
        'Origin':           IVASMS_BASE_URL,
        'Referer':          referer or IVASMS_RECV_URL,
    }


# ════════════════════════════════════════════════════════════════
# LOGIN
# ════════════════════════════════════════════════════════════════
def do_login(scraper):
    """Return (csrf, scraper, live_html) atau None."""
    try:
        login_page = scraper.get(IVASMS_LOGIN_URL, timeout=20)
        soup       = BeautifulSoup(login_page.text, 'html.parser')
        tok_el     = soup.find('input', {'name': '_token'})
        if not tok_el:
            logger.error("[LOGIN] _token tidak ada")
            return None
        tok = tok_el['value']

        resp = scraper.post(
            IVASMS_LOGIN_URL,
            data={'email': IVASMS_USERNAME, 'password': IVASMS_PASSWORD, '_token': tok},
            headers={'Content-Type': 'application/x-www-form-urlencoded',
                     'Referer': IVASMS_LOGIN_URL, 'Origin': IVASMS_BASE_URL},
            timeout=20, allow_redirects=True,
        )
        if '/login' in resp.url:
            logger.error("[LOGIN] Gagal — balik ke /login")
            return None

        portal = scraper.get(IVASMS_LIVE_URL, timeout=20)
        html   = decompress_response(portal)
        psoup  = BeautifulSoup(html, 'html.parser')

        meta  = psoup.find('meta', {'name': 'csrf-token'})
        inp   = psoup.find('input', {'name': '_token'})
        csrf  = (meta['content'] if meta else (inp['value'] if inp else tok))

        logger.info(f"[LOGIN] OK  csrf={csrf[:12]}...")
        return csrf, scraper, html

    except Exception as e:
        logger.error(f"[LOGIN] Exception: {e}")
        return None


# ════════════════════════════════════════════════════════════════
# LIVE SMS PARSER
# Dari screenshot: tabel dengan kolom Live SMS | SID | Paid | Limit | Message content
# Kolom 0 berisi "RANGE\nNOMOR", kolom 1 SID, kolom terakhir pesan
# ════════════════════════════════════════════════════════════════
def parse_live_sms(html):
    soup    = BeautifulSoup(html, 'html.parser')
    results = []

    for tbl in soup.find_all('table'):
        ths = [th.get_text(strip=True).lower() for th in tbl.find_all('th')]
        if not any(h in ('message content', 'sid', 'live sms') or 'message' in h for h in ths):
            continue

        for row in tbl.find_all('tr'):
            tds = row.find_all('td')
            if len(tds) < 3:
                continue

            raw0     = tds[0].get_text(separator='\n', strip=True)
            sid      = tds[1].get_text(strip=True) if len(tds) > 1 else ''
            msg_text = tds[-1].get_text(strip=True)

            if not raw0 or not msg_text or len(msg_text) < 4:
                continue
            if re.match(r'^(live sms|sid|range|sender|message|time)', raw0, re.I):
                continue

            lines  = [l.strip() for l in raw0.split('\n') if l.strip()]
            range_ = lines[0] if lines else ''
            number = ''
            for l in lines[1:]:
                d = re.sub(r'\D', '', l)
                if len(d) >= 8:
                    number = d
                    break
            if not number:
                m = re.search(r'(\d{8,15})', raw0)
                if m:
                    number = m.group(1)

            if not range_:
                continue

            results.append({
                'range':        range_,
                'phone_number': number,
                'otp_message':  msg_text,
                'sid':          sid,
                'source':       'live',
            })

    logger.info(f"[LIVE] {len(results)} SMS")
    return results


# ════════════════════════════════════════════════════════════════
# SMS RECEIVED — 3 level AJAX
#
# Dari screenshot struktur asli:
#   Level 1 → POST /getsms → tabel dengan kolom RANGE|COUNT|PAID|UNPAID|REVENUE
#             Setiap baris bisa di-expand (onclick)
#             RANGE berisi teks "ZIMBABWE 188" dll
#
#   Level 2 → POST /getsms/number?range=... → tabel dengan nomor telepon
#             Nomor tampil dengan ikon telepon, misal "263784490048"
#
#   Level 3 → POST /getsms/number/sms → tabel SENDER|MESSAGE|TIME|REVENUE
#             Sender = badge "WhatsApp" dll
#             Message = isi SMS lengkap
# ════════════════════════════════════════════════════════════════

def get_ranges_received(scraper, csrf, from_date, to_date):
    """
    POST /portal/sms/received/getsms
    Return: list of range strings misal ["ZIMBABWE 188", "TOGO 106"]
    """
    try:
        resp = scraper.post(
            f"{IVASMS_BASE_URL}/portal/sms/received/getsms",
            data={'from': to_ivas_date(from_date), 'to': to_ivas_date(to_date), '_token': csrf},
            headers=ajax_headers(),
            timeout=15,
        )
        if resp.status_code != 200:
            logger.error(f"[RANGES] Status {resp.status_code}")
            return []

        html = decompress_response(resp)
        soup = BeautifulSoup(html, 'html.parser')
        ranges = []

        # Coba selector dari HTML asli ivasms — berbagai kemungkinan struktur
        # Prioritas 1: div.item dengan .col-sm-4
        for item in soup.select('div.item'):
            col = item.select_one('.col-sm-4')
            if col:
                txt = col.get_text(strip=True)
                if txt and not txt.lower().startswith('range'):
                    ranges.append(txt)

        # Prioritas 2: tr di tabel dengan onclick getDetials
        if not ranges:
            for el in soup.find_all(onclick=True):
                oc = el.get('onclick', '')
                m  = re.search(r"getDetials\s*\(\s*['\"]([^'\"]+)['\"]", oc)
                if m:
                    ranges.append(m.group(1).strip())

        # Prioritas 3: ambil dari tabel — kolom pertama yang berisi nama negara
        if not ranges:
            for row in soup.select('table tbody tr, table tr'):
                tds = row.find_all('td')
                if not tds:
                    continue
                txt = tds[0].get_text(strip=True)
                # Format "ZIMBABWE 188" = huruf besar + spasi + angka
                if re.match(r'^[A-Z][A-Z\s]+\d+$', txt):
                    ranges.append(txt)

        logger.info(f"[RANGES] {len(ranges)} ranges: {ranges}")
        return ranges

    except Exception as e:
        logger.error(f"[RANGES] Error: {e}")
        return []


def get_numbers_received(scraper, csrf, phone_range, from_date, to_date):
    """
    POST /portal/sms/received/getsms/number
    Return: list of phone number strings misal ["263784490048"]
    Dari screenshot: nomor tampil di tabel dengan ikon telepon
    """
    try:
        resp = scraper.post(
            f"{IVASMS_BASE_URL}/portal/sms/received/getsms/number",
            data={'_token': csrf, 'start': to_ivas_date(from_date), 'end': to_ivas_date(to_date), 'range': phone_range},
            headers=ajax_headers(),
            timeout=15,
        )
        if resp.status_code != 200:
            logger.error(f"[NUMBERS] Status {resp.status_code} for {phone_range}")
            return []

        html = decompress_response(resp)
        soup = BeautifulSoup(html, 'html.parser')
        numbers = []

        # Prioritas 1: div.card.card-body (original selector)
        for item in soup.select('div.card.card-body'):
            col = item.select_one('.col-sm-4')
            if col:
                # Ambil hanya digit (hapus ikon/teks non-digit)
                txt = col.get_text(strip=True)
                digits = re.sub(r'\D', '', txt)
                if len(digits) >= 8:
                    numbers.append(digits)

        # Prioritas 2: onclick getDetialsNumber
        if not numbers:
            for el in soup.find_all(onclick=True):
                oc = el.get('onclick', '')
                m  = re.search(r"getDetialsNumber\s*\(\s*['\"]?(\d{7,15})['\"]?", oc)
                if m:
                    numbers.append(m.group(1))

        # Prioritas 3: scan tabel — cari cell berisi nomor telepon (8-15 digit)
        if not numbers:
            for row in soup.select('table tbody tr, table tr'):
                tds = row.find_all('td')
                if not tds:
                    continue
                for td in tds:
                    txt = td.get_text(strip=True)
                    # Bersihkan non-digit, cek panjang
                    digits = re.sub(r'\D', '', txt)
                    if 8 <= len(digits) <= 15 and digits not in numbers:
                        numbers.append(digits)

        # Prioritas 4: span/div yang teks-nya semua digit
        if not numbers:
            for el in soup.find_all(['span', 'div', 'td', 'p']):
                if el.find_all(True):  # skip jika punya children
                    continue
                txt    = el.get_text(strip=True)
                digits = re.sub(r'\D', '', txt)
                if 8 <= len(digits) <= 15 and digits not in numbers:
                    numbers.append(digits)

        logger.info(f"[NUMBERS] {phone_range} → {len(numbers)} nomor: {numbers}")
        return numbers

    except Exception as e:
        logger.error(f"[NUMBERS] Error for {phone_range}: {e}")
        return []


def get_sms_received(scraper, csrf, phone_number, phone_range, from_date, to_date):
    """
    POST /portal/sms/received/getsms/number/sms
    Return: isi SMS (string) atau None
    Dari screenshot: tabel SENDER | MESSAGE | TIME | REVENUE
    MESSAGE ada di kolom kedua
    """
    try:
        resp = scraper.post(
            f"{IVASMS_BASE_URL}/portal/sms/received/getsms/number/sms",
            data={
                '_token': csrf,
                'start':  to_ivas_date(from_date),
                'end':    to_ivas_date(to_date),
                'Number': phone_number,
                'Range':  phone_range,
            },
            headers=ajax_headers(),
            timeout=15,
        )
        if resp.status_code != 200:
            logger.error(f"[SMS] Status {resp.status_code} for {phone_number}")
            return None

        html = decompress_response(resp)
        soup = BeautifulSoup(html, 'html.parser')

        # Prioritas 1: selector original
        el = soup.select_one('.col-9.col-sm-6 p')
        if el:
            return el.get_text(strip=True)

        # Prioritas 2: dari tabel SENDER|MESSAGE|TIME|REVENUE (sesuai screenshot)
        # Cari tabel dengan header MESSAGE atau SENDER
        for tbl in soup.find_all('table'):
            ths = [th.get_text(strip=True).lower() for th in tbl.find_all('th')]
            if not any(h in ('message', 'sender', 'content') for h in ths):
                continue

            # Cari index kolom MESSAGE
            msg_idx = next(
                (i for i, h in enumerate(ths) if 'message' in h or 'content' in h),
                1  # default kolom ke-2
            )

            for row in tbl.find_all('tr'):
                tds = row.find_all('td')
                if len(tds) <= msg_idx:
                    continue
                txt = tds[msg_idx].get_text(strip=True)
                if txt and len(txt) > 5:
                    return txt

        # Prioritas 3: cari p atau div yang teksnya panjang (isi SMS)
        for tag in ['p', 'div', 'span', 'td']:
            for el in soup.find_all(tag):
                if el.find_all(True):
                    continue
                txt = el.get_text(strip=True)
                # SMS biasanya > 10 karakter dan mengandung angka
                if len(txt) > 10 and re.search(r'\d{4,}', txt):
                    return txt

        logger.warning(f"[SMS] Tidak ada pesan untuk {phone_number}")
        return None

    except Exception as e:
        logger.error(f"[SMS] Error for {phone_number}: {e}")
        return None


def fetch_received_sms(scraper, csrf, from_date, to_date, limit=None):
    ranges  = get_ranges_received(scraper, csrf, from_date, to_date)
    results = []

    for phone_range in ranges:
        numbers = get_numbers_received(scraper, csrf, phone_range, from_date, to_date)
        for phone_number in numbers:
            if limit and len(results) >= limit:
                return results
            msg = get_sms_received(scraper, csrf, phone_number, phone_range, from_date, to_date)
            if msg:
                results.append({
                    'range':        phone_range,
                    'phone_number': phone_number,
                    'otp_message':  msg,
                    'source':       'received',
                })

    logger.info(f"[RECV] Total: {len(results)} SMS")
    return results


# ════════════════════════════════════════════════════════════════
# MAIN FETCH
# ════════════════════════════════════════════════════════════════
def fetch_all_otp(from_date, to_date, limit=None, mode='both'):
    scraper = build_scraper()
    result  = do_login(scraper)
    if not result:
        return None, "Login gagal — cek IVASMS_USERNAME dan IVASMS_PASSWORD"

    csrf, scraper, live_html = result
    all_otp = []

    if mode in ('live', 'both'):
        live_sms = parse_live_sms(live_html)
        logger.info(f"[MAIN] Live: {len(live_sms)}")
        all_otp.extend(live_sms)

    if mode in ('received', 'both'):
        recv_sms = fetch_received_sms(scraper, csrf, from_date, to_date, limit)
        logger.info(f"[MAIN] Received: {len(recv_sms)}")
        live_keys = {(x['phone_number'], x['otp_message'][:40]) for x in all_otp}
        for item in recv_sms:
            key = (item['phone_number'], item['otp_message'][:40])
            if key not in live_keys:
                all_otp.append(item)
                live_keys.add(key)

    if limit:
        all_otp = all_otp[:limit]

    logger.info(f"[MAIN] Total: {len(all_otp)}")
    return all_otp, None


# ════════════════════════════════════════════════════════════════
# FLASK APP
# ════════════════════════════════════════════════════════════════
app = Flask(__name__)


@app.route('/')
def welcome():
    return jsonify({
        'message': 'IVAS SMS API',
        'status':  'alive',
        'endpoints': {
            '/sms':              'GET — date=DD/MM/YYYY, limit=N, mode=live|received|both',
            '/live':             'GET — Live SMS saja (cepat)',
            '/health':           'GET — Test login',
            '/debug/ranges':     'GET — Debug: lihat raw ranges response (date=DD/MM/YYYY)',
            '/debug/numbers':    'GET — Debug: lihat raw numbers response (date=DD/MM/YYYY&range=ZIMBABWE+188)',
            '/debug/sms':        'GET — Debug: lihat raw SMS response (date=DD/MM/YYYY&range=ZIMBABWE+188&number=263784490048)',
        }
    })


@app.route('/health')
def health_check():
    scraper = build_scraper()
    result  = do_login(scraper)
    if result:
        _, _, live_html = result
        n = len(parse_live_sms(live_html))
        return jsonify({'status': 'ok', 'login': 'success', 'live_sms_now': n})
    return jsonify({'status': 'error', 'login': 'failed'}), 500


@app.route('/live')
def get_live_only():
    scraper = build_scraper()
    result  = do_login(scraper)
    if not result:
        return jsonify({'error': 'Login gagal'}), 500
    _, _, live_html = result
    msgs = parse_live_sms(live_html)
    return jsonify({'status': 'success', 'source': 'live', 'total': len(msgs), 'otp_messages': msgs})


# ── DEBUG ENDPOINTS ── lihat raw HTML response per step ──────────

@app.route('/debug/ranges')
def debug_ranges():
    """Lihat raw response dari POST /getsms — untuk debug selector."""
    date_str = request.args.get('date', datetime.now().strftime('%d/%m/%Y'))
    scraper  = build_scraper()
    result   = do_login(scraper)
    if not result:
        return jsonify({'error': 'Login gagal'}), 500

    csrf, scraper, _ = result
    try:
        resp = scraper.post(
            f"{IVASMS_BASE_URL}/portal/sms/received/getsms",
            data={'from': to_ivas_date(date_str), 'to': to_ivas_date(date_str), '_token': csrf},
            headers=ajax_headers(), timeout=15,
        )
        html   = decompress_response(resp)
        soup   = BeautifulSoup(html, 'html.parser')
        ranges = get_ranges_received(scraper, csrf, date_str, date_str)

        return jsonify({
            'status':      'ok',
            'date':        date_str,
            'http_status': resp.status_code,
            'ranges_found': ranges,
            'html_length':  len(html),
            'html_preview': html[:3000],  # 3000 karakter pertama untuk debug
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/debug/numbers')
def debug_numbers():
    """Lihat raw response dari POST /getsms/number — debug selector nomor."""
    date_str    = request.args.get('date', datetime.now().strftime('%d/%m/%Y'))
    phone_range = request.args.get('range', '')
    if not phone_range:
        return jsonify({'error': 'Parameter range wajib, contoh: ?date=06/03/2026&range=ZIMBABWE 188'}), 400

    scraper = build_scraper()
    result  = do_login(scraper)
    if not result:
        return jsonify({'error': 'Login gagal'}), 500

    csrf, scraper, _ = result
    try:
        resp = scraper.post(
            f"{IVASMS_BASE_URL}/portal/sms/received/getsms/number",
            data={'_token': csrf, 'start': to_ivas_date(date_str), 'end': to_ivas_date(date_str), 'range': phone_range},
            headers=ajax_headers(), timeout=15,
        )
        html    = decompress_response(resp)
        numbers = get_numbers_received(scraper, csrf, phone_range, date_str, date_str)

        return jsonify({
            'status':        'ok',
            'date':          date_str,
            'range':         phone_range,
            'http_status':   resp.status_code,
            'numbers_found': numbers,
            'html_length':   len(html),
            'html_preview':  html[:3000],
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/debug/sms')
def debug_sms():
    """Lihat raw response dari POST /getsms/number/sms — debug selector pesan."""
    date_str    = request.args.get('date', datetime.now().strftime('%d/%m/%Y'))
    phone_range = request.args.get('range', '')
    phone_number = request.args.get('number', '')
    if not phone_range or not phone_number:
        return jsonify({'error': 'Parameter range dan number wajib'}), 400

    scraper = build_scraper()
    result  = do_login(scraper)
    if not result:
        return jsonify({'error': 'Login gagal'}), 500

    csrf, scraper, _ = result
    try:
        resp = scraper.post(
            f"{IVASMS_BASE_URL}/portal/sms/received/getsms/number/sms",
            data={'_token': csrf, 'start': to_ivas_date(date_str), 'end': to_ivas_date(date_str),
                  'Number': phone_number, 'Range': phone_range},
            headers=ajax_headers(), timeout=15,
        )
        html = decompress_response(resp)
        msg  = get_sms_received(scraper, csrf, phone_number, phone_range, date_str, date_str)

        return jsonify({
            'status':      'ok',
            'date':        date_str,
            'range':       phone_range,
            'number':      phone_number,
            'http_status': resp.status_code,
            'message_found': msg,
            'html_length': len(html),
            'html_preview': html[:3000],
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/sms')
def get_sms_endpoint():
    date_str = request.args.get('date')
    limit    = request.args.get('limit')
    mode     = request.args.get('mode', 'both')

    if mode not in ('live', 'received', 'both'):
        return jsonify({'error': 'mode harus: live, received, atau both'}), 400

    from_date = datetime.now().strftime('%d/%m/%Y')
    to_date   = from_date

    if mode != 'live':
        if not date_str:
            return jsonify({'error': 'Parameter date wajib untuk mode received/both (DD/MM/YYYY)'}), 400
        try:
            datetime.strptime(date_str, '%d/%m/%Y')
            from_date = date_str
            to_date   = request.args.get('to_date', date_str)
            if to_date != date_str:
                datetime.strptime(to_date, '%d/%m/%Y')
        except ValueError:
            return jsonify({'error': 'Format tanggal tidak valid. Gunakan DD/MM/YYYY'}), 400

    if limit:
        try:
            limit = int(limit)
            if limit <= 0:
                return jsonify({'error': 'Limit harus positif'}), 400
        except ValueError:
            return jsonify({'error': 'Limit harus angka'}), 400
    else:
        limit = None

    otp_messages, err = fetch_all_otp(from_date, to_date, limit, mode)
    if otp_messages is None:
        return jsonify({'error': err}), 500

    return jsonify({
        'status':       'success',
        'mode':         mode,
        'from_date':    from_date,
        'to_date':      to_date,
        'total':        len(otp_messages),
        'otp_messages': otp_messages,
    })


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)

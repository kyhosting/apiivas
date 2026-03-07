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
def find_live_ajax_url(html):
    """
    Scan JavaScript di dalam HTML iVAS untuk menemukan endpoint AJAX Live SMS.
    Cari pola: url: '...', $.ajax({url:, $.post(', fetch('
    """
    patterns = [
        r"url\s*:\s*['\"]([^'\"]*live[^'\"]*)['\"]",
        r"url\s*:\s*['\"]([^'\"]*sms[^'\"]*)['\"]",
        r"\$\.post\s*\(\s*['\"]([^'\"]*live[^'\"]*)['\"]",
        r"\$\.post\s*\(\s*['\"]([^'\"]*sms[^'\"]*)['\"]",
        r"fetch\s*\(\s*['\"]([^'\"]*live[^'\"]*)['\"]",
        r"axios\.[a-z]+\s*\(\s*['\"]([^'\"]*live[^'\"]*)['\"]",
    ]
    found = []
    for pat in patterns:
        for m in re.finditer(pat, html, re.IGNORECASE):
            url = m.group(1)
            if url not in found and len(url) > 3:
                found.append(url)
    return found


def parse_live_sms(html):
    """
    Parse Live SMS dari HTML iVAS.
    Struktur tabel: Live SMS | SID | Paid | Limit | Message content
    Kolom 0: flag + RANGE NAME + NOMOR TELEPON
    Kolom 1: nama service (WhatsApp, dll) — ini adalah SID
    Kolom 4: isi pesan SMS
    """
    soup    = BeautifulSoup(html, 'html.parser')
    results = []

    for tbl in soup.find_all('table'):
        ths = [th.get_text(strip=True).lower() for th in tbl.find_all('th')]

        # Tabel Live SMS punya header: "Live SMS", "SID", "Paid", "Limit", "Message content"
        has_live_sms = any('live' in h for h in ths)
        has_message  = any('message' in h or 'content' in h for h in ths)

        # Skip tabel yang jelas bukan Live SMS
        if ths and not (has_live_sms or has_message):
            continue

        for row in tbl.find_all('tr'):
            tds = row.find_all('td')
            if len(tds) < 2:
                continue

            # Kolom 0: RANGE NAME + NOMOR TELEPON
            td0  = tds[0]
            raw0 = td0.get_text(separator='\n', strip=True)
            lines = [l.strip() for l in raw0.split('\n') if l.strip()]

            if not lines:
                continue

            # Skip baris header
            if re.match(r'^(live sms|sid|paid|limit|message|#)', lines[0], re.I):
                continue

            # Pisahkan range dan nomor
            range_ = ''
            number = ''
            for line in lines:
                clean_digits = re.sub(r'\D', '', line)
                if len(clean_digits) >= 10:
                    if not number:
                        number = clean_digits
                elif re.search(r'[A-Za-z]', line) and len(line) > 2:
                    if not range_:
                        range_ = line.strip()

            # Kolom 1: SID (nama service)
            sid = tds[1].get_text(strip=True) if len(tds) > 1 else ''

            # Kolom terakhir: isi pesan
            msg_text = tds[-1].get_text(strip=True)

            if not msg_text or len(msg_text) < 4:
                continue
            if re.match(r'^(message content|content|message)$', msg_text, re.I):
                continue
            if not range_ and not number:
                continue

            results.append({
                'range':        range_ or 'Unknown',
                'phone_number': number,
                'otp_message':  msg_text,
                'sid':          sid,
                'source':       'live',
            })

    logger.info(f"[LIVE] {len(results)} SMS dari HTML")
    return results


def fetch_live_via_js_url(scraper, csrf, html):
    """
    Cari URL AJAX dari JS di dalam HTML, lalu hit endpoint tersebut.
    """
    found_urls = find_live_ajax_url(html)
    logger.info(f"[LIVE JS SCAN] URL ditemukan: {found_urls}")

    live_headers = {
        'Accept':           'application/json, text/javascript, */*; q=0.01',
        'Content-Type':     'application/x-www-form-urlencoded; charset=UTF-8',
        'X-Requested-With': 'XMLHttpRequest',
        'Origin':           IVASMS_BASE_URL,
        'Referer':          IVASMS_LIVE_URL,
    }

    for url in found_urls:
        # Jadikan URL absolut
        if url.startswith('/'):
            url = IVASMS_BASE_URL + url
        elif not url.startswith('http'):
            url = IVASMS_BASE_URL + '/' + url

        try:
            resp = scraper.post(url, data={'_token': csrf}, headers=live_headers, timeout=10)
            if resp.status_code == 200 and len(resp.text) > 50:
                logger.info(f"[LIVE JS SCAN] Hit! {url} → len={len(resp.text)}")
                # Coba parse HTML
                msgs = parse_live_sms(resp.text)
                if msgs:
                    return msgs
                # Coba parse JSON
                try:
                    data = resp.json()
                    if isinstance(data, list) and data:
                        return data
                    if isinstance(data, dict) and ('data' in data or 'sms' in data or 'messages' in data):
                        items = data.get('data', data.get('sms', data.get('messages', [])))
                        if items:
                            return items
                except Exception:
                    pass
        except Exception as e:
            logger.info(f"[LIVE JS SCAN] Error {url}: {e}")

    return []


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
    Return: list of range strings misal ["IVORY COAST 3878", "ZIMBABWE 188"]

    Struktur HTML asli (dari debug):
    <div class="rng" onclick="toggleRange('IVORY COAST 3878','IVORY_COAST_3878')">
      <div class="inner">
        <div class="c-name">
          <span class="rname">IVORY COAST 3878</span>
        </div>
        <div class="c-val v-count">5</div>
        ...
      </div>
      <div class="sub" id="sp_IVORY_COAST_3878">...</div>
    </div>
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

        # Prioritas 1: div.rng → span.rname  (struktur asli dari debug)
        for rng in soup.select('div.rng'):
            rname = rng.select_one('span.rname')
            if rname:
                txt = rname.get_text(strip=True)
                if txt and txt not in ranges:
                    ranges.append(txt)

        # Prioritas 2: onclick="toggleRange('IVORY COAST 3878','IVORY_COAST_3878')"
        if not ranges:
            for el in soup.find_all(onclick=True):
                oc = el.get('onclick', '')
                m  = re.search(r"toggleRange\s*\(\s*['\"]([^'\"]+)['\"]", oc)
                if m:
                    txt = m.group(1).strip()
                    if txt and txt not in ranges:
                        ranges.append(txt)

        # Prioritas 3: onclick="getDetials(...)"
        if not ranges:
            for el in soup.find_all(onclick=True):
                oc = el.get('onclick', '')
                m  = re.search(r"getDetials\s*\(\s*['\"]([^'\"]+)['\"]", oc)
                if m:
                    txt = m.group(1).strip()
                    if txt and txt not in ranges:
                        ranges.append(txt)

        # Prioritas 4: tabel — kolom pertama nama negara
        if not ranges:
            for row in soup.select('table tbody tr, table tr'):
                tds = row.find_all('td')
                if not tds:
                    continue
                txt = tds[0].get_text(strip=True)
                if re.match(r'^[A-Z][A-Z\s]+\d+$', txt) and txt not in ranges:
                    ranges.append(txt)

        logger.info(f"[RANGES] {len(ranges)} ranges: {ranges}")
        # Return list of dict: {'name': 'IVORY COAST 3878', 'id': 'IVORY_COAST_3878'}
        result = []
        for rng_div in soup.select('div.rng'):
            rname = rng_div.select_one('span.rname')
            if not rname:
                continue
            name = rname.get_text(strip=True)
            if not name:
                continue
            # Ambil ID dari onclick atau dari div.sub id
            rng_id = None
            oc = rng_div.get('onclick', '')
            m  = re.search(r"toggleRange\s*\(\s*'[^']*'\s*,\s*'([^']+)'", oc)
            if m:
                rng_id = m.group(1)
            if not rng_id:
                sub = rng_div.select_one('div[id^="sp_"]')
                if sub:
                    rng_id = sub.get('id', '').replace('sp_', '')
            if not rng_id:
                rng_id = name.replace(' ', '_')
            result.append({'name': name, 'id': rng_id})

        # Fallback: dari ranges list yang sudah dikumpulkan sebelumnya
        if not result:
            for r in ranges:
                result.append({'name': r, 'id': r.replace(' ', '_')})

        logger.info(f"[RANGES] {len(result)} ranges: {[r['name'] for r in result]}")
        return result

    except Exception as e:
        logger.error(f"[RANGES] Error: {e}")
        return []


def get_numbers_received(scraper, csrf, range_name, range_id, from_date, to_date):
    """
    POST /portal/sms/received/getsms/number
    Dari HTML: $.ajax({ url: .../getsms/number, data: {_token, start, end, range: id} })
    range = range_id (IVORY_COAST_3878), bukan nama dengan spasi
    """
    try:
        resp = scraper.post(
            f"{IVASMS_BASE_URL}/portal/sms/received/getsms/number",
            data={
                '_token': csrf,
                'start':  to_ivas_date(from_date),
                'end':    to_ivas_date(to_date),
                'range':  range_id,   # ← kirim ID (underscore), bukan nama
            },
            headers=ajax_headers(),
            timeout=15,
        )
        if resp.status_code != 200:
            logger.error(f"[NUMBERS] Status {resp.status_code} for {range_name}")
            return []

        html = decompress_response(resp)
        soup = BeautifulSoup(html, 'html.parser')
        numbers = []

        # Prioritas 1: div.num atau div.number → ambil digit
        for el in soup.select('div.num, div.number, div.phone'):
            txt    = el.get_text(strip=True)
            digits = re.sub(r'\D', '', txt)
            if 8 <= len(digits) <= 15 and digits not in numbers:
                numbers.append(digits)

        # Prioritas 2: onclick getDetialsNumber('263784490048',...)
        if not numbers:
            for el in soup.find_all(onclick=True):
                oc = el.get('onclick', '')
                m  = re.search(r"getDetialsNumber\s*\(\s*['\"]?(\d{7,15})['\"]?", oc)
                if m and m.group(1) not in numbers:
                    numbers.append(m.group(1))

        # Prioritas 3: scan tabel td
        if not numbers:
            for row in soup.select('table tbody tr, table tr'):
                tds = row.find_all('td')
                for td in tds:
                    txt    = td.get_text(strip=True)
                    digits = re.sub(r'\D', '', txt)
                    if 8 <= len(digits) <= 15 and digits not in numbers:
                        numbers.append(digits)

        # Prioritas 4: semua element leaf yang isinya digit panjang
        if not numbers:
            for el in soup.find_all(['span', 'div', 'td', 'p', 'a']):
                if el.find_all(True):
                    continue
                txt    = el.get_text(strip=True)
                digits = re.sub(r'\D', '', txt)
                if 8 <= len(digits) <= 15 and digits not in numbers:
                    numbers.append(digits)

        logger.info(f"[NUMBERS] {range_name} → {len(numbers)} nomor: {numbers}")
        return numbers

    except Exception as e:
        logger.error(f"[NUMBERS] Error for {range_name}: {e}")
        return []


def get_sms_received(scraper, csrf, phone_number, range_name, range_id, from_date, to_date):
    """
    POST /portal/sms/received/getsms/number/sms
    Dari screenshot: tabel SENDER | MESSAGE | TIME | REVENUE
    Range dikirim pakai range_id (underscore)
    """
    try:
        resp = scraper.post(
            f"{IVASMS_BASE_URL}/portal/sms/received/getsms/number/sms",
            data={
                '_token': csrf,
                'start':  to_ivas_date(from_date),
                'end':    to_ivas_date(to_date),
                'Number': phone_number,
                'Range':  range_id,   # ← pakai ID (underscore)
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
    # ranges sekarang list of dict: [{'name': 'IVORY COAST 3878', 'id': 'IVORY_COAST_3878'}]
    ranges  = get_ranges_received(scraper, csrf, from_date, to_date)
    results = []

    for rng in ranges:
        rng_name = rng['name']
        rng_id   = rng['id']
        numbers  = get_numbers_received(scraper, csrf, rng_name, rng_id, from_date, to_date)

        for phone_number in numbers:
            if limit and len(results) >= limit:
                return results
            msg = get_sms_received(scraper, csrf, phone_number, rng_name, rng_id, from_date, to_date)
            if msg:
                results.append({
                    'range':        rng_name,
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
        # Coba 1: parse langsung dari HTML (kalau data sudah ada)
        live_sms = parse_live_sms(live_html)
        # Coba 2: scan JS di HTML untuk menemukan AJAX endpoint
        if not live_sms:
            live_sms = fetch_live_via_js_url(scraper, csrf, live_html)
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
    csrf, scraper, live_html = result
    msgs = parse_live_sms(live_html)
    if not msgs:
        msgs = fetch_live_via_js_url(scraper, csrf, live_html)
    return jsonify({'status': 'success', 'source': 'live', 'total': len(msgs), 'otp_messages': msgs})


@app.route('/debug/live')
def debug_live():
    """Debug: scan JS iVAS untuk temukan endpoint AJAX Live SMS."""
    scraper = build_scraper()
    result  = do_login(scraper)
    if not result:
        return jsonify({'error': 'Login gagal'}), 500
    csrf, scraper, live_html = result

    js_urls   = find_live_ajax_url(live_html)
    msgs_html = parse_live_sms(live_html)
    msgs_js   = fetch_live_via_js_url(scraper, csrf, live_html)

    return jsonify({
        'status':            'ok',
        'login':             'success',
        'html_length':       len(live_html),
        'js_urls_found':     js_urls,
        'messages_from_html': len(msgs_html),
        'messages_from_ajax': len(msgs_js),
        'messages':          msgs_html or msgs_js,
        # Preview 200 char di sekitar kata "url" dari JS
        'js_url_context':    [
            live_html[max(0, m.start()-50):m.start()+150]
            for m in list(re.finditer(r"url\s*:", live_html, re.I))[:10]
        ],
    })


@app.route('/debug/js')
def debug_js():
    """Debug: tampilkan semua script tag dari halaman Live SMS iVAS."""
    scraper = build_scraper()
    result  = do_login(scraper)
    if not result:
        return jsonify({'error': 'Login gagal'}), 500
    _, scraper, live_html = result

    soup    = BeautifulSoup(live_html, 'html.parser')
    scripts = []
    for i, s in enumerate(soup.find_all('script')):
        content = s.string or ''
        # Hanya tampilkan script yang mengandung kata 'ajax', 'url', 'sms', 'live'
        if any(kw in content.lower() for kw in ['ajax', '.post', 'fetch', 'live', 'sms', 'getlive']):
            scripts.append({
                'index':   i,
                'src':     s.get('src', ''),
                'content': content[:2000],  # 2000 char pertama
            })

    return jsonify({
        'status':       'ok',
        'total_scripts': len(soup.find_all('script')),
        'relevant_scripts': len(scripts),
        'scripts':      scripts,
    })


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
        # range parameter dari user bisa berupa nama "IVORY COAST 3878" atau ID "IVORY_COAST_3878"
        # Normalize: konversi spasi ke underscore untuk dikirim ke ivasms
        range_id = phone_range.replace(' ', '_')
        resp = scraper.post(
            f"{IVASMS_BASE_URL}/portal/sms/received/getsms/number",
            data={'_token': csrf, 'start': to_ivas_date(date_str), 'end': to_ivas_date(date_str), 'range': range_id},
            headers=ajax_headers(), timeout=15,
        )
        html    = decompress_response(resp)
        numbers = get_numbers_received(scraper, csrf, phone_range, range_id, date_str, date_str)

        return jsonify({
            'status':        'ok',
            'date':          date_str,
            'range_name':    phone_range,
            'range_id':      range_id,
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
        range_id = phone_range.replace(' ', '_')
        resp = scraper.post(
            f"{IVASMS_BASE_URL}/portal/sms/received/getsms/number/sms",
            data={'_token': csrf, 'start': to_ivas_date(date_str), 'end': to_ivas_date(date_str),
                  'Number': phone_number, 'Range': range_id},
            headers=ajax_headers(), timeout=15,
        )
        html = decompress_response(resp)
        msg  = get_sms_received(scraper, csrf, phone_number, phone_range, range_id, date_str, date_str)

        return jsonify({
            'status':        'ok',
            'date':          date_str,
            'range_name':    phone_range,
            'range_id':      range_id,
            'number':        phone_number,
            'http_status':   resp.status_code,
            'message_found': msg,
            'html_length':   len(html),
            'html_preview':  html[:3000],
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

#!/usr/bin/env python3
"""kambience: web front end for kambience.py on mattbot.

Pick a book from the Calibre library (or upload one), tag it, and the scenes are embedded in the
EPUB itself (META-INF/kambience.json), so the sounds travel with the book through Calibre-Web,
OPDS or USB. Library books are updated in place after a backup to DATA/backups; uploads can be
downloaded or dropped into the Calibre-Web-Automated ingest folder.
Stdlib only, apart from the optional `anthropic` package for Claude.
"""
import collections, datetime, json, os, queue, re, shutil, sqlite3, sys, threading, time, urllib.parse, uuid, zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.environ.get('KAMBIENCE_DIR', os.path.join(HERE, '..', 'kambience')))
import kambience as im  # noqa: E402

PORT = int(os.environ.get('PORT', 8095))
DATA = os.environ.get('DATA', os.path.join(HERE, 'data'))
LIBRARY = os.environ.get('LIBRARY', '/library')
INGEST = os.environ.get('INGEST', '/ingest')
SOUNDS = os.environ.get('SOUNDS', os.path.join(HERE, '..', 'sounds'))
STATIC = os.path.join(HERE, 'static')
MAX_UPLOAD = 300 * 1024 * 1024

# Provider presets. Everything except Claude speaks the OpenAI chat/completions format.
# 'batch': text per request; local servers get smaller batches to fit their context window
# (matbot-llm runs llama-server with --ctx-size 8192).
PRESETS = {
    'mattbot':    {'name': 'Qwen on mattbot (free, local)', 'api': 'openai', 'base_url': 'http://127.0.0.1:11434/v1',
                   'model': 'qwen36-heretic', 'key': False, 'batch': 12000},
    'anthropic':  {'name': 'Claude (Anthropic)', 'api': 'anthropic', 'base_url': '', 'model': 'claude-opus-5-5', 'key': True},
    'openai':     {'name': 'OpenAI', 'api': 'openai', 'base_url': 'https://api.openai.com/v1', 'model': '', 'key': True},
    'openrouter': {'name': 'OpenRouter', 'api': 'openai', 'base_url': 'https://openrouter.ai/api/v1', 'model': '', 'key': True},
    'gemini':     {'name': 'Google Gemini', 'api': 'openai', 'base_url': 'https://generativelanguage.googleapis.com/v1beta/openai', 'model': '', 'key': True},
    'groq':       {'name': 'Groq', 'api': 'openai', 'base_url': 'https://api.groq.com/openai/v1', 'model': '', 'key': True},
    'ollama':     {'name': 'Ollama', 'api': 'openai', 'base_url': 'http://127.0.0.1:11434/v1', 'model': '', 'key': False, 'batch': 12000},
    'lmstudio':   {'name': 'LM Studio', 'api': 'openai', 'base_url': 'http://127.0.0.1:1234/v1', 'model': '', 'key': False, 'batch': 12000},
    'custom':     {'name': 'Other (OpenAI-compatible)', 'api': 'openai', 'base_url': '', 'model': '', 'key': True},
}

for d in ('jobs', 'uploads', 'backups'):
    os.makedirs(os.path.join(DATA, d), exist_ok=True)


# ---------- settings ----------

SETTINGS = os.path.join(DATA, 'settings.json')
settings_lock = threading.Lock()


def load_settings():
    try:
        with open(SETTINGS) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {'provider': 'mattbot', **{k: PRESETS['mattbot'][k] for k in ('api', 'base_url', 'model')}, 'api_key': ''}


def save_settings(s):
    with settings_lock:
        tmp = SETTINGS + '.tmp'
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # holds the API key
        with os.fdopen(fd, 'w') as f:
            json.dump(s, f)
        os.replace(tmp, SETTINGS)


def public_settings():
    s = load_settings()
    key = s.get('api_key') or ''
    return {k: s.get(k, '') for k in ('provider', 'api', 'base_url', 'model')} | {
        'key_hint': ('…' + key[-4:]) if len(key) > 8 else ('set' if key else ''),
        'limit_pct': int(s.get('limit_pct') or 100),
        'presets': {k: {kk: v[kk] for kk in ('name', 'base_url', 'model', 'key')} for k, v in PRESETS.items()}}


def llm_config():
    s = load_settings()
    p = PRESETS.get(s.get('provider'), PRESETS['custom'])
    cfg = {'api': p['api'], 'base_url': s.get('base_url') or p['base_url'], 'model': s.get('model') or p['model'],
           'api_key': s.get('api_key') or None, 'batch_chars': p.get('batch')}
    if not cfg['model']:
        raise ValueError('Choose a model in the AI settings first.')
    if cfg['api'] == 'openai' and not cfg['base_url']:
        raise ValueError('Fill in the server address in the AI settings first.')
    if p['key'] and not cfg['api_key']:
        raise ValueError('Add your API key in the AI settings first.')
    return cfg


# ---------- Calibre library ----------

_flag_cache = {}


def coverage(path):
    """Percent of the book that has sound: 0 none, 100 all (books from before partial runs count as 100)."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return 0
    if _flag_cache.get(path, (None,))[0] != mtime:
        data = im.embedded(path)
        _flag_cache[path] = (mtime, 0 if data is None else int(data.get('coverage', 100)))
    return _flag_cache[path][1]


def library_books():
    db = os.path.join(LIBRARY, 'metadata.db')
    if not os.path.exists(db):
        return []
    con = sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=5)
    try:
        rows = con.execute("""
            SELECT b.id, b.title, b.path, d.name,
                   (SELECT group_concat(a.name, ' & ') FROM books_authors_link l JOIN authors a ON a.id = l.author
                    WHERE l.book = b.id)
            FROM books b JOIN data d ON d.book = b.id AND d.format = 'EPUB' ORDER BY b.sort""").fetchall()
    finally:
        con.close()
    out = []
    for bid, title, rel, name, authors in rows:
        path = library_path(rel, name)
        if path:
            out.append({'id': bid, 'title': title, 'author': authors or '', 'path': path,
                        'coverage': coverage(path)})
    return out


def title_sort(title):
    """Calibre's default: a leading article moves to the end."""
    m = re.match(r'(A|An|The)\s+(.+)', title or '', re.S)
    return f'{m.group(2)}, {m.group(1)}' if m else title


def mark_calibre(book_id):
    """Title suffix and tag in metadata.db too: Calibre-Web shows the database, not the EPUB."""
    con = sqlite3.connect(os.path.join(LIBRARY, 'metadata.db'), timeout=30)
    con.create_function('title_sort', 1, title_sort)  # Calibre's books_update_trg calls it
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(sep=' ')
    try:
        with con:
            con.execute('UPDATE books SET title = title || ?, last_modified = ? WHERE id = ? AND substr(title, -?) <> ?',
                        (im.TITLE_SUFFIX, now, book_id, len(im.TITLE_SUFFIX), im.TITLE_SUFFIX))
            con.execute('INSERT OR IGNORE INTO tags(name) VALUES (?)', (im.MARK,))
            con.execute('INSERT OR IGNORE INTO books_tags_link(book, tag) SELECT ?, id FROM tags WHERE name = ?',
                        (book_id, im.MARK))
    finally:
        con.close()


def library_path(rel, name):
    """Joins a path from Calibre's database, refusing anything that would leave the library."""
    root = os.path.realpath(LIBRARY)
    path = os.path.realpath(os.path.join(root, rel, name + '.epub'))
    return path if path.startswith(root + os.sep) and os.path.isfile(path) else None


# ---------- jobs ----------

JOBS = collections.OrderedDict()
jobs_lock = threading.Lock()
work_queue = queue.Queue()
PROGRESS = re.compile(r'(\d+)/(\d+) passages')
TOTAL = re.compile(r': (\d+) passages\b')


def job_view(j):
    return {k: j.get(k) for k in ('id', 'title', 'author', 'source', 'state', 'done', 'total', 'note', 'error', 'percent',
                                  'summary', 'missing', 'saved', 'ingested', 'created', 'finished')}


def persist(j):
    d = os.path.join(DATA, 'jobs', j['id'])
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, 'job.json'), 'w') as f:
        json.dump({k: v for k, v in j.items() if k != 'log'}, f)


def load_jobs():
    root = os.path.join(DATA, 'jobs')
    found = []
    for jid in os.listdir(root):
        try:
            with open(os.path.join(root, jid, 'job.json')) as f:
                j = json.load(f)
        except (OSError, ValueError):
            continue
        if j.get('state') in ('queued', 'running'):  # interrupted by a restart: carry on from the checkpoint
            j.update(state='queued', note='Picking up where it left off')
        found.append(j)
    for j in sorted(found, key=lambda j: j.get('created', 0)):
        JOBS[j['id']] = j
        if j['state'] == 'queued':
            work_queue.put(j['id'])


def new_job(**kw):
    j = {'id': uuid.uuid4().hex[:12], 'state': 'queued', 'done': 0, 'total': 0, 'note': 'Waiting its turn',
         'created': time.time(), 'saved': '', 'ingested': False,
         'percent': int(load_settings().get('limit_pct') or 100), **kw}
    with jobs_lock:
        JOBS[j['id']] = j
    persist(j)
    work_queue.put(j['id'])
    return j


def safe_name(s):
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]+', '_', s).strip(' .') or 'book'


def summarize(data):
    used = collections.Counter(s['sound'] for s in data['scenes'] if s['sound'] != 'silence')
    return {'changes': len(data['scenes']), 'sounds': used.most_common()}


def work(j):
    def log(msg):
        msg = msg.strip()
        if m := PROGRESS.search(msg):
            j['done'], j['total'] = int(m.group(1)), int(m.group(2))
            j['note'] = f'Reading passage {j["done"]:,} of {j["total"]:,}'
        elif m := TOTAL.search(msg):
            j['total'] = int(m.group(1))
            j['note'] = f'Reading {j["total"]:,} passages'
        elif 'retrying' in msg or 'failed' in msg or 'resuming' in msg:
            j['note'] = msg.capitalize()
    labels = im.load_labels(SOUNDS)
    if len(labels) == 1:
        raise ValueError('No sound files found. Run sounds/fetch_sounds.py on mattbot first.')
    cfg = llm_config()
    j['state'], j['note'] = 'running', 'Opening the book'
    d = os.path.join(DATA, 'jobs', j['id'])
    ckpt = os.path.join(d, 'progress.json')  # labels so far, so a stopped job resumes instead of starting over
    try:
        with open(ckpt) as f:
            resume = json.load(f)
    except (OSError, ValueError):
        resume = None if j.get('fresh') else im.embedded(j['path'])  # continue a book done in parts

    def save(done, missing):
        with open(ckpt + '.tmp', 'w') as f:
            json.dump({'labels': done, 'missing': dict(missing)}, f)
        os.replace(ckpt + '.tmp', ckpt)
        persist(j)

    data, missing = im.run(j['path'], labels, cfg, log=log, resume=resume, on_batch=save,
                           percent=j.get('percent') or 100)
    j['json'] = os.path.join(d, safe_name(j['name']) + '.kambience.json')
    with open(j['json'], 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=1)
    if j['source'] == 'library':
        backup = os.path.join(DATA, 'backups', str(j['book_id']), os.path.basename(j['path']))
        if not os.path.exists(backup):  # keep the very first, untouched copy
            os.makedirs(os.path.dirname(backup), exist_ok=True)
            shutil.copy2(j['path'], backup)
        im.embed(j['path'], j['path'], data)
        mark_calibre(j['book_id'])
        j['epub'] = j['path']
        j['saved'] = 'Saved into your Calibre library. The original is backed up on mattbot.'
    else:
        j['epub'] = os.path.join(d, safe_name(j['name']) + '.epub')
        im.embed(j['path'], j['epub'], data)
    j['summary'] = summarize(data) | {'coverage': data['coverage']}
    j['missing'] = missing.most_common(10)
    if os.path.exists(ckpt):
        os.remove(ckpt)


def worker():
    while True:
        jid = work_queue.get()
        j = JOBS.get(jid)
        if not j:
            continue
        try:
            work(j)
            j.update(state='done', note='Finished')
        except Exception as e:  # shown to the user; the queue keeps going
            j.update(state='failed', error=str(e)[:400] or type(e).__name__)
        j['finished'] = time.time()
        persist(j)


# ---------- HTTP ----------

TYPES = {'.html': 'text/html; charset=utf-8', '.css': 'text/css', '.js': 'text/javascript',
         '.woff2': 'font/woff2', '.svg': 'image/svg+xml', '.txt': 'text/plain; charset=utf-8'}


class H(BaseHTTPRequestHandler):
    def send(self, code, body=b'', ctype='application/json', headers=()):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def fail(self, code, msg):
        self.send(code, {'error': msg})

    def body_json(self):
        n = int(self.headers.get('Content-Length') or 0)
        if n > 1_000_000:
            raise ValueError('request too large')
        return json.loads(self.rfile.read(n) or b'{}')

    def file(self, path, name, ctype):
        size = os.path.getsize(path)
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(size))
        self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + urllib.parse.quote(name))
        self.end_headers()
        with open(path, 'rb') as f:
            shutil.copyfileobj(f, self.wfile)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == '/':
            path = '/static/index.html'
        if path.startswith('/static/'):
            full = os.path.realpath(os.path.join(STATIC, path[len('/static/'):]))
            if not full.startswith(os.path.realpath(STATIC) + os.sep) or not os.path.isfile(full):
                return self.fail(404, 'not found')
            with open(full, 'rb') as f:
                return self.send(200, f.read(), TYPES.get(os.path.splitext(full)[1], 'application/octet-stream'),
                                 [('Cache-Control', 'no-cache')])
        if path == '/api/library':
            return self.send(200, [{k: b[k] for k in ('id', 'title', 'author', 'coverage')} for b in library_books()])
        if path == '/api/settings':
            return self.send(200, public_settings())
        if path == '/api/jobs':
            return self.send(200, [job_view(j) for j in reversed(JOBS.values())][:30])
        m = re.fullmatch(r'/api/jobs/(\w+)/(epub|json)', path)
        if m and (j := JOBS.get(m.group(1))) and j.get('state') == 'done':
            if m.group(2) == 'epub':
                return self.file(j['epub'], safe_name(j['name']) + '.epub', 'application/epub+zip')
            return self.file(j['json'], safe_name(j['name']) + '.kambience.json', 'application/json')
        self.fail(404, 'not found')

    def do_POST(self):
        path = urllib.parse.urlparse(self.path)
        try:
            if path.path == '/api/upload':
                return self.upload(urllib.parse.parse_qs(path.query).get('name', ['book.epub'])[0])
            if path.path == '/api/jobs':
                return self.start(self.body_json())
            if path.path == '/api/settings':
                return self.settings(self.body_json())
            if path.path == '/api/settings/test':
                return self.test()
            m = re.fullmatch(r'/api/jobs/(\w+)/(ingest|retry)', path.path)
            if m:
                return self.ingest(m.group(1)) if m.group(2) == 'ingest' else self.retry(m.group(1))
        except (ValueError, KeyError) as e:
            return self.fail(400, str(e))
        self.fail(404, 'not found')

    def upload(self, name):
        n = int(self.headers.get('Content-Length') or 0)
        if not name.lower().endswith('.epub'):
            return self.fail(400, 'Only .epub files can have sound added.')
        if not 0 < n <= MAX_UPLOAD:
            return self.fail(400, 'That file is empty or too large.')
        uid = uuid.uuid4().hex[:12]
        dest = os.path.join(DATA, 'uploads', uid + '.epub')
        with open(dest, 'wb') as f:
            left = n
            while left:
                chunk = self.rfile.read(min(1 << 20, left))
                if not chunk:
                    break
                f.write(chunk)
                left -= len(chunk)
        try:
            title, _ = im.read_epub(dest)
        except Exception:
            os.remove(dest)
            return self.fail(400, "That doesn't look like a valid EPUB.")
        j = new_job(source='upload', path=dest, title=title, author='', name=os.path.splitext(os.path.basename(name))[0])
        self.send(200, job_view(j))

    def start(self, body):
        book = next((b for b in library_books() if b['id'] == int(body['book_id'])), None)
        if not book:
            return self.fail(404, 'That book is no longer in the library.')
        if any(j.get('book_id') == book['id'] and j['state'] in ('queued', 'running') for j in JOBS.values()):
            return self.fail(409, 'That book is already being worked on.')
        j = new_job(source='library', path=book['path'], book_id=book['id'], title=book['title'], fresh=bool(body.get('fresh')),
                    author=book['author'], name=os.path.splitext(os.path.basename(book['path']))[0])
        self.send(200, job_view(j))

    def settings(self, body):
        s = load_settings()
        provider = body.get('provider', s.get('provider'))
        if provider not in PRESETS:
            return self.fail(400, 'Unknown provider.')
        s.update(provider=provider, api=PRESETS[provider]['api'],
                 base_url=str(body.get('base_url', '')).strip(), model=str(body.get('model', '')).strip())
        if body.get('api_key'):  # empty means "keep the saved key"
            s['api_key'] = str(body['api_key']).strip()
        if 'limit_pct' in body:
            pct = 100 if body['limit_pct'] in (None, '') else int(body['limit_pct'])
            if not 1 <= pct <= 100:
                return self.fail(400, 'The part of the book to read must be between 1 and 100%.')
            s['limit_pct'] = pct
        if body.get('clear_key'):
            s['api_key'] = ''
        save_settings(s)
        self.send(200, public_settings())

    def test(self):
        try:
            reply = im.call_llm(llm_config(), 'You answer with a single word.', 'Reply with the word ready.')
            self.send(200, {'ok': True, 'reply': reply.strip()[:80]})
        except Exception as e:
            self.send(200, {'ok': False, 'error': str(e)[:300] or type(e).__name__})

    def ingest(self, jid):
        j = JOBS.get(jid)
        if not j or j.get('state') != 'done' or j['source'] != 'upload':
            return self.fail(400, 'Only finished uploads can be added to Calibre.')
        if not os.path.isdir(INGEST) or not os.access(INGEST, os.W_OK):
            return self.fail(500, 'The Calibre ingest folder is not available.')
        shutil.copy2(j['epub'], os.path.join(INGEST, safe_name(j['name']) + '.epub'))
        j['ingested'] = True
        persist(j)
        self.send(200, job_view(j))

    def retry(self, jid):
        j = JOBS.get(jid)
        if not j or j.get('state') != 'failed':
            return self.fail(400, 'Only a stopped job can be tried again.')
        resumes = os.path.exists(os.path.join(DATA, 'jobs', jid, 'progress.json'))
        j.update(state='queued', error=None, note='Picking up where it left off' if resumes else 'Waiting its turn')
        persist(j)
        work_queue.put(jid)
        self.send(200, job_view(j))

    def log_message(self, *a):
        pass


if __name__ == '__main__':
    load_jobs()
    threading.Thread(target=worker, daemon=True).start()
    print(f'kambience on :{PORT}', flush=True)
    ThreadingHTTPServer(('0.0.0.0', PORT), H).serve_forever()

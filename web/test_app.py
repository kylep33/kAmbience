# End-to-end check of the web app with a fake library and a fake AI: python3 web/test_app.py
import json, os, shutil, sqlite3, sys, tempfile, threading, time, urllib.request, zipfile

root = tempfile.mkdtemp()
lib, ingest, data, sounds = (os.path.join(root, d) for d in ('library', 'ingest', 'data', 'sounds'))
for d in (lib, ingest, data, os.path.join(sounds, 'rain')):
    os.makedirs(d)
open(os.path.join(sounds, 'rain', 'heavy.mp3'), 'w').close()
os.environ.update(LIBRARY=lib, INGEST=ingest, DATA=data, SOUNDS=sounds, PORT='0')

def make_epub(path, text):
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('mimetype', 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        z.writestr('META-INF/container.xml', '<container><rootfiles><rootfile full-path="c.opf"/></rootfiles></container>')
        z.writestr('c.opf', '<package xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/">'
                   '<metadata><dc:title>Storm Book</dc:title></metadata><manifest><item id="a" href="a.xhtml"/></manifest>'
                   '<spine><itemref idref="a"/></spine></package>')
        z.writestr('a.xhtml', '<html><body>' + ''.join(f'<p>{text}</p>' for _ in range(8)) + '</body></html>')

# A minimal Calibre library: one EPUB book.
os.makedirs(os.path.join(lib, 'Ann Author', 'Storm Book (1)'))
book = os.path.join(lib, 'Ann Author', 'Storm Book (1)', 'Storm Book - Ann Author.epub')
make_epub(book, 'The rain hammered down on the roof all night long. ' * 30)
con = sqlite3.connect(os.path.join(lib, 'metadata.db'))
con.executescript("""CREATE TABLE books(id INTEGER PRIMARY KEY, title, sort, path, last_modified);
CREATE TABLE tags(id INTEGER PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE, UNIQUE(name));
CREATE TABLE books_tags_link(id INTEGER PRIMARY KEY, book INTEGER NOT NULL, tag INTEGER NOT NULL, UNIQUE(book, tag));
CREATE TRIGGER books_update_trg AFTER UPDATE ON books BEGIN
  UPDATE books SET sort=title_sort(NEW.title) WHERE id=NEW.id AND OLD.title <> NEW.title; END;
CREATE TABLE data(id INTEGER PRIMARY KEY, book, format, name);
CREATE TABLE authors(id INTEGER PRIMARY KEY, name);
CREATE TABLE books_authors_link(id INTEGER PRIMARY KEY, book, author);
INSERT INTO books VALUES(1, 'Storm Book', 'Storm Book', 'Ann Author/Storm Book (1)', '');
INSERT INTO books VALUES(2, 'Escape', 'Escape', '../../etc', '');
INSERT INTO data VALUES(1, 1, 'EPUB', 'Storm Book - Ann Author');
INSERT INTO data VALUES(2, 2, 'EPUB', 'passwd');
INSERT INTO authors VALUES(1, 'Ann Author');
INSERT INTO books_authors_link VALUES(1, 1, 1);""")
con.commit(); con.close()
original = open(book, 'rb').read()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app
def fake_llm(cfg, system, user):
    n = user.count('\n[')
    if 'ready' in user:
        return 'ready'
    return json.dumps({'passages': [{'label': 'rain/heavy', 'evidence': 'The rain hammered down on the roof'}] * n})
app.im.call_llm = fake_llm

srv = app.ThreadingHTTPServer(('127.0.0.1', 0), app.H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
threading.Thread(target=app.worker, daemon=True).start()
base = f'http://127.0.0.1:{srv.server_port}'

def req(path, data=None, raw=None, method=None):
    body = raw if raw is not None else (json.dumps(data).encode() if data is not None else None)
    r = urllib.request.Request(base + path, data=body, method=method or ('POST' if body is not None else 'GET'))
    try:
        with urllib.request.urlopen(r) as resp:
            out = resp.read()
            return resp.status, (json.loads(out) if resp.headers['Content-Type'] == 'application/json' else out)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())

def wait(jid):
    for _ in range(100):
        j = next(j for j in req('/api/jobs')[1] if j['id'] == jid)
        if j['state'] in ('done', 'failed'):
            return j
        time.sleep(0.05)
    raise AssertionError('job never finished')

# Page and assets are served; paths can't escape the static folder.
assert b'kambience' in req('/')[1]
assert req('/static/fonts/literata-latin-opsz-normal.woff2')[0] == 200
assert req('/static/../app.py')[0] == 404

# Library lists the book, and the database row pointing outside the library is ignored.
st, books = req('/api/library')
assert [b['title'] for b in books] == ['Storm Book'] and books[0]['author'] == 'Ann Author' and books[0]['coverage'] == 0

# Out of the box it uses the local Qwen: no key, smaller batches for its 8k context.
assert req('/api/settings')[1]['provider'] == 'mattbot'
cfg = app.llm_config()
assert cfg['base_url'] == 'http://127.0.0.1:11434/v1' and cfg['api_key'] is None and cfg['batch_chars'] == 12000

# A cloud service without a key fails the job with a clear message instead of crashing.
req('/api/settings', {'provider': 'openai', 'model': 'some-model', 'base_url': 'https://api.openai.com/v1'})
st, j = req('/api/jobs', {'book_id': 1})
j = wait(j['id'])
assert j['state'] == 'failed' and 'API key' in j['error'], j

# Settings: key is stored but never sent back.
st, s = req('/api/settings', {'provider': 'anthropic', 'model': 'claude-opus-5-5', 'api_key': 'sk-test-1234567890'})
assert s['key_hint'] == '…7890' and 'sk-test' not in json.dumps(s)
assert oct(os.stat(os.path.join(data, 'settings.json')).st_mode & 0o777) == '0o600'
st, s = req('/api/settings', {'provider': 'anthropic', 'model': 'claude-opus-5-5', 'api_key': ''})  # empty keeps it
assert s['key_hint'] == '…7890'
assert req('/api/settings/test', {})[1] == {'ok': True, 'reply': 'ready'}

# Library job: embedded in place, original backed up, downloadable.
st, j = req('/api/jobs', {'book_id': 1})
j = wait(j['id'])
assert j['state'] == 'done', j
assert app.im.embedded(book)['scenes'][0]['sound'] == 'rain/heavy'
backup = os.path.join(data, 'backups', '1', os.path.basename(book))
assert open(backup, 'rb').read() == original
assert req('/api/library')[1][0]['coverage'] == 100
con = sqlite3.connect(os.path.join(lib, 'metadata.db'))
assert con.execute('SELECT title, sort FROM books WHERE id = 1').fetchone() == ('Storm Book [kambience]',) * 2
assert con.execute('SELECT t.name FROM books_tags_link l JOIN tags t ON t.id = l.tag WHERE l.book = 1').fetchall() == [('kambience',)]
con.close()
assert app.title_sort('The Wise Man’s Fear') == 'Wise Man’s Fear, The'
st, epub = req(f'/api/jobs/{j["id"]}/epub')
assert st == 200 and epub[:2] == b'PK'
with zipfile.ZipFile(book) as z:
    assert z.infolist()[0].filename == 'mimetype' and z.infolist()[0].compress_type == zipfile.ZIP_STORED

# Testing limit: redo with only half the book, then raise it and "continue" reads just the rest.
assert req('/api/settings', {'provider': 'anthropic', 'model': 'claude-opus-5-5', 'limit_pct': 0})[0] == 400
st, s = req('/api/settings', {'provider': 'anthropic', 'model': 'claude-opus-5-5', 'limit_pct': 50})
assert s['limit_pct'] == 50
asked = []
app.im.call_llm = lambda cfg, sy, u: (asked.append(u.count('\n[')), fake_llm(cfg, sy, u))[1]
j = wait(req('/api/jobs', {'book_id': 1, 'fresh': True})[1]['id'])
assert j['state'] == 'done' and j['percent'] == 50 and 0 < j['summary']['coverage'] < 100, j
cov = req('/api/library')[1][0]['coverage']
assert 0 < cov < 100 and sum(asked) == j['total'], (cov, asked, j)
first = sum(asked); asked.clear()
req('/api/settings', {'provider': 'anthropic', 'model': 'claude-opus-5-5', 'limit_pct': 100})
j = wait(req('/api/jobs', {'book_id': 1})[1]['id'])  # continue
assert j['state'] == 'done' and req('/api/library')[1][0]['coverage'] == 100
total = len(app.im.chunk(app.im.read_epub(book)[1]))
assert sum(asked) == total - first, (asked, total, first)  # nothing read twice
app.im.call_llm = fake_llm

# Upload job: library untouched, ingest copies the book with sound.
make_epub(os.path.join(root, 'up.epub'), 'The rain hammered down on the roof all night long. ' * 30)
st, j = req('/api/upload?name=My%20Book.epub', raw=open(os.path.join(root, 'up.epub'), 'rb').read())
j = wait(j['id'])
assert j['state'] == 'done' and j['source'] == 'upload'
assert req(f'/api/jobs/{j["id"]}/ingest', {})[0] == 200
assert app.im.embedded(os.path.join(ingest, 'My Book.epub')) is not None
assert req('/api/upload?name=x.txt', raw=b'hello')[0] == 400
assert req('/api/upload?name=x.epub', raw=b'not a zip')[0] == 400

# History survives a restart.
before = sum(j['state'] == 'done' for j in app.JOBS.values())
app.JOBS.clear(); app.load_jobs()
assert sum(j['state'] == 'done' for j in app.JOBS.values()) == before >= 4

# A job killed mid-book resumes from its checkpoint when the app starts again.
jid = 'resumetest01'
os.makedirs(os.path.join(data, 'jobs', jid))
shutil.copy(os.path.join(root, 'up.epub'), os.path.join(data, 'uploads', 'r.epub'))
json.dump({'id': jid, 'state': 'running', 'source': 'upload', 'path': os.path.join(data, 'uploads', 'r.epub'),
           'title': 'Storm Book', 'author': '', 'name': 'Resumed', 'done': 1, 'total': 2, 'created': time.time()},
          open(os.path.join(data, 'jobs', jid, 'job.json'), 'w'))
json.dump({'labels': ['rain/heavy'], 'missing': {}}, open(os.path.join(data, 'jobs', jid, 'progress.json'), 'w'))
asked = []
app.im.call_llm = lambda cfg, s, u: (asked.append(u.count('\n[')), fake_llm(cfg, s, u))[1]
app.JOBS.clear(); app.load_jobs()
assert app.JOBS[jid]['state'] == 'queued'
j = wait(jid)
assert j['state'] == 'done' and sum(asked) == j['total'] - 1, (j, asked)  # the done passage wasn't read again
assert not os.path.exists(os.path.join(data, 'jobs', jid, 'progress.json'))

# "Try again" on a failed job requeues it.
app.JOBS[jid].update(state='failed', error='boom')
assert req(f'/api/jobs/{jid}/retry', {})[1]['state'] == 'queued'
assert wait(jid)['state'] == 'done'
assert req(f'/api/jobs/{jid}/retry', {})[0] == 400  # only failed jobs
print('ok')

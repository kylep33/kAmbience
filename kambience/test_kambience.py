# python3 test_kambience.py  (no network: the LLM is faked)
import json, os, sys, tempfile, threading, zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kambience as im

def make_epub(path, chapters):
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('META-INF/container.xml', '<container><rootfiles><rootfile full-path="OEBPS/c.opf"/></rootfiles></container>')
        items = ''.join(f'<item id="c{i}" href="ch{i}.xhtml"/>' for i in range(len(chapters)))
        refs = ''.join(f'<itemref idref="c{i}"/>' for i in range(len(chapters)))
        z.writestr('OEBPS/c.opf', '<package xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/">'
                   f'<metadata><dc:title>Test Book</dc:title></metadata><manifest>{items}</manifest><spine>{refs}</spine></package>')
        for i, paras in enumerate(chapters):
            body = ''.join(f'<p>{p}</p>' for p in paras)
            z.writestr(f'OEBPS/ch{i}.xhtml', f'<html><head><style>x{{}}</style></head><body>{body}</body></html>')

d = tempfile.mkdtemp()
epub = os.path.join(d, 'b.epub')
make_epub(epub, [['Title page'], ['The rain hammered down on the roof. ' * 40] * 6, ['They sat by the crackling fire. ' * 40] * 3])
title, frags = im.read_epub(epub)
assert title == 'Test Book' and len(frags) == 3 and frags[0] == ['Title page'], (title, frags[:1])

chunks = im.chunk(frags)
assert chunks[0]['frag'] == 1 and chunks[1]['at'] == 0
assert all(0 <= c['at'] < 1 for c in chunks)
assert sorted(set(c['frag'] for c in chunks)) == [1, 2, 3]

# Only sounds with a file are offered; labels.txt just describes them.
snd = os.path.join(d, 'sounds')
for p in ['rain/heavy.mp3', 'fire/hearth.mp3']:
    os.makedirs(os.path.dirname(os.path.join(snd, p)), exist_ok=True)
    open(os.path.join(snd, p), 'w').close()
open(os.path.join(snd, 'labels.txt'), 'w').write('rain/heavy  Heavy downpour\nindoor/library  Library\n')
labels = im.load_labels(snd)
assert labels == {'rain/heavy': 'Heavy downpour', 'fire/hearth': '', 'silence': 'No ambience (the default)'}, labels
assert im.load_labels(os.path.join(d, 'empty')) == {'silence': 'No ambience (the default)'}
# Pack descriptions are picked up too, once the pack's file exists.
os.makedirs(os.path.join(snd, 'packs', 'fantasy')); os.makedirs(os.path.join(snd, 'forge'))
open(os.path.join(snd, 'packs', 'fantasy', 'labels.txt'), 'w').write('forge/smithy  Blacksmith forge\n')
open(os.path.join(snd, 'forge', 'smithy.mp3'), 'w').close()
assert im.load_labels(snd)['forge/smithy'] == 'Blacksmith forge'
os.remove(os.path.join(snd, 'forge', 'smithy.mp3'))
labels = im.load_labels(snd)

# Strict labels: exact match or silence, never a near miss.
assert im.fix_label(' Rain/Heavy ', labels) == 'rain/heavy'
assert im.fix_label('rain/light', labels) == 'silence' and im.fix_label('indoor/library', labels) == 'silence'

# Evidence must really be in the passage; "same scene" only continues the identical sound.
text = 'Outside, the rain hammered down on the old slate roof.'
assert im.supported('rain/heavy', 'the rain hammered down on the old', text, 'silence')
assert im.supported('rain/heavy', 'The RAIN hammered  down, on the old', text, 'silence')   # case/punctuation-insensitive
assert not im.supported('rain/heavy', 'a storm raged across the valley', text, 'silence')    # invented quote
assert not im.supported('rain/heavy', 'rain', text, 'silence')                              # too short to mean anything
assert not im.supported('rain/heavy', '', text, 'silence')
assert im.supported('rain/heavy', 'same scene', 'They talked.', 'rain/heavy')
assert not im.supported('fire/hearth', 'same scene', 'They talked.', 'rain/heavy')
assert im.supported('silence', '', text, 'rain/heavy')

calls = []
def fake(cfg, system, user):
    calls.append(user)
    if len(calls) == 1:
        return 'oops not json'  # exercises the retry
    out = []
    for seg in user.split('\n[')[1:]:
        if 'Title page' in seg: out.append({'label': 'silence', 'evidence': ''})
        elif 'rain' in seg: out.append({'label': 'rain/heavy', 'evidence': 'The rain hammered down on the roof'})
        else: out.append({'label': 'fire/hearth', 'evidence': 'They sat by the crackling fire'})
    return 'Sure! ' + json.dumps({'passages': out})

res = im.label_chunks(chunks, labels, {'model': 'fake'}, llm=fake, log=lambda *a: None)
sc = im.scenes(chunks, res)
assert [s['sound'] for s in sc] == ['silence', 'rain/heavy', 'fire/hearth'], sc
assert sc[1] == {'frag': 2, 'at': 0, 'sound': 'rain/heavy'}

# "missing" requests are counted for the report, and never become a sound.
import collections
miss = collections.Counter()
asks = lambda cfg, s, u: json.dumps({'passages': [{'label': 'silence', 'evidence': '', 'missing': 'Blacksmith forge '},
                                                  {'label': 'silence', 'evidence': '', 'missing': 'blacksmith forge'}]})
assert im.label_chunks(chunks[1:3], labels, {}, llm=asks, log=lambda *a: None, missing=miss) == ['silence', 'silence']
assert miss == {'blacksmith forge': 2}

# A model that invents quotes or labels gets silence, not a guess.
liar = lambda cfg, s, u: json.dumps({'passages': [{'label': 'rain/heavy', 'evidence': 'thunder split the sky above them'},
                                                  {'label': 'sea/storm', 'evidence': 'The rain hammered down on the roof'}]})
assert im.label_chunks(chunks[1:3], labels, {}, llm=liar, log=lambda *a: None) == ['silence', 'silence']
# A batch that never parses is left silent.
assert set(im.label_chunks(chunks[:2], labels, {}, llm=lambda *a: 'nope', log=lambda *a: None)) == {'silence'}

# One-passage blips fold into the scene before them; real scenes stay.
assert im.smooth(['silence', 'sea/calm', 'sea/calm', 'rain/heavy', 'sea/calm', 'silence', 'silence']) == \
    ['silence', 'sea/calm', 'sea/calm', 'sea/calm', 'sea/calm', 'silence', 'silence']
assert im.smooth(['fire/hearth', 'silence', 'silence']) == ['fire/hearth', 'silence', 'silence']
assert im.smooth(['silence', 'silence', 'rain/light']) == ['silence', 'silence', 'silence']
assert 'most passages should be "silence"' in im.SYSTEM

# Embedding: the scenes ride inside the EPUB, mimetype stays first and stored, re-embedding replaces.
data = {'version': 1, 'title': 'Test Book', 'scenes': sc}
out = os.path.join(d, 'out.epub')
im.embed(epub, out, data)
assert im.embedded(out) == data and im.embedded(epub) is None
with zipfile.ZipFile(out) as z:
    assert z.infolist()[0].filename == 'META-INF/container.xml'  # make_epub has no mimetype; order kept
    assert [i.filename for i in z.infolist()].count(im.EMBED_PATH) == 1
assert im.read_epub(out)[0] == 'Test Book'
data2 = dict(data, scenes=[])
im.embed(out, out, data2)  # in place
assert im.embedded(out) == data2 and not os.path.exists(out + '.kambience.tmp')
with zipfile.ZipFile(out) as z:
    assert [i.filename for i in z.infolist()].count(im.EMBED_PATH) == 1

# Books tagged before the rename (old file name inside the EPUB) are still read, and re-tagging
# replaces the old entry instead of leaving both.
old = os.path.join(d, 'old.epub')
with zipfile.ZipFile(epub) as zin, zipfile.ZipFile(old, 'w') as zout:
    for i in zin.infolist():
        zout.writestr(i, zin.read(i.filename))
    zout.writestr('META-INF/kimmersive.json', json.dumps(data))
assert im.embedded(old) == data
im.embed(old, old, data2)
with zipfile.ZipFile(old) as z:
    assert 'META-INF/kimmersive.json' not in z.namelist() and im.embedded(old) == data2

# Resume: a run stopped after the first batch picks up from the saved labels, without asking again.
saved, sent = [], []
def killed_after_first_batch(cfg, s, u):
    if saved:
        raise KeyboardInterrupt  # the job is stopped mid-book
    sent.append(u)
    return fake(cfg, s, u)
calls.clear(); calls.append('already past the retry test')
real_llm, im.call_llm = im.call_llm, killed_after_first_batch
cfg_small = {'model': 'm', 'batch_chars': 3000}  # several batches for this small book
try:
    im.run(epub, labels, cfg_small, log=lambda *a: None,
           on_batch=lambda done, miss: saved.append({'labels': list(done), 'missing': dict(miss)}))
except KeyboardInterrupt:
    pass
assert saved and 0 < len(saved[-1]['labels']) < len(chunks), saved
first_done = len(saved[-1]['labels'])
sent.clear(); saved_at_stop = saved[-1]; saved.clear()
im.call_llm = lambda cfg, s, u: (sent.append(u), fake(cfg, s, u))[1]
data3, _ = im.run(epub, labels, cfg_small, log=lambda *a: None, resume=saved_at_stop)
im.call_llm = real_llm
assert [s['sound'] for s in data3['scenes']] == ['silence', 'rain/heavy', 'fire/hearth'], data3
assert 'Title page' not in ''.join(sent)  # passages done before the stop were not sent again
assert sum(u.count('\n[') for u in sent) == len(chunks) - first_done

# Partial runs: the first 40% only, then continue from what was embedded, without re-reading.
sent.clear()
im.call_llm = lambda cfg, s, u: (sent.append(u.count('\n[')), fake(cfg, s, u))[1]
half, _ = im.run(epub, labels, cfg_small, log=lambda *a: None, percent=40)
n = len(chunks)
assert len(half['labels']) == -(-n * 40 // 100) and half['passages'] == n and 0 < half['coverage'] < 100
assert sum(sent) == len(half['labels'])
assert half['scenes'][-1]['sound'] in ('silence', 'rain/heavy')  # nothing beyond what was read gets sound
assert 'fire/hearth' not in [s['sound'] for s in half['scenes']]  # the fire chapter wasn't read yet
sent.clear()
whole, _ = im.run(epub, labels, cfg_small, log=lambda *a: None, resume=half, percent=100)
assert whole['coverage'] == 100 and sum(sent) == n - len(half['labels'])  # only the rest was read
assert [s['sound'] for s in whole['scenes']] == ['silence', 'rain/heavy', 'fire/hearth']
same, _ = im.run(epub, labels, cfg_small, log=lambda *a: None, resume=whole, percent=10)
assert same['coverage'] == 100  # a lower % never throws away passages already done
im.call_llm = real_llm

# OpenAI-compatible HTTP path against a local fake server.
seen = {}
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        seen['path'], seen['auth'] = self.path, self.headers.get('Authorization')
        seen['body'] = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        out = json.dumps({'choices': [{'message': {'content': '{"passages": [{"label": "silence", "evidence": ""}]}'}}]}).encode()
        self.send_response(200); self.send_header('Content-Length', str(len(out))); self.end_headers(); self.wfile.write(out)
    def log_message(self, *a): pass
srv = HTTPServer(('127.0.0.1', 0), H)
threading.Thread(target=srv.handle_request, daemon=True).start()
cfg = {'api': 'openai', 'base_url': f'http://127.0.0.1:{srv.server_port}/v1/', 'model': 'm', 'api_key': 'k'}
assert im.parse_labels(im.call_llm(cfg, 'sys', 'usr'), 1) == [('silence', '', '')]
assert seen['path'] == '/v1/chat/completions' and seen['auth'] == 'Bearer k' and seen['body']['model'] == 'm'
print('ok', sc)

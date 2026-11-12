#!/usr/bin/env python3
"""kambience: tag an EPUB's scenes with ambient sound labels.

Writes <book>.kambience.json next to the EPUB for the kambience KOReader plugin.
Positions are (DocFragment index, fraction through that fragment's text), which is
what the plugin can compute from KOReader without guessing crengine DOM paths.
"""
import argparse, collections, json, os, posixpath, re, sys, urllib.request, zipfile
from html.parser import HTMLParser
from xml.etree import ElementTree as ET

CHUNK_CHARS = 1500      # about one Kindle page of text
BATCH_CHARS = 24000     # text per LLM request; cfg['batch_chars'] overrides (local models with small contexts)
AUDIO_EXT = ('.mp3', '.flac', '.wav', '.ogg', '.m4a')


# ---------- EPUB ----------

class _Text(HTMLParser):
    BLOCK = {'p', 'div', 'br', 'li', 'blockquote', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'tr', 'section'}
    SKIP = {'script', 'style', 'head', 'title'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.paras, self.cur, self.skip = [], [], 0

    def _flush(self):
        t = ' '.join(''.join(self.cur).split())
        if t:
            self.paras.append(t)
        self.cur = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP: self.skip += 1
        elif tag in self.BLOCK: self._flush()

    def handle_endtag(self, tag):
        if tag in self.SKIP: self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK: self._flush()

    def handle_data(self, data):
        if not self.skip: self.cur.append(data)


def read_epub(path):
    """Returns (title, [paragraph lists]) in spine order; index 0 is DocFragment[1]."""
    z = zipfile.ZipFile(path)
    container = ET.fromstring(z.read('META-INF/container.xml'))
    opf_path = container.find('.//{*}rootfile').get('full-path')
    opf = ET.fromstring(z.read(opf_path))
    base = posixpath.dirname(opf_path)
    title_el = opf.find('.//{http://purl.org/dc/elements/1.1/}title')
    manifest = {i.get('id'): i.get('href') for i in opf.find('{*}manifest')}
    frags = []
    for ref in opf.find('{*}spine'):
        href = urllib.request.unquote(manifest[ref.get('idref')])
        p = _Text()
        p.feed(z.read(posixpath.normpath(posixpath.join(base, href))).decode('utf-8', 'replace'))
        p._flush()
        frags.append(p.paras)
    return (title_el.text if title_el is not None else os.path.basename(path)), frags


def chunk(frags):
    """Splits fragments into ~CHUNK_CHARS passages: [{'frag', 'at', 'text'}]."""
    out = []
    for fi, paras in enumerate(frags, 1):
        total = sum(len(p) for p in paras)
        pos, buf, start = 0, [], 0
        for p in paras:
            if not buf: start = pos
            buf.append(p)
            pos += len(p)
            if sum(map(len, buf)) >= CHUNK_CHARS:
                out.append({'frag': fi, 'at': round(start / total, 4), 'text': '\n'.join(buf)})
                buf = []
        if buf:
            out.append({'frag': fi, 'at': round(start / total, 4) if total else 0, 'text': '\n'.join(buf)})
    return out


# ---------- sound labels ----------

def load_labels(sounds_dir):
    """Only labels that have an audio file in the pack (the AI is never offered a sound that
    can't play), described from labels.txt, plus silence."""
    desc = {}
    packs = os.path.join(sounds_dir, 'packs')
    txts = [os.path.join(sounds_dir, 'labels.txt')] + \
        [os.path.join(packs, p, 'labels.txt') for p in (sorted(os.listdir(packs)) if os.path.isdir(packs) else [])]
    for txt in filter(os.path.exists, txts):
        for line in open(txt, encoding='utf-8'):
            line = line.strip()
            if line and not line.startswith('#'):
                name, _, d = line.partition(' ')
                desc[name] = d.strip()
    labels = {}
    for root, _, files in os.walk(sounds_dir):
        for f in files:
            if f.lower().endswith(AUDIO_EXT):
                name = os.path.splitext(os.path.relpath(os.path.join(root, f), sounds_dir))[0].replace(os.sep, '/')
                labels[name] = desc.get(name, '')
    labels['silence'] = 'No ambience (the default)'
    return labels


def fix_label(label, labels):
    """Exact labels only. Anything else, even a near miss, is silence."""
    label = (label or '').strip().strip('/').lower()
    return label if label in labels else 'silence'


def _words(s):
    return ' '.join(re.findall(r"[a-z0-9]+", s.lower()))


def supported(label, evidence, text, prev):
    """A sound must be backed by a quote that really is in the passage, or continue the
    previous passage's identical sound as 'same scene'."""
    if label == 'silence':
        return True
    ev = _words(evidence or '')
    if ev == 'same scene':
        return label == prev
    return len(ev) >= 8 and ev in _words(text)


# ---------- LLM providers ----------

SYSTEM = """You choose subtle ambient background sounds for passages of a novel. A wrong sound \
breaks immersion far more than no sound, so be strict: most passages should be "silence".

For each numbered passage, pick a sound ONLY when ALL of these hold:
- The passage itself states where the characters physically are right now, and that place or \
weather has a strong sound of its own (a storm, the sea, a busy tavern, a crackling fire).
- One label below matches it exactly: the right place, indoors vs outdoors, weather and intensity. \
If the best label is only roughly right (e.g. a library scene but no library label), use "silence".
- It is happening now, not remembered, imagined, described in dialogue, or mentioned in passing. \
Weather that is only approaching, smelled or expected ("a storm was coming") is not happening yet.
Use "silence" for dialogue-driven or introspective passages, exposition, letters, dreams, ordinary \
quiet rooms, and whenever you are unsure.

For every sound, give evidence: a short EXACT quote (5-15 words, copied character for character) \
from that passage that shows the setting. If the passage continues the same setting as the passage \
right before it with no change, you may keep the identical label with evidence "same scene". \
For "silence", evidence is "".

If a passage clearly has a vivid setting with a strong sound but NO label fits it, use "silence" \
and name what is missing in 1-4 plain words (e.g. "missing": "blacksmith forge"). Otherwise \
"missing" is "".

Available labels (use these exact strings):
{labels}

Reply with only JSON:
{{"passages": [{{"label": "...", "evidence": "...", "missing": ""}}, ...]}}
with exactly one entry per passage, in order."""


def call_llm(cfg, system, user):
    if cfg['api'] == 'anthropic':
        import anthropic  # pip install anthropic
        client = anthropic.Anthropic(api_key=cfg.get('api_key'))  # None -> SDK's own credential lookup
        r = client.beta.messages.create(
            model=cfg['model'], max_tokens=16000, system=system,
            messages=[{'role': 'user', 'content': user}],
            output_config={'effort': cfg.get('effort', 'low')},  # simple classification
            betas=['server-side-fallback-2026-07-01'], fallbacks='default',
        )
        if r.stop_reason == 'refusal':
            raise RuntimeError('model refused this batch')
        return ''.join(b.text for b in r.content if b.type == 'text')
    # Everything else speaks the OpenAI chat/completions format.
    req = urllib.request.Request(
        cfg['base_url'].rstrip('/') + '/chat/completions',
        data=json.dumps({'model': cfg['model'], 'temperature': 0,
                         'messages': [{'role': 'system', 'content': system},
                                      {'role': 'user', 'content': user}]}).encode(),
        headers={'Content-Type': 'application/json',
                 **({'Authorization': 'Bearer ' + cfg['api_key']} if cfg.get('api_key') else {})})
    with urllib.request.urlopen(req, timeout=600) as resp:
        return json.load(resp)['choices'][0]['message']['content']


def parse_labels(text, n):
    """[(label, evidence, missing)] for n passages."""
    m = re.search(r'\{.*\}', text, re.S)
    got = json.loads(m.group(0))['passages'] if m else None
    if not isinstance(got, list) or len(got) != n:
        raise ValueError(f'expected {n} passages, got {got!r:.200}')
    return [(str(p.get('label', '')), str(p.get('evidence', '')), str(p.get('missing') or '')) for p in got]


def label_chunks(chunks, labels, cfg, llm=None, log=print, missing=None, done=None, on_batch=None):
    """Per-passage labels. If `missing` (a Counter) is given, it counts settings the model said
    had no fitting sound, which is what to add to the pack next. `done` holds labels already
    worked out (to resume), and on_batch(labels, missing) is called after every batch to save them."""
    llm = llm or call_llm  # looked up at call time, so callers and tests can swap it
    batch_chars = cfg.get('batch_chars') or BATCH_CHARS
    system = SYSTEM.format(labels='\n'.join(f'- {k}: {v}' if v else f'- {k}' for k, v in sorted(labels.items())))
    result = list(done or [])
    prev, i = (result[-1] if result else 'silence'), len(result)
    while i < len(chunks):
        batch, size = [], 0
        while i + len(batch) < len(chunks) and (not batch or size + len(chunks[i + len(batch)]['text']) <= batch_chars):
            size += len(chunks[i + len(batch)]['text'])
            batch.append(chunks[i + len(batch)])
        user = f'The previous passage was labelled "{prev}".\n\n' + '\n\n'.join(
            f'[{k + 1}]\n{c["text"]}' for k, c in enumerate(batch))
        for attempt in range(3):
            try:
                got = parse_labels(llm(cfg, system, user), len(batch))
                break
            except (ValueError, KeyError, json.JSONDecodeError) as e:
                log(f'  retrying batch ({e})')
        else:
            log('  batch failed 3 times, leaving it silent')
            got = [('silence', '', '')] * len(batch)
        for c, (l, ev, miss) in zip(batch, got):
            if missing is not None and miss.strip():
                missing[miss.strip().lower()] += 1
            l = fix_label(l, labels)
            if not supported(l, ev, c['text'], prev):
                l = 'silence'
            prev = l
            result.append(l)
        i += len(batch)
        log(f'  {i}/{len(chunks)} passages')
        if on_batch:
            on_batch(result, missing)
    return result


MIN_RUN = 2  # passages; shorter scenes are blips that would fade in and straight back out


def smooth(result):
    """Folds runs shorter than MIN_RUN (except the first) into the scene before them."""
    runs = []
    for l in result:
        if runs and runs[-1][0] == l: runs[-1][1] += 1
        else: runs.append([l, 1])
    for i in range(1, len(runs)):
        if runs[i][1] < MIN_RUN:
            runs[i][0] = runs[i - 1][0]
    return [l for l, n in runs for _ in range(n)]


def scenes(chunks, result):
    """Collapses per-passage labels into change points."""
    out = []
    for c, l in zip(chunks, smooth(result)):
        if not out or out[-1]['sound'] != l:
            out.append({'frag': c['frag'], 'at': c['at'], 'sound': l})
    return out


# ---------- CLI ----------

def load_config(path):
    cfg = json.load(open(path, encoding='utf-8'))
    if cfg.get('api_key_env'):
        cfg['api_key'] = os.environ.get(cfg['api_key_env'])
        if not cfg['api_key'] and cfg.get('api') != 'anthropic':
            sys.exit(f'Set the {cfg["api_key_env"]} environment variable with your API key.')
    cfg.setdefault('api', 'openai')
    return cfg


EMBED_PATH = 'META-INF/kambience.json'  # inside the EPUB zip; readers ignore unknown META-INF files
OLD_EMBED_PATHS = ('META-INF/kimmersive.json',)  # books tagged before the rename
MARK = 'kambience'  # tag (dc:subject / Calibre tag) on books with sound
TITLE_SUFFIX = ' [kambience]'


def mark_opf(opf):
    """Adds TITLE_SUFFIX to the first title and a MARK subject. Text edits, not ElementTree, which
    would rewrite the namespace prefixes. Idempotent."""
    m = re.search(r'<((?:[\w-]+:)?)title\b[^>]*>(.*?)</\1title>', opf, re.S)
    if not m:
        return opf
    dc = m.group(1)
    if not m.group(2).endswith(TITLE_SUFFIX):
        opf = opf[:m.end(2)] + TITLE_SUFFIX + opf[m.end(2):]
    subject = f'<{dc}subject>{MARK}</{dc}subject>'
    if subject not in opf:
        opf = re.sub(r'(</(?:[\w-]+:)?metadata>)', lambda e: subject + e.group(1), opf, count=1)
    return opf


def run(epub, labels, cfg, log=print, resume=None, on_batch=None, percent=100):
    """Tags one book. Returns (result dict for the .json / embedding, Counter of missing sounds).
    resume: {'labels': [...], 'missing': {...}} from on_batch, or the data embedded by an earlier
    partial run, to pick up where it stopped. percent: only read the first part of the book (for
    testing, or doing a long book in parts); the rest stays silent until a later run extends it."""
    title, frags = read_epub(epub)
    chunks = chunk(frags)
    n = len(chunks)
    upto = n if percent >= 100 else max(1, -(-n * percent // 100))  # ceiling
    done = list((resume or {}).get('labels') or [])
    if len(done) > n:  # the book changed since; start over
        done = []
    upto = max(upto, len(done))  # never throw away passages already done
    part = '' if upto == n else f' (the first {percent}% of {n})'
    log(f'{title}: {upto} passages{part}, {len(labels) - 1} sounds, model {cfg["model"]}')
    missing = collections.Counter((resume or {}).get('missing') or {} if done else {})
    if done and len(done) < upto:
        log(f'resuming at passage {len(done) + 1}')
    got = label_chunks(chunks[:upto], labels, cfg, log=log, missing=missing, done=done, on_batch=on_batch)
    sc = scenes(chunks, got + ['silence'] * (n - len(got)))
    # 'labels' lets a later run continue a partial book; 'coverage' is how much of it has been read.
    return {'version': 1, 'title': title, 'scenes': sc, 'passages': n, 'labels': got,
            'coverage': 100 if len(got) == n else min(99, 100 * len(got) // n)}, missing


def embed(src, dst, data):
    """Copies the EPUB with `data` stored at EMBED_PATH, replacing an earlier one. src may equal dst:
    the copy goes to a temp file first and is moved over dst only once complete."""
    tmp = dst + '.kambience.tmp'
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(tmp, 'w') as zout:
        opf_path = ET.fromstring(zin.read('META-INF/container.xml')).find('.//{*}rootfile').get('full-path')
        for info in zin.infolist():  # keeps order, so 'mimetype' stays first and uncompressed
            if info.filename in (EMBED_PATH, *OLD_EMBED_PATHS):
                continue
            body = zin.read(info.filename)
            if info.filename == opf_path:
                body = mark_opf(body.decode('utf-8')).encode('utf-8')
            zout.writestr(info, body)
        zout.writestr(EMBED_PATH, json.dumps(data, indent=1), compress_type=zipfile.ZIP_DEFLATED)
    with zipfile.ZipFile(tmp) as check:  # never replace a book with a broken file
        if check.testzip() is not None or EMBED_PATH not in check.namelist():
            os.remove(tmp)
            raise ValueError('rewritten EPUB failed its check; original left untouched')
    os.replace(tmp, dst)


def embedded(epub):
    """The data embedded in an EPUB (current or pre-rename location), or None."""
    try:
        with zipfile.ZipFile(epub) as z:
            names = set(z.namelist())
            for p in (EMBED_PATH, *OLD_EMBED_PATHS):
                if p in names:
                    return json.loads(z.read(p))
    except (OSError, ValueError, zipfile.BadZipFile):
        pass
    return None


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description='Tag an EPUB with ambient sound scenes for kambience.')
    ap.add_argument('epub')
    ap.add_argument('-c', '--config', default=os.path.join(here, 'config.json'))
    ap.add_argument('-s', '--sounds', default=os.path.join(here, '..', 'sounds'))
    ap.add_argument('-o', '--out', help='default: next to the EPUB as <name>.kambience.json')
    ap.add_argument('--embed', action='store_true', help='also store the scenes inside the EPUB itself')
    ap.add_argument('--percent', type=int, default=100,
                    help='only read the first N%% of the book (testing); re-run with a higher N to continue')
    a = ap.parse_args()

    cfg = load_config(a.config)
    labels = load_labels(a.sounds)
    if len(labels) == 1:
        sys.exit(f'No sound files in {a.sounds}. Run sounds/fetch_sounds.py first.')
    if not 1 <= a.percent <= 100:
        sys.exit('--percent must be between 1 and 100')
    data, missing = run(a.epub, labels, cfg, resume=embedded(a.epub), percent=a.percent)
    sc = data['scenes']
    out = a.out or os.path.splitext(a.epub)[0] + '.kambience.json'
    json.dump(data, open(out, 'w', encoding='utf-8'), indent=1)
    print(f'{len(sc)} scene changes -> {out}')
    if a.embed:
        embed(a.epub, a.epub, data)
        print(f'embedded in {a.epub}')
    if missing:
        print('\nSettings with no matching sound (passages), worth adding to the pack:')
        for what, n in missing.most_common(15):
            print(f'  {n:4}  {what}')


if __name__ == '__main__':
    main()

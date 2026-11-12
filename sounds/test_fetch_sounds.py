# Offline checks for fetch_sounds.py: python3 test_fetch_sounds.py
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_sounds as fs

assert fs.license_name('http://creativecommons.org/publicdomain/zero/1.0/') == 'CC0'
assert fs.license_name('https://creativecommons.org/licenses/by/4.0/') == 'CC BY'
for bad in ['http://creativecommons.org/licenses/by-nc/3.0/', 'https://creativecommons.org/licenses/by-nc-sa/4.0/',
            'https://creativecommons.org/licenses/sampling+/1.0/', 'https://creativecommons.org/licenses/by-nd/4.0/']:
    assert fs.license_name(bad) is None, bad

page = '''<html><head><meta property="og:title" content="Rain on tent &amp; tarp">
<meta property="og:audio" content="https://cdn.freesound.org/previews/320/320338_5121236-lq.mp3"></head>
<body><a href="/people/guyburns/sounds/320338/">x</a>
<a href="http://creativecommons.org/publicdomain/zero/1.0/">Creative Commons 0</a></body></html>'''
info = fs.parse_page(page, 'https://freesound.org/people/guyburns/sounds/320338/')
assert info['license'] == 'CC0' and info['author'] == 'guyburns' and info['title'] == 'Rain on tent & tarp'
assert info['preview'] == 'https://cdn.freesound.org/previews/320/320338_5121236-hq.mp3'   # lq -> hq
nc = page.replace('publicdomain/zero/1.0', 'licenses/by-nc/4.0')
assert fs.parse_page(nc, 'https://freesound.org/people/guyburns/sounds/320338/')['license'] is None
assert fs.parse_page('<html>login wall</html>', 'https://freesound.org/s/1/') is None

# sources.json covers every label, and mixes only reference real labels.
# Core and every pack: sources cover exactly that catalog's labels, and mixes only layer real,
# non-mix labels (packs may layer core ones).
here = os.path.dirname(os.path.abspath(__file__))
def catalog(d):
    return [l.split()[0] for l in open(os.path.join(d, 'labels.txt')) if l.strip() and not l.startswith('#')]
def own(d):
    return {k for k in json.load(open(os.path.join(d, 'sources.json'))) if not k.startswith('_')}
core = set(catalog(here)) - {'silence'}
assert own(here) == core, own(here) ^ core
packs = os.listdir(os.path.join(here, 'packs'))
assert 'fantasy' in packs
for p in packs:
    d = os.path.join(here, 'packs', p)
    assert own(d) == set(catalog(d)), (p, own(d) ^ set(catalog(d)))
    assert not own(d) & core, (p, own(d) & core)   # packs add labels, never redefine core ones
src = fs.load_sources(packs)
for k, v in src.items():
    if isinstance(v, dict):
        assert all(x in src and not isinstance(src[x], dict) for x in v['mix']), k
    else:
        assert v and all(isinstance(i, int) for i in v), k
print('ok')

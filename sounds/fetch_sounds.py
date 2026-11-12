#!/usr/bin/env python3
"""Downloads the sound pack from Freesound.org. No account or API key needed.

sources.json lists hand-picked Freesound sound IDs per label (best first). For each label
with no file yet, this opens the sound's public page, checks the license (CC0 or CC BY only,
never NonCommercial), downloads its preview MP3, loops/trims it to 3 minutes with ffmpeg, and
credits it in CREDITS.md. {"mix": [a, b]} entries layer two finished labels.

  python3 fetch_sounds.py --dry-run     # check pages and licenses only
  python3 fetch_sounds.py               # download (needs ffmpeg)
  python3 fetch_sounds.py rain/heavy    # only these labels
  python3 fetch_sounds.py --pack fantasy   # core sounds plus an extra pack (packs/<name>/)
"""
import argparse, html, json, os, re, shutil, subprocess, sys, tempfile, time, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
UA = {'User-Agent': 'Mozilla/5.0 (kambience sound pack fetcher)'}
AUDIO_EXT = ('.mp3', '.flac', '.wav', '.ogg', '.m4a')
LOOP_SECONDS = 180


def has_file(label):
    return any(os.path.exists(os.path.join(HERE, label + e)) for e in AUDIO_EXT)


def get(url, timeout=60):
    for attempt in range(5):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code not in (429, 503) or attempt == 4:
                raise
            wait = int(e.headers.get('Retry-After') or 0) or 10 * 2 ** attempt
            print(f'  rate limited, waiting {wait} s')
            time.sleep(wait)


def license_name(url):
    """CC0 / CC BY only. NonCommercial, NoDerivs and Sampling+ are rejected."""
    if 'publicdomain/zero' in url:
        return 'CC0'
    m = re.search(r'/licenses/([a-z+-]+)/', url)
    return 'CC BY' if m and m.group(1) == 'by' else None


def parse_page(page, final_url):
    """Pulls what we need out of a Freesound sound page. None if anything is missing."""
    lic = re.search(r'https?://creativecommons\.org/(?:licenses|publicdomain)/[^"\'\s<>]+', page)
    prev = re.findall(r'https://cdn\.freesound\.org/previews/\d+/\d+_\d+-(?:hq|lq)\.mp3', page)
    title = re.search(r'<meta property="og:title" content="([^"]*)"', page) or re.search(r'<title>([^<]*)', page)
    user = re.search(r'/people/([^/]+)/sounds/\d+', final_url) or re.search(r'/people/([^/"]+)/sounds/\d+', page)
    if not (lic and prev and user):
        return None
    hq = next((p for p in prev if p.endswith('-hq.mp3')), prev[0].replace('-lq.mp3', '-hq.mp3'))
    return {'license': license_name(lic.group(0)), 'license_url': lic.group(0), 'preview': hq,
            'author': urllib.request.unquote(user.group(1)),
            'title': html.unescape(title.group(1)).strip() if title else '?'}


def lookup(sound_id):
    url = f'https://freesound.org/s/{sound_id}/'
    with get(url) as r:
        info = parse_page(r.read().decode('utf-8', 'replace'), r.geturl())
    if info:
        info['url'] = f'https://freesound.org/people/{info["author"]}/sounds/{sound_id}/'
    return info


def ffmpeg(args):
    subprocess.run(['ffmpeg', '-v', 'error', '-y'] + args, check=True)


SEAM = 4  # seconds of crossfade that hide the loop point


def to_loop(src, out):
    """Seamless 3-minute loop: the audio just past the cut is crossfaded over the start, so the
    end flows straight back into the beginning with no gap or volume dip. Short clips repeat."""
    L, X = LOOP_SECONDS, SEAM
    wav = out + '.tmp.wav'  # MP3 padding makes -stream_loop drift; WAV repeats exactly
    ffmpeg(['-i', src, '-t', str(L + X), '-ac', '2', '-ar', '44100', wav])
    try:
        _loop_wav(wav, out, L, X)
    finally:
        os.remove(wav)


def _loop_wav(wav, out, L, X):
    ffmpeg(['-stream_loop', '-1', '-i', wav, '-t', str(L + X), '-filter_complex',
            f'[0:a]aformat=sample_rates=44100:channel_layouts=stereo,asplit=3[a][b][c];'
            f'[a]atrim=0:{X},asetpts=PTS-STARTPTS[head];'
            f'[b]atrim={X}:{L},asetpts=PTS-STARTPTS[body];'
            f'[c]atrim={L}:{L + X},asetpts=PTS-STARTPTS[tail];'
            # Equal-power curves: a linear crossfade dips ~3 dB on noise-like sounds such as rain.
            f'[tail][head]acrossfade=d={X}:c1=qsin:c2=qsin[seam];[seam][body]concat=n=2:v=0:a=1',
            '-b:a', '96k', out])


# Previews are ~128 kbps, so 3.5 MB covers the LOOP_SECONDS + SEAM (~2.9 MB) we use. Long field
# recordings can be 30+ minutes; there is no point pulling all of that from a slow CDN.
MAX_DOWNLOAD = int(3.5 * 1024 * 1024)


def fetch(info, out):
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, 'src.mp3')
        with get(info['preview'], timeout=300) as r, open(src, 'wb') as f:
            left = MAX_DOWNLOAD
            while left > 0:
                block = r.read(min(65536, left))
                if not block:
                    break
                f.write(block)
                left -= len(block)
        to_loop(src, out)


def mix(parts, out):
    srcs = [os.path.join(HERE, p + '.mp3') for p in parts]
    args = sum((['-i', s] for s in srcs), [])
    ffmpeg(args + ['-filter_complex', f'amix=inputs={len(srcs)}:duration=longest:normalize=0,volume=0.8',
                   '-ac', '2', '-ar', '44100', '-b:a', '96k', out])


def load_sources(packs=()):
    """Core sources.json plus packs/<name>/sources.json for each requested pack."""
    paths = [os.path.join(HERE, 'sources.json')] + [os.path.join(HERE, 'packs', p, 'sources.json') for p in packs]
    out = {}
    for path in paths:
        if not os.path.exists(path):
            sys.exit(f'No such pack: {path}')
        out.update({k: v for k, v in json.load(open(path)).items() if not k.startswith('_')})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('only', nargs='*', help='labels to fetch (default: all missing)')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--pack', action='append', default=[], help='also fetch packs/<name> (repeatable)')
    a = ap.parse_args()
    if not a.dry_run and not shutil.which('ffmpeg'):
        sys.exit('ffmpeg is needed: sudo apt install ffmpeg')

    sources = load_sources(a.pack)
    credits = os.path.join(HERE, 'CREDITS.md')
    if not os.path.exists(credits):
        open(credits, 'w').write('# Sound credits\n\n| File | Source URL | Author | License |\n|------|------------|--------|---------|\n')
    used = {int(i) for i in re.findall(r'freesound\.org/people/[^/]+/sounds/(\d+)', open(credits).read())}
    todo = [l for l in sources if (not a.only or l in a.only) and not has_file(l)]
    # Mixes last, so the labels they layer exist already.
    todo.sort(key=lambda l: isinstance(sources[l], dict))
    failed = []
    for label in todo:
        out = os.path.join(HERE, label + '.mp3')
        os.makedirs(os.path.dirname(out), exist_ok=True)
        src = sources[label]
        if isinstance(src, dict):
            missing = [p for p in src['mix'] if not has_file(p)]
            if missing or a.dry_run:
                print(f'{"--" if missing else "ok"}  {label}: mix of {" + ".join(src["mix"])}'
                      + (f' (missing {" ".join(missing)})' if missing else ''))
                failed += [label] if missing else []
                continue
            mix(src['mix'], out)
            print(f'ok  {label}: mixed {" + ".join(src["mix"])}')
            continue
        done = False
        for sid in src:
            if sid in used:
                continue
            try:
                info = lookup(sid)
            except (OSError, ValueError) as e:
                print(f'  {label}: sound {sid} unavailable ({e})')
                continue
            finally:
                time.sleep(1)
            if not info or not info['license']:
                why = 'page format not recognised' if not info else f'license not allowed ({info["license_url"]})'
                print(f'  {label}: skipping {sid}, {why}')
                continue
            print(f'ok  {label}: "{info["title"]}" by {info["author"]} ({info["license"]})', flush=True)
            if not a.dry_run:
                try:
                    fetch(info, out)
                except (OSError, subprocess.CalledProcessError) as e:
                    print(f'  {label}: download failed ({e})')
                    continue
                with open(credits, 'a', encoding='utf-8') as f:
                    f.write(f'| {label}.mp3 | {info["url"]} | {info["author"]} | {info["license"]} |\n')
            used.add(sid)
            done = True
            break
        if not done:
            failed.append(label)
            print(f'--  {label}: no usable candidate')
    if failed:
        print(f'\n{len(failed)} labels have no sound, so they will never be used: {" ".join(failed)}')


if __name__ == '__main__':
    main()

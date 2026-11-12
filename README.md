# kambience

Ambient sounds that follow the story while you read in KOReader on a jailbroken Kindle.

1. **kambience** (on your computer) reads an EPUB, asks an AI of your choice which ambience fits
   each page, and writes `<book>.kambience.json` next to the book.
2. **kambience.koplugin** (in KOReader) watches where you are in the book and tells
   [KinAMP](https://github.com/kbarni/KinAMP) to loop the matching sound over Bluetooth.

## kambience web page (mattbot)

`docker compose up -d --build` serves it on `http://mattbot:8095` (also linked from the matt registry).

- **Choose a book** from the Calibre library, or upload an EPUB. It is read and tagged in the background.
- **Testing / doing it in parts:** Settings → "Read the first N % of each book". The rest stays silent;
  raise the number later and press *continue* on that book to carry on without re-reading anything.
  A stopped or failed job also resumes from its last batch. (CLI: `--percent N`.)
- **The sounds are embedded in the EPUB** (`META-INF/kambience.json`), so they travel with the book
  through Calibre-Web, OPDS or USB. Library books are updated in place; the untouched original is kept
  in `web/data/backups/`. Uploads can be downloaded, or sent to Calibre's ingest folder.
- **The reader** section picks the AI service, model and key. The key is stored in
  `web/data/settings.json` (mode 600) and never sent back to the page. There is no login: it is meant
  for the LAN / Tailscale only, like the other mattbot pages.

## 1. Tag a book from the command line

Needs Python 3.8+. No packages are required, except `anthropic` if you use Claude.

```sh
cd kambience
cp config.example.json config.json   # pick a provider from "_presets" and copy its values to the top
python3 kambience.py ~/Books/mybook.epub
```

- **Local and free:** Ollama, LM Studio or llama.cpp, with no API key.
- **Cloud:** Claude, OpenAI, OpenRouter, Groq or Gemini. Put your key in the environment variable named by
  `api_key_env` (for example `export ANTHROPIC_API_KEY=...`). Keys are never stored in the config.
- Any other service that offers an OpenAI-compatible `/chat/completions` endpoint works with `"api": "openai"`.

A typical novel is a few hundred passages, sent in batches of about 16.

## 2. Install on the Kindle

Type these in the Kindle search bar:

1. `;kpm add-repo https://kylep33.github.io/kile-kpm-repo/manifest.json`
2. `;kpm add-repo https://kindletweaks.github.io/Repo/manifest.json` (for KinAMP)
3. `;kpm update`, then `;kpm install kinamp` and `;kpm install kambience` (pulls in the sounds)
4. Pair your headphones in Settings → Wireless & Bluetooth, and check that KinAMP plays music on its own.
5. Put the tagged book on the Kindle. The scenes travel inside the EPUB, so no extra file is needed.

## Reading

You never open KinAMP. Everything happens in KOReader:

- Open a book with sound (one with a `.kambience.json`). The first time, a note says it has sounds.
  Sounds are **off** until you turn them on with **kambience → Play sounds for this book**
  (under the tools icon). The choice is remembered per book.
- For one-tap control, bind **Toggle kambience** to a gesture in KOReader's gesture settings.
- On turning on, the plugin checks for Bluetooth headphones. It switches the radio on if needed and
  waits up to 10 s for them to reconnect. If none connect, it says so and stays off. Pairing new
  headphones has to be done once in the Kindle's own Settings → Wireless & Bluetooth.
- Books without a `.kambience.json` are untouched: the menu entry is greyed out and nothing plays.

## Sounds

`sounds/labels.txt` is the catalog: 73 labels such as `rain/heavy`, `rain/indoor` and `sea/below-deck`.
The AI is only offered labels that have a sound file, and picks strictly:

- Silence is the default. A sound needs a setting the passage itself states, happening now, that
  one label fits exactly. A near miss (a library scene with no library sound) stays silent.
- Every sound must come with a short exact quote from the passage, and the kambience checks that
  the quote really is there. An invented quote or an unknown label becomes silence.
- A sound carries over to the next passage only when it is the identical sound, marked "same scene".
- One-page blips are dropped so a sound never fades in and straight back out.

- **Quick start:** `python3 sounds/fetch_sounds.py` (needs ffmpeg, no account or key). It downloads
  the hand-picked Freesound recordings listed in `sounds/sources.json`, double-checks each license on the
  sound's page (CC0 and CC BY only, never NonCommercial), loops each to 3 minutes, and writes `CREDITS.md`.
  A few labels are made by layering two others (e.g. `indoor/castle-hall` = church + hearth).
- **Packs** add sounds for a genre: `python3 sounds/fetch_sounds.py --pack fantasy` adds inns, roads,
  wagons, horses, forges, villages, bells and more (see `sounds/packs/fantasy/labels.txt`). A pack is a
  folder with its own `labels.txt` and `sources.json`; its files sit beside the core sounds.
- After tagging a book, the kambience lists vivid settings it had to leave silent because no sound
  fit. That list is the shopping list for the next sounds to add.
- To swap a sound, put a different Freesound ID first in its `sources.json` list, delete the old file and re-run.
- To fill a slot by hand, add `sounds/rain/heavy.mp3`. Short seamless loops of 1–3 minutes work best.
- A label with no file is never used: the kambience doesn't offer it, and the plugin plays silence.
- To add a label, add its file plus a line describing it in `labels.txt`, then re-run the kambience.
- KinAMP plays one sound at a time, so a layered sound (rain plus a fireplace) must be pre-mixed into one
  file, like `rain/indoor-fire`.
- Use CC0 or CC-BY sounds (for example freesound.org or pixabay.com/sound-effects) and record each one in
  `sounds/CREDITS.md`.

## Tests

```sh
python3 kambience/test_kambience.py               # no network or API key needed
pip install lupa && python3 kambience.koplugin/test_scenes.py
```

## Known limits

- EPUB and other reflowable books only, not PDF.
- Scene changes are accurate to about a page.
- Bluetooth streaming uses noticeably more battery.
- While a book with a `.kambience.json` is open, the plugin takes over KinAMP, so it replaces any music
  you were playing.

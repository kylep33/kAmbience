// kambience front end. No framework: a few fetches and some DOM.
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || `Something went wrong (${r.status}).`);
  return body;
}
const post = (path, data) => api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });

// ---------- tabs ----------
for (const tab of document.querySelectorAll('[role=tab]')) {
  tab.addEventListener('click', () => {
    for (const t of document.querySelectorAll('[role=tab]')) {
      const on = t === tab;
      t.setAttribute('aria-selected', on);
      $('#' + t.getAttribute('aria-controls')).hidden = !on;
    }
  });
}

// ---------- library ----------
let books = [];
function drawBooks() {
  const q = $('#q').value.trim().toLowerCase();
  const list = books.filter((b) => !q || (b.title + ' ' + b.author).toLowerCase().includes(q));
  $('#books').innerHTML = list.length ? list.map((b) => b.coverage >= 100 ? `
    <li class="is-done"><span class="t">${esc(b.title)}</span><span class="a">${esc(b.author)}</span>
      <span class="done-note">already has sound<br><button class="link" data-id="${b.id}" data-redo>redo</button></span></li>` : b.coverage > 0 ? `
    <li class="is-part"><span class="t">${esc(b.title)}</span><span class="a">${esc(b.author)}</span>
      <span class="done-note">sound in the first ${b.coverage}%<br><button class="link" data-id="${b.id}">continue</button>
        · <button class="link" data-id="${b.id}" data-redo>redo</button></span></li>` : `
    <li><span class="t">${esc(b.title)}</span><span class="a">${esc(b.author)}</span>
      <button class="btn ghost go" data-id="${b.id}">Add sound</button></li>`).join('')
    : `<li class="empty">${books.length ? 'No book matches that.' : 'No EPUBs in the Calibre library yet.'}</li>`;
}
async function loadBooks() {
  try { books = await api('/api/library'); drawBooks(); }
  catch (e) { $('#books').innerHTML = `<li class="empty">${esc(e.message)}</li>`; }
}
$('#q').addEventListener('input', drawBooks);
$('#books').addEventListener('click', async (e) => {
  const b = e.target.closest('button[data-id]');
  if (!b) return;
  const redo = 'redo' in b.dataset;
  if (redo && !confirm('This book already has sounds. Read it again from the start and replace them?')) return;
  b.disabled = true;
  try { await post('/api/jobs', { book_id: Number(b.dataset.id), fresh: redo }); await loadJobs(); $('#work').scrollIntoView({ behavior: 'smooth' }); }
  catch (err) { alert(err.message); }
  b.disabled = false;
});

// ---------- upload ----------
async function upload(file) {
  if (!file) return;
  if (!/\.epub$/i.test(file.name)) { alert('Only .epub files can have sound added.'); return; }
  const label = $('.drop-main'), was = label.textContent;
  label.textContent = `Uploading ${file.name}…`;
  try {
    await api('/api/upload?name=' + encodeURIComponent(file.name), { method: 'POST', body: file });
    await loadJobs();
    $('#work').scrollIntoView({ behavior: 'smooth' });
  } catch (e) { alert(e.message); }
  label.textContent = was;
  $('#file').value = '';
}
$('#file').addEventListener('change', (e) => upload(e.target.files[0]));
const drop = $('#drop');
['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('over'); }));
['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', (e) => upload(e.dataTransfer.files[0]));

// ---------- jobs ----------
const ago = (t) => {
  const s = Date.now() / 1000 - t;
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(t * 1000).toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
};
const pretty = (label) => label.replace('/', ' · ').replace(/-/g, ' ');

// Little remarks shown under the progress line, a new one every 5%.
const REMARKS = [
  'My what a good chapter!', 'Oh man, I did NOT see that coming.', "Something crazy just happened but I won't spoil it for you.",
  'Okay okay, one more chapter.', 'Wait, go back. Did that just happen?', 'I need a minute.', 'This is the good part.',
  "Ooh, someone's lying. I can feel it.", 'Not him again.', "I'm not crying, you're crying.", 'Ugh, cliffhanger.',
  'Okay that was rude of the author.', 'I would die for this character.', 'I would NOT die for this character.',
  "Bold move. Let's see if it pays off.", 'Somebody get this man a nap.', 'The tension in this room, my god.',
  "Oh, it's raining in the book too.", 'Called it. Totally called it.', "Didn't call that one.",
  "Shh, it's getting quiet.", 'Love a good tavern scene.', 'This villain kind of has a point, sadly.',
  'Okay that line was beautiful.', 'Hold on, rereading that sentence.', "Somebody's about to make a terrible decision.",
  'And they made the terrible decision.', 'The vibes are immaculate.', 'Mild peril. My favourite.',
  'This feels important. Remembering it.', 'Wow. Just wow.', 'I have so many questions.',
  'Okay I take back what I said earlier.', 'Snacks would really help right now.', 'This chapter is going places.',
  'Somebody hug this poor kid.', 'Wait, who is that?', 'Oh no. Oh no no no.', 'Okay, that was actually funny.',
  'The foreshadowing is getting loud.', 'Love when they finally talk to each other.', 'Why would you open that door.',
  'They opened the door.', 'Big feelings happening.', 'Not the dramatic pause.',
  "That's going to come back later, I just know it.", "Fire's crackling. Very cosy in here.",
  'This is a lot of walking. Good walking though.', 'Someone just said the title of the book!', 'Ahh, the slow burn.',
  'Okay, this author is showing off now.', "I'd read a whole book about this side character.",
  'Somewhere a bard is writing a song about this.', 'Things are about to get loud.', 'This is fine. Everything is fine.',
  'It is not fine.', 'New favourite chapter, sorry to the last one.', 'Yep. Still thinking about that last page.',
  'A map would be really handy right now.', 'The weather in this book is doing a lot.', 'Ooh, a secret.',
  "I'm sensing a training montage.", 'Gasped out loud. Embarrassing.', 'Who hurt this author.', 'That was a whole mood.',
  'Turning pages very fast now.', 'The quiet before something.', 'This dialogue is so good it should be illegal.',
  'Wait, is this a flashback?', 'Rooting for them so hard right now.', "Somebody's getting what they deserve.",
  'Okay, breathing again.', 'Hmm. Suspicious.', "I'd trust exactly nobody in this room.", 'That description? Perfect.',
  'I see what you did there, author.', 'They really said that out loud.', 'This one is a page turner, for real.',
  'Getting dark in here. Literally.', 'The stakes just went up.', 'Not a dry eye in the house.',
  "I'm going to need a sequel.", 'Small break. Kidding, keep going.', 'Love this found family energy.',
  "Someone's about to have a very bad day.", 'Okay now I am invested.', 'Mysterious stranger alert.',
  "Can't stop, won't stop.", 'Wait, that name sounds familiar.', 'Nobody trust the nice old man.',
  'That was clever. I hate that it was clever.', 'Shoutout to whoever wrote that sentence.', 'Wow, they really went there.',
];
const ENDING = [ // only from 90% on
  "Nearly there. Don't tell me how it ends.", 'What a ride.', 'Last few pages, hands are sweating.',
  'This ending had better be good.', 'Wrapping up, one sec.', 'Okay, I have feelings about this ending already.',
  'So close. Just a couple more chapters.', 'Almost done. I need a sequel immediately.',
];
function remark(j, pct) {
  if (pct < 5) return '';
  if (pct >= 90) return ENDING[(parseInt(j.id.slice(0, 6), 16) + Math.floor(pct / 5)) % ENDING.length];
  // each book gets its own order: start at a spot from its id, then step by a number coprime to the length
  return REMARKS[(parseInt(j.id.slice(0, 6), 16) + Math.floor(pct / 5) * 37) % REMARKS.length];
}

function drawJob(j) {
  const pct = j.total ? Math.round(100 * j.done / j.total) : 0;
  let body = '';
  if (j.state === 'queued' || j.state === 'running') {
    const r = j.state === 'running' && j.done ? remark(j, pct) : '';
    body = `<div class="bar"><i style="width:${pct}%"></i></div><p class="note">${esc(j.note)}</p>${r ? `<p class="remark">${esc(r)}</p>` : ''}`;
  } else if (j.state === 'failed') {
    body = `<p class="err">${esc(j.error)}</p>
      <div class="actions"><button class="link" data-retry="${j.id}">${j.done ? 'try again from where it stopped' : 'try again'}</button></div>`;
  } else {
    const s = j.summary || { changes: 0, sounds: [] };
    const sounds = s.sounds.slice(0, 6).map(([k, n]) => `${esc(pretty(k))} <span class="sc">×${n}</span>`).join(', ');
    const missing = (j.missing || []).slice(0, 5).map(([k, n]) => `<b>${esc(k)}</b> (${n})`).join(', ');
    const part = s.coverage && s.coverage < 100 ? ` Only the first ${s.coverage}% has been read so far.` : '';
    body = `<p class="sum">${s.sounds.length ? `${s.changes} scene changes, using ${s.sounds.length} sounds.` : 'No passage was vivid enough for a sound. The book stays silent.'}${part}</p>
      ${sounds ? `<p class="sounds">${sounds}</p>` : ''}
      ${missing ? `<p class="missing">Left silent for lack of a sound: ${missing}.</p>` : ''}
      ${j.saved ? `<p class="saved">${esc(j.saved)}</p>` : ''}
      ${j.ingested ? `<p class="saved">Sent to Calibre. It appears in the library once Calibre-Web has taken it in.</p>` : ''}
      <div class="actions">
        <a class="btn" href="/api/jobs/${j.id}/epub">Download EPUB</a>
        <a class="link" href="/api/jobs/${j.id}/json">just the sound file</a>
        ${j.source === 'upload' && !j.ingested ? `<button class="link" data-ingest="${j.id}">add to Calibre</button>` : ''}
      </div>`;
  }
  const when = j.state === 'done' || j.state === 'failed' ? ago(j.finished || j.created) : (j.state === 'queued' ? 'waiting' : `${pct}%`);
  return `<li class="job"><div class="head"><span class="t">${esc(j.title)}</span><span class="when">${when}</span></div>
    ${j.author ? `<div class="sc">${esc(j.author)}</div>` : ''}${body}</li>`;
}

let timer;
async function loadJobs() {
  clearTimeout(timer);
  let jobs = [];
  try { jobs = await api('/api/jobs'); } catch { /* keep the last view */ }
  $('#jobs').innerHTML = jobs.map(drawJob).join('');
  $('#nojobs').hidden = jobs.length > 0;
  const busy = jobs.some((j) => j.state === 'queued' || j.state === 'running');
  if (!busy && wasBusy) loadBooks(); // refresh the "already has sound" marks
  wasBusy = busy;
  timer = setTimeout(loadJobs, busy ? 2000 : 15000);
}
let wasBusy = false;
$('#jobs').addEventListener('click', async (e) => {
  const b = e.target.closest('[data-ingest], [data-retry]');
  if (!b) return;
  b.disabled = true;
  const url = b.dataset.ingest ? `/api/jobs/${b.dataset.ingest}/ingest` : `/api/jobs/${b.dataset.retry}/retry`;
  try { await post(url, {}); await loadJobs(); } catch (err) { alert(err.message); b.disabled = false; }
});

// ---------- settings ----------
const dlg = $('#settings-dialog');
$('#open-settings').addEventListener('click', () => { setStatus(''); dlg.showModal(); });
$('#close-settings').addEventListener('click', () => dlg.close());
dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); }); // click outside the sheet
let presets = {};
function setStatus(text, kind = '') { const s = $('#status'); s.textContent = text; s.className = 'status ' + kind; }
function fitForm(fromPreset) {
  const p = presets[$('#provider').value];
  if (!p) return;
  // Cloud services have a fixed address; only local and custom servers need one typed in.
  $('#row-url').hidden = !['ollama', 'lmstudio', 'custom'].includes($('#provider').value);
  $('#row-key').hidden = !p.key;
  if (fromPreset) { $('#base_url').value = p.base_url; $('#model').value = p.model; }
}
async function loadSettings() {
  const s = await api('/api/settings');
  presets = s.presets;
  $('#provider').innerHTML = Object.entries(presets).map(([k, p]) => `<option value="${k}">${esc(p.name)}</option>`).join('');
  $('#provider').value = s.provider;
  $('#base_url').value = s.base_url || presets[s.provider]?.base_url || '';
  $('#model').value = s.model || '';
  $('#limit_pct').value = s.limit_pct || 100;
  $('#api_key').placeholder = s.key_hint ? `saved (${s.key_hint}), paste to replace` : 'paste your API key';
  fitForm(false);
}
$('#provider').addEventListener('change', () => { fitForm(true); setStatus(''); });
$('#settings').addEventListener('submit', async (e) => {
  e.preventDefault();
  try {
    await post('/api/settings', { provider: $('#provider').value, base_url: $('#base_url').value, model: $('#model').value, api_key: $('#api_key').value,
      limit_pct: Number($('#limit_pct').value) || 100 });
    $('#api_key').value = '';
    await loadSettings();
    setStatus('Saved.', 'good');
  } catch (err) { setStatus(err.message, 'bad'); }
});
$('#test').addEventListener('click', async () => {
  setStatus('Asking it a question…');
  try {
    const r = await post('/api/settings/test', {});
    r.ok ? setStatus(`It answered "${r.reply}". Ready.`, 'good') : setStatus(r.error, 'bad');
  } catch (err) { setStatus(err.message, 'bad'); }
});

loadBooks(); loadJobs();
loadSettings().then(() => { if (location.hash === '#settings') dlg.showModal(); });

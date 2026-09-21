/* Ansichten und Zustand des Prüf-Cockpits.
   Die Daten sind die Zwischenstände der Pipeline aus out/<job>/. Geschrieben
   wird nur eine Datei: review.decisions.json (Freigaben und Korrekturvermerke). */

import { el, icon, Badge, Meter, StatRow, Card, Button, SearchField, Segmented,
         Table, Dist, Empty, Note, Quote, toast, DropZone, RunProgress,
         MetricRow, ProbBar, CodeChip } from './components.js';

const state = {
  job: '', demo: false, config: { review_threshold: 0.75, min_coverage: 0.30, etim_version: 'ETIM-10.0' },
  products: [], classified: [], enriched: [], validation: null, files: [],
  labels: {}, decisions: {},
  view: 'overview', selected: null, filter: 'review', query: '',
  // Modellvergleich und Laufsteuerung
  compare: null, jev: null, run: null, meta: {},
  jobs: [], jobsLoaded: false, serverUp: false, busy: '',
  cmpSelected: null, cmpFilter: 'all', cmpQuery: '',
  runOpts: { reuse_gemini: false, with_features: false },
};

/* ---------------------------------------------------------------- Ableitungen */

const byPid = list => Object.fromEntries(list.map(x => [x.product.supplier_pid, x]));

/** Grund für die Review-Queue. Spiegelt die Regel aus report.py. */
function reviewReason(e) {
  const { review_threshold: t, min_coverage: c } = state.config;
  if (!e.class_id) return { text: 'Keine Klasse', tone: 'crit' };
  if (e.class_confidence < t) return { text: 'Klasse unsicher', tone: 'crit' };
  if (c > 0 && e.features.length && e.coverage < c) {
    return { text: `Abdeckung ${Math.round(e.coverage * 100)} %`, tone: 'warn' };
  }
  if (e.features.some(f => f.value !== null && f.confidence < t)) return { text: 'Merkmale unsicher', tone: 'warn' };
  return { text: 'Prüfen', tone: 'warn' };
}

function statusOf(e) {
  const d = state.decisions[e.product.supplier_pid];
  if (d?.status === 'ok') return { key: 'ok', label: 'Freigegeben', tone: 'ok' };
  if (d?.status === 'fix') return { key: 'fix', label: 'Korrektur nötig', tone: 'crit' };
  if (e.needs_review) { const r = reviewReason(e); return { key: 'review', label: r.text, tone: r.tone }; }
  return { key: 'auto', label: 'Freigabefähig', tone: 'ok' };
}

const filled = e => e.features.filter(f => f.value !== null).length;

function visibleArticles() {
  const q = state.query.trim().toLowerCase();
  return state.enriched.filter(e => {
    const st = statusOf(e).key;
    if (state.filter === 'review' && !(st === 'review' || st === 'fix')) return false;
    if (state.filter === 'done' && !(st === 'ok')) return false;
    if (!q) return true;
    return (e.product.supplier_pid + ' ' + e.product.name + ' ' + (e.class_id || '') + ' ' + e.class_desc)
      .toLowerCase().includes(q);
  });
}

/* ------------------------------------------------------------------ Merkmale */

/** Ein Merkmal als lesbarer Wert — EV-Codes werden über die ETIM-Wertelisten aufgelöst. */
function featureValue(f, meta) {
  if (f.value === null) return el('div', { class: 'ledger__value ledger__value--none' }, 'nicht ermittelt');
  const unit = meta?.unit_desc ? el('span', { class: 'ledger__unit' }, ' ' + meta.unit_desc) : null;
  if (meta?.type === 'L') {
    return el('div', { class: 'ledger__value' }, f.value === 'true' ? 'ja' : 'nein');
  }
  if (meta?.type === 'A') {
    const label = state.labels[f.value];
    return el('div', { class: 'ledger__value' },
      label || f.value,
      label ? el('span', { class: 'ledger__evcode' }, f.value) : null);
  }
  if (f.value_max) {
    return el('div', { class: 'ledger__value' }, `${f.value} – ${f.value_max}`, unit);
  }
  return el('div', { class: 'ledger__value' }, f.value, unit);
}

function LedgerRow(f, meta) {
  const t = state.config.review_threshold;
  const empty = f.value === null;
  return el('div', { class: `ledger__row${empty ? ' ledger__row--empty' : ''}` },
    el('div', { class: 'ledger__id' }, f.feature_id),
    el('div', {},
      el('div', { class: 'ledger__desc' }, meta?.desc || '—'),
      meta ? el('div', { class: 'ledger__type' },
        ({ A: 'Werteliste', N: 'numerisch', L: 'logisch', R: 'Bereich' }[meta.type] || meta.type) +
        (meta.n_values ? ` · ${meta.n_values} Werte` : '') +
        (meta.unit_desc ? ` · ${meta.unit_desc}` : '')) : null,
    ),
    el('div', {},
      featureValue(f, meta),
      Quote(f.source, f.reason),
    ),
    empty ? el('div', {}) : Meter(f.confidence, { tone: f.confidence >= t ? 'ok' : 'warn' }),
  );
}

/* -------------------------------------------------------------------- Ansichten */

function ViewOverview() {
  const n = state.enriched.length;
  const withClass = state.enriched.filter(e => e.class_id);
  const review = state.enriched.filter(e => statusOf(e).key === 'review' || statusOf(e).key === 'fix');
  const totalF = withClass.reduce((s, e) => s + e.features.length, 0);
  const filledF = withClass.reduce((s, e) => s + filled(e), 0);
  const thin = withClass.filter(e => state.config.min_coverage > 0 && e.features.length
                                     && e.coverage < state.config.min_coverage);

  const classRows = Object.values(withClass.reduce((acc, e) => {
    const k = e.class_id;
    (acc[k] ||= { name: e.class_desc, code: e.class_id, n: 0 }).n++;
    return acc;
  }, {})).sort((a, b) => b.n - a.n);

  const missing = {};
  for (const e of withClass) for (const f of e.features) {
    if (f.value === null) {
      const k = f.feature_id;
      (missing[k] ||= { name: e.feature_meta[k]?.desc || k, code: k, n: 0 }).n++;
    }
  }
  const missRows = Object.values(missing).sort((a, b) => b.n - a.n).slice(0, 7);

  return el('div', { class: 'view scroll-y' },
    state.demo ? Note('Beispieldaten. Es ist kein Job geladen — so sieht das Cockpit mit echten Katalogdaten aus.', 'accent') : null,
    StatRow([
      { label: 'Artikel', value: n, sub: `${withClass.length} mit ETIM-Klasse` },
      { label: 'Freigabefähig', value: n - review.length, tone: 'ok',
        sub: n ? `${Math.round((n - review.length) / n * 100)} % des Katalogs` : '—' },
      { label: 'Zu prüfen', value: review.length, tone: review.length ? 'warn' : undefined,
        sub: thin.length ? `davon ${thin.length} unter Mindestabdeckung` : 'keine Abdeckungsfälle' },
      { label: 'Merkmale befüllt', value: totalF ? Math.round(filledF / totalF * 100) : 0, unit: '%',
        sub: `${filledF} von ${totalF}` },
    ]),
    el('div', { class: 'grid-2' },
      Card('Klassenverteilung', classRows.length
        ? Dist(classRows) : Empty('Noch keine Klassen', 'classify ausführen.')),
      Card('Häufig fehlende Merkmale', missRows.length
        ? el('div', {},
            el('p', { class: 'muted', style: 'font-size:var(--fs-sm);line-height:var(--lh-body);margin-bottom:var(--s-6)' },
              'Diese Merkmale konnte kein Modell aus dem Katalog belegen — der Hersteller muss sie nachliefern.'),
            Dist(missRows, { tone: 'warn' }))
        : Empty('Nichts fehlt', 'Alle Merkmale der erkannten Klassen sind befüllt.')),
    ),
    Card('Artikel zur Prüfung', review.length
      ? Table([
          { label: 'Artikel-Nr.', render: r => el('span', { class: 'code' }, r.product.supplier_pid) },
          { label: 'Bezeichnung', render: r => r.product.name },
          { label: 'Klasse', render: r => r.class_id
              ? el('span', { class: 'code' }, r.class_id) : el('span', { class: 'faint' }, '—') },
          { label: 'Abdeckung', num: true, render: r => Meter(r.coverage) },
          { label: 'Grund', render: r => { const s = statusOf(r); return Badge(s.label, s.tone, { dot: true }); } },
        ], review.slice(0, 12), { onRowClick: r => { state.selected = r.product.supplier_pid; go('review'); } })
      : Empty('Nichts offen', 'Kein Artikel unter den Schwellen.', 'check'),
      { flush: review.length > 0 }),
  );
}

function ViewArticles() {
  const rows = visibleArticles();
  return el('div', { class: 'view scroll-y' },
    el('div', { class: 'view__head' },
      el('h2', { class: 'view__title' }, 'Artikel'),
      el('span', { class: 'faint', style: 'font-size:var(--fs-sm)' }, `${rows.length} von ${state.enriched.length}`),
      el('div', { class: 'view__spacer' }),
      el('div', { style: 'width:240px' }, SearchField('Artikel, Nummer oder Klasse', e => { state.query = e.target.value; render(); })),
      Segmented([
        { value: 'all', label: 'Alle', count: state.enriched.length },
        { value: 'review', label: 'Prüfen', count: state.enriched.filter(e => ['review', 'fix'].includes(statusOf(e).key)).length },
        { value: 'done', label: 'Freigegeben', count: state.enriched.filter(e => statusOf(e).key === 'ok').length },
      ], state.filter, v => { state.filter = v; render(); }),
    ),
    Card(null, rows.length ? Table([
      { label: 'Artikel-Nr.', render: r => el('span', { class: 'code' }, r.product.supplier_pid) },
      { label: 'Bezeichnung', render: r => r.product.name },
      { label: 'ETIM-Klasse', render: r => r.class_id
          ? el('div', {}, el('div', {}, r.class_desc), el('div', { class: 'code faint', style: 'font-size:var(--fs-2xs)' }, r.class_id))
          : el('span', { class: 'faint' }, 'keine') },
      { label: 'Merkmale', num: true, render: r => `${filled(r)}/${r.features.length}` },
      { label: 'Abdeckung', num: true, render: r => Meter(r.coverage) },
      { label: 'Seite', num: true, render: r => String(r.product.page || '—') },
      { label: 'Status', render: r => { const s = statusOf(r); return Badge(s.label, s.tone, { dot: true }); } },
    ], rows, { onRowClick: r => { state.selected = r.product.supplier_pid; go('review'); } })
      : Empty('Kein Treffer', 'Suche oder Filter anpassen.', 'search'), { flush: true }),
  );
}

function ViewReview() {
  const rows = visibleArticles();
  if (state.selected && !rows.some(r => r.product.supplier_pid === state.selected)) {
    if (!state.enriched.some(r => r.product.supplier_pid === state.selected)) state.selected = null;
  }
  const current = state.enriched.find(r => r.product.supplier_pid === state.selected) || rows[0] || null;

  const list = el('div', { class: 'split__list' },
    el('div', { class: 'split__filter' },
      SearchField('Suchen', e => { state.query = e.target.value; render(); }),
      Segmented([
        { value: 'review', label: 'Offen', count: state.enriched.filter(e => ['review', 'fix'].includes(statusOf(e).key)).length },
        { value: 'all', label: 'Alle', count: state.enriched.length },
        { value: 'done', label: 'Erledigt', count: state.enriched.filter(e => statusOf(e).key === 'ok').length },
      ], state.filter, v => { state.filter = v; render(); }),
    ),
    el('div', { class: 'split__scroll scroll-y' }, rows.length ? rows.map(r => {
      const s = statusOf(r);
      return el('button', {
        class: `plist__item plist__item--${s.tone}`, type: 'button',
        'aria-current': String(current && r.product.supplier_pid === current.product.supplier_pid),
        onClick: () => { state.selected = r.product.supplier_pid; render(); },
      },
        el('div', { class: 'plist__top' },
          el('span', { class: 'plist__pid' }, r.product.supplier_pid),
          Badge(s.label, s.tone)),
        el('div', { class: 'plist__name' }, r.product.name),
        el('div', { class: 'plist__meta' },
          el('span', { class: 'code' }, r.class_id || 'keine Klasse'),
          el('span', {}, '·'),
          el('span', {}, `${filled(r)}/${r.features.length} Merkmale`)),
      );
    }) : Empty('Nichts offen', 'Alle Artikel dieses Filters sind erledigt.', 'check')),
  );

  return el('div', { class: 'view view--flush' },
    el('div', { class: 'split' }, list,
      el('div', { class: 'split__detail' }, current ? Detail(current) :
        Empty('Kein Artikel gewählt', 'Links einen Artikel wählen, um Klasse und Merkmale zu prüfen.')),
    ),
  );
}

function Detail(e) {
  const pid = e.product.supplier_pid;
  const cls = byPid(state.classified)[pid];
  const dec = state.decisions[pid];
  const runnerUp = cls?.candidates?.find(c => c.class_id === cls.decision.runner_up)
                   || cls?.candidates?.find(c => c.class_id !== e.class_id);
  const feats = [...e.features].sort((a, b) => (a.value === null) - (b.value === null));

  return el('div', { class: 'detail' },
    el('div', { class: 'detail__head' },
      el('div', { style: 'min-width:0' },
        el('div', { class: 'detail__pid' }, pid, e.product.gtin ? ` · GTIN ${e.product.gtin}` : ' · ohne GTIN'),
        el('h2', { class: 'detail__name' }, e.product.name),
        e.product.description ? el('p', { class: 'detail__sub' }, e.product.description) : null,
      ),
      el('div', { class: 'detail__actions' },
        Button('Freigeben', { variant: 'ok', size: 'sm', iconName: 'check',
          pressed: dec?.status === 'ok', onClick: () => setDecision(pid, 'ok') }),
        Button('Korrektur', { variant: 'warn', size: 'sm',
          pressed: dec?.status === 'fix', onClick: () => setDecision(pid, 'fix') }),
      ),
    ),

    Card('Klassenentscheidung',
      el('div', {},
        el('div', { class: 'decision' },
          el('div', { class: 'decision__cell' },
            el('span', { class: 'label' }, 'Gewählt'),
            el('div', { class: 'decision__class' }, e.class_desc || 'keine Klasse'),
            el('div', { class: 'decision__code' }, e.class_id || '—'),
            el('div', { style: 'margin-top:var(--s-4)' }, Meter(e.class_confidence)),
          ),
          el('div', { class: 'decision__rule' }),
          el('div', { class: 'decision__cell' },
            el('span', { class: 'label' }, 'Zweitbeste'),
            el('div', { class: 'decision__class muted' }, runnerUp?.description || '—'),
            el('div', { class: 'decision__code' }, runnerUp?.class_id || '—'),
            runnerUp ? el('div', { style: 'margin-top:var(--s-4)' },
              Meter(runnerUp.score, { tone: 'idle' })) : null,
          ),
        ),
        cls?.decision?.reasoning ? el('p', { class: 'decision__why' }, cls.decision.reasoning) : null,
      )),

    e.product.attributes?.length ? Card('Katalogattribute',
      el('div', { class: 'attrs' }, e.product.attributes.map(a =>
        el('span', { class: 'attr' },
          el('span', { class: 'attr__k' }, a.name),
          el('span', { class: 'attr__v' }, a.value))))) : null,

    Card(`Merkmale · ${filled(e)} von ${e.features.length} belegt`,
      feats.length ? el('div', {}, feats.map(f => LedgerRow(f, e.feature_meta[f.feature_id])))
        : Empty('Keine Merkmale', 'Für diese Klasse sind keine ETIM-Merkmale hinterlegt.'),
      { flush: true, action: Meter(e.coverage, { showPct: true }) }),
  );
}

function ViewExport() {
  const v = state.validation;
  const errs = v?.issues?.filter(i => i.level === 'error') || [];
  const warns = v?.issues?.filter(i => i.level === 'warn') || [];
  const exported = state.enriched.filter(e => !e.needs_review).length;

  return el('div', { class: 'view scroll-y' },
    StatRow([
      { label: 'Im Export', value: exported, sub: `${state.enriched.length - exported} zurückgehalten` },
      { label: 'XSD-Prüfung', value: v?.xsd_ok === true ? 'bestanden' : v?.xsd ? 'gescheitert' : 'keine XSD',
        tone: v?.xsd_ok === true ? 'ok' : v?.xsd ? 'crit' : undefined,
        sub: v?.xsd ? String(v.xsd).split('/').pop() : 'BMEcat-Guideline fehlt in data/schema/' },
      { label: 'Fehler', value: errs.length, tone: errs.length ? 'crit' : 'ok', sub: 'strukturell' },
      { label: 'Warnungen', value: warns.length, tone: warns.length ? 'warn' : undefined, sub: 'DQR-relevant' },
    ]),
    !v ? Note('Noch nicht geprüft — validate ausführen.') : null,
    v && !v.xsd ? Note('Ohne XSD läuft nur die Strukturprüfung. Die echte Schemaprüfung braucht die ETIM-BMEcat-Guideline in data/schema/.') : null,
    v?.issues?.length ? Card('Befunde', Table([
      { label: 'Stufe', render: i => Badge(i.level === 'error' ? 'Fehler' : 'Warnung', i.level === 'error' ? 'crit' : 'warn', { dot: true }) },
      { label: 'Artikel', render: i => el('span', { class: 'code' }, i.pid || '—') },
      { label: 'Befund', key: 'msg' },
    ], v.issues), { flush: true }) : null,
    state.files.length ? Card('Dateien', el('div', { class: 'files' }, state.files.map(f =>
      el('div', { class: 'file' },
        icon('file', 'rail__icon'),
        el('div', {}, el('div', { class: 'file__name' }, f.name), el('div', { class: 'file__desc' }, f.desc)),
        el('div', { class: 'file__spacer' }),
        el('span', { class: 'faint num', style: 'font-size:var(--fs-xs)' }, f.size),
      ))), { flush: true }) : null,
  );
}

/* ------------------------------------------------------- Katalog und Läufe */

/** Kein Jev-Zugang, kein Jev-Ergebnis — und das steht dann auch so da. */
function JevBanner() {
  const j = state.jev;
  if (!j) return null;
  if (j.simulated) {
    return Note('Trockenlauf (ETIM_DRY_RUN=1): Jev-Antworten sind simuliert. ' +
                'Die Zahlen zeigen, dass die Kette läuft — sie sind keine Messung von Jev.', 'warn');
  }
  if (!j.ready) return Note(`Jev ist nicht eingerichtet: ${j.reason}`, 'warn');
  return null;
}

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch { /* leer */ }
  if (!res.ok) throw new Error(data?.error || `${res.status} ${res.statusText}`);
  return data;
}

async function loadJobs() {
  try {
    const d = await api('api/jobs');
    state.jobs = d.jobs || [];
    state.jev = d.jev || state.jev;
    state.serverUp = true;
  } catch {
    // Datei direkt im Browser geöffnet (geteilte Vorschau): kein Server, keine Jobs.
    state.serverUp = false;
  }
  state.jobsLoaded = true;
}

async function loadJob(job) {
  const d = await api(`api/job?job=${encodeURIComponent(job)}`);
  Object.assign(state, d);
  state.decisions = d.decisions || {};
  state.demo = false;
  state.selected = null;
  state.cmpSelected = null;
  history.replaceState(null, '', `?job=${encodeURIComponent(job)}`);
}

async function switchJob(job) {
  try { await loadJob(job); await loadJobs(); render(); }
  catch (e) { toast(`Job ${job}: ${e.message}`); }
}

async function uploadCatalog(file) {
  if (state.busy) return;
  const limit = (state.config.max_upload_mb || 40) * 1e6;
  if (file.size > limit) {
    // Schon hier abfangen: eine zu große Datei muss nicht erst über die Leitung.
    state.run = {
      state: 'error', message: 'Datei zu groß', stages: [], log: [],
      error: `${file.name} ist ${(file.size / 1e6).toFixed(1)} MB groß, ` +
             `erlaubt sind ${state.config.max_upload_mb || 40} MB.`,
    };
    render();
    return;
  }
  state.busy = `${file.name} wird übertragen …`;
  render();
  try {
    const created = await api('api/upload', {
      method: 'POST',
      headers: { 'X-Filename': encodeURIComponent(file.name), 'Content-Type': 'application/octet-stream' },
      body: file,
    });
    state.busy = '';
    toast(`Job '${created.job}' angelegt`);
    await loadJobs();
    await loadJob(created.job);
    render();
    await startRun('full', created.job);
  } catch (e) {
    state.busy = '';
    state.run = { state: 'error', error: e.message, message: 'Upload abgelehnt', stages: [], log: [] };
    render();
  }
}

async function startRun(kind, job = state.job) {
  if (!job) { toast('Kein Job gewählt'); return; }
  try {
    state.run = await api('api/run', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job, kind, ...state.runOpts }),
    });
    render();
    pollRun(job);
  } catch (e) {
    state.run = { state: 'error', error: e.message, message: 'Lauf nicht gestartet', stages: [], log: [] };
    render();
  }
}

let pollTimer = null;
function pollRun(job) {
  clearTimeout(pollTimer);
  const step = async () => {
    try {
      const r = await api(`api/run?job=${encodeURIComponent(job)}`);
      state.run = r;
      if (r.state === 'running') { render(); pollTimer = setTimeout(step, 1200); return; }
      await loadJob(job);
      await loadJobs();
      state.run = r;
      if (r.state === 'done' && state.compare) state.view = 'compare';
      render();
    } catch (e) {
      state.run = { state: 'error', error: e.message, message: 'Verbindung zum Lauf verloren', stages: [], log: [] };
      render();
    }
  };
  pollTimer = setTimeout(step, 800);
}

function ViewCatalog() {
  const running = state.run?.state === 'running';
  const j = state.jev;
  const accept = '.pdf,.xlsx,.csv,application/pdf,text/csv';

  const jobRows = state.jobs.map(x => ({ ...x }));
  const opt = (key, label, hint) => el('label', { class: 'opt' },
    el('input', {
      type: 'checkbox', checked: state.runOpts[key] || null, disabled: running || null,
      onChange: e => { state.runOpts[key] = e.target.checked; },
    }),
    el('div', {}, el('div', {}, label), el('div', { class: 'opt__hint' }, hint)));

  return el('div', { class: 'view scroll-y' },
    state.demo ? Note('Beispieldaten — ohne laufenden Server lässt sich hier nichts hochladen.', 'accent') : null,
    JevBanner(),
    el('div', { class: 'grid-2' },
      Card('Neuen Katalog einspielen', el('div', {},
        DropZone({
          accept,
          disabled: state.demo || running,
          hint: `PDF, XLSX oder CSV · höchstens ${state.config.max_upload_mb || 40} MB. ` +
                'Daraus entsteht ein neuer Job; danach laufen Extraktion und Vergleich von allein.',
          onFile: uploadCatalog,
        }),
        state.busy ? el('p', { class: 'muted', style: 'margin-top:var(--s-5)' }, state.busy) : null,
      )),
      Card('Vergleich für diesen Job', el('div', {},
        el('p', { class: 'muted', style: 'font-size:var(--fs-sm);line-height:var(--lh-body)' },
          `Gemini entscheidet aus den Top-${state.config.top_k || 20} Kandidaten, ` +
          `Jev aus bis zu ${state.config.jev_top_k || 254}. Beide bekommen denselben Artikel ` +
          'und dieselbe Retrieval-Liste.'),
        el('div', { class: 'opts' },
          opt('reuse_gemini', 'Gemini aus dem letzten Lauf übernehmen',
              'Spart die Gemini-Kosten — dann sind Laufzeit und Kosten nur für Jev gemessen.'),
          opt('with_features', 'ETIM-Merkmale mitvergleichen',
              'Logische Merkmale als Noul, Wertelisten als Choice. Zahlen bleiben bei Gemini.')),
        el('div', { class: 'row-actions' },
          Button('Vergleich starten', {
            variant: 'primary', iconName: 'scale', disabled: running || !state.job || state.demo,
            onClick: () => startRun('compare'),
          }),
          Button('Katalog neu einlesen und vergleichen', {
            iconName: 'spark', disabled: running || !state.meta?.source || state.demo,
            title: state.meta?.source ? '' : 'Nur für Jobs, deren Katalog hier hochgeladen wurde',
            onClick: () => startRun('full'),
          })),
        RunProgress(state.run),
      )),
    ),
    Card(`Jobs · ${state.jobs.length}`, jobRows.length ? Table([
      { label: 'Job', render: r => el('span', { class: 'code' }, r.job) },
      { label: 'Quelle', render: r => r.source_name || el('span', { class: 'faint' }, 'aus dem Terminal') },
      { label: 'Artikel', num: true, render: r => String(r.products || '—') },
      { label: 'Stand', render: r => el('div', { class: 'tags' },
          r.has_compare ? Badge('Vergleich', 'accent') : null,
          r.has_enriched ? Badge('Merkmale', 'ok') : null,
          r.has_reference ? Badge('Referenz', 'ok') : null,
          r.running ? Badge('läuft', 'warn', { dot: true }) : null) },
      { label: '', render: r => r.job === state.job
          ? Badge('geöffnet', 'accent')
          : Button('Öffnen', { size: 'sm', onClick: () => switchJob(r.job) }) },
    ], jobRows) : Empty('Noch kein Job', 'Oben einen Katalog einspielen.', 'upload'), { flush: jobRows.length > 0 }),
  );
}

/* ----------------------------------------------------------------- Vergleich */

const pct = v => (v === null || v === undefined ? null : `${(v * 100).toFixed(0)} %`);
const usd = v => (v === null || v === undefined ? null : `$${v < 0.01 ? v.toFixed(4) : v.toFixed(2)}`);
const ms = v => (!v ? null : v >= 1000 ? `${(v / 1000).toFixed(1)} s` : `${v} ms`);

/** Wer ist besser? Nur wo ein Vergleich überhaupt zulässig ist. */
function better(a, b, { lower = false, allow = true } = {}) {
  if (!allow || a === null || a === undefined || b === null || b === undefined || a === b) return null;
  return (lower ? a < b : a > b) ? 'a' : 'b';
}

function ModelHead(m, cmp) {
  return el('div', { class: 'cmp__head' },
    el('div', { class: 'cmp__model' }, m.label,
      m.model_id ? el('span', { class: 'cmp__id code' }, m.model_id) : null),
    el('div', { class: 'cmp__tags' },
      m.simulated ? Badge('simuliert', 'warn') : null,
      m.errors ? Badge(`${m.errors} Fehler`, 'crit') : null,
      m.model === 'gemini' && cmp.reused_gemini ? Badge('aus letztem Lauf', 'idle') : null,
      el('span', { class: 'cmp__k' }, `${m.candidates_seen || 0} Kandidaten`)),
  );
}

function MetricTable(cmp) {
  const g = cmp.metrics.gemini, j = cmp.metrics.jev;
  const ref = cmp.has_reference;
  const measured = !g.simulated && !j.simulated;   // simulierte Zahlen kuert man nicht
  const refHint = ref ? '' : 'keine Referenz hinterlegt';

  const rows = [
    { label: 'Trefferquote gegen die Referenz',
      hint: ref ? `${cmp.n_reference} Artikel mit Referenzklasse` : refHint,
      a: ref ? `${pct(g.hit_rate)} (${g.hits}/${cmp.n_reference})` : null,
      b: ref ? `${pct(j.hit_rate)} (${j.hits}/${cmp.n_reference})` : null,
      best: better(g.hit_rate, j.hit_rate, { allow: ref && measured }) },
    { label: 'Referenzklasse im Kandidatenfeld',
      hint: 'trennt „Retrieval hat sie nicht gefunden“ von „Modell hat sie übersehen“',
      a: ref ? `${g.reference_in_window}/${cmp.n_reference}` : null,
      b: ref ? `${j.reference_in_window}/${cmp.n_reference}` : null,
      best: better(g.reference_in_window, j.reference_in_window, { allow: ref }) },
    { label: 'Artikel ohne Klasse', hint: `von ${g.n}`,
      a: String(g.no_class), b: String(j.no_class),
      best: better(g.no_class, j.no_class, { lower: true, allow: measured }) },
    { label: 'Variantenkonsistenz',
      hint: g.variant_groups ? `${g.variant_groups} Gruppen mit gleicher Basisbezeichnung`
                             : 'keine Varianten im Katalog',
      a: g.variant_consistency === null ? null : `${pct(g.variant_consistency)} (${g.variant_consistent}/${g.variant_groups})`,
      b: j.variant_consistency === null ? null : `${pct(j.variant_consistency)} (${j.variant_consistent}/${j.variant_groups})`,
      best: better(g.variant_consistency, j.variant_consistency, { allow: measured }) },
    { label: 'Unbelegte EC-Codes in der Begründung',
      hint: 'Codes, die es in der ETIM-Klassentabelle nicht gibt',
      a: `${g.unverified_codes} von ${g.reasoning_codes} genannten`,
      b: el('span', { class: 'faint' }, 'erzeugt keinen Text — strukturell unmöglich'),
      best: null },
    { label: 'Konfidenz bei richtiger Antwort', hint: refHint,
      a: pct(g.conf_correct), b: pct(j.conf_correct), best: null },
    { label: 'Konfidenz bei falscher Antwort',
      hint: 'je niedriger, desto brauchbarer als Review-Signal',
      a: pct(g.conf_wrong), b: pct(j.conf_wrong),
      best: better(g.conf_wrong, j.conf_wrong, { lower: true, allow: ref && measured }) },
    { label: 'Latenz je Artikel',
      a: ms(g.latency_ms_avg), b: ms(j.latency_ms_avg),
      best: better(g.latency_ms_avg, j.latency_ms_avg, { lower: true, allow: measured }) },
    { label: 'Kosten für diesen Lauf',
      hint: cmp.reused_gemini ? 'Gemini wiederverwendet — nicht vergleichbar' : 'Klassenzuordnung',
      a: usd(g.cost_usd), b: usd(j.cost_usd),
      best: better(g.cost_usd, j.cost_usd, { lower: true, allow: measured && !cmp.reused_gemini }) },
  ];

  if (cmp.with_features) {
    rows.push(
      { label: 'Merkmale befüllt', hint: 'Typ L als Noul, Typ A als Choice; Zahlen bleiben bei Gemini',
        a: g.feat_total ? `${g.feat_filled}/${g.feat_total}` : null,
        b: j.feat_total ? `${j.feat_filled}/${j.feat_total}` : null, best: null },
      { label: 'davon mit Katalogbeleg exportfähig',
        hint: 'Jev liefert kein Zitat — der Beleg muss von Gemini kommen, sonst Review',
        a: g.feat_total ? String(g.feat_exportable) : null,
        b: j.feat_total ? String(j.feat_exportable) : null,
        best: better(g.feat_exportable, j.feat_exportable, { allow: measured && !!j.feat_total }) },
    );
  }

  return el('div', { class: 'cmp' },
    el('div', { class: 'cmp__row cmp__row--head' },
      el('div', { class: 'cmp__label' }, 'Kennzahl'),
      ModelHead(g, cmp), ModelHead(j, cmp)),
    rows.map(MetricRow),
  );
}

function cmpRows(cmp) {
  const q = state.cmpQuery.trim().toLowerCase();
  return cmp.items.filter(it => {
    const g = it.answers.gemini || {}, j = it.answers.jev || {};
    if (state.cmpFilter === 'diff' && g.class_id === j.class_id) return false;
    if (state.cmpFilter === 'none' && g.class_id && j.class_id) return false;
    if (!q) return true;
    return (it.product.supplier_pid + ' ' + it.product.name).toLowerCase().includes(q);
  });
}

function AnswerCard(a, it, cmp) {
  if (!a) return el('div', { class: 'ans' }, el('span', { class: 'faint' }, 'keine Antwort'));
  const isRef = it.reference_class && a.class_id === it.reference_class;
  const tone = a.error ? 'crit' : !a.class_id ? 'warn' : it.reference_class ? (isRef ? 'ok' : 'crit') : 'idle';
  return el('div', { class: `ans ans--${tone}` },
    el('div', { class: 'ans__head' },
      el('span', { class: 'ans__model' }, a.model === 'jev' ? 'Jev' : 'Gemini'),
      a.simulated ? Badge('simuliert', 'warn') : null,
      it.reference_class ? Badge(isRef ? 'richtig' : 'falsch', isRef ? 'ok' : 'crit') : null),
    a.error
      ? el('p', { class: 'ans__err' }, a.error)
      : el('div', {},
          el('div', { class: 'ans__class' }, a.class_id
            ? (cmp.classNames?.[a.class_id] || a.class_id) : 'keine Klasse gewählt'),
          el('div', { class: 'ans__meta' },
            a.class_id ? el('span', { class: 'code' }, a.class_id) : null,
            a.rank_of_choice ? el('span', {}, `Rang ${a.rank_of_choice}`) : null,
            el('span', {}, `${a.candidates_seen} Kandidaten`),
            a.latency_ms ? el('span', {}, ms(a.latency_ms)) : null),
          el('div', { style: 'margin-top:var(--s-4)' },
            Meter(a.confidence, { tone: 'idle' })),
          a.is_accessory !== null && a.is_accessory !== undefined
            ? el('p', { class: 'ans__noul' },
                `Zubehör/Ersatzteil: ${(a.is_accessory * 100).toFixed(0)} % — ` +
                'unabhängig von der Klassenwahl gefragt')
            : null,
          a.trim_level ? el('p', { class: 'ans__trim' }, `Optionsbeschreibungen: ${a.trim_level}`) : null,
          a.reasoning ? el('p', { class: 'ans__why' }, a.reasoning) : null,
          a.reasoning_codes?.length
            ? el('div', { class: 'chips' }, a.reasoning_codes.map(CodeChip)) : null,
          ProbBar(a.probabilities, { chosen: a.class_id, labels: cmp.classNames }),
        ),
  );
}

function CompareDetail(it, cmp) {
  return el('div', { class: 'cmpdetail' },
    el('div', { class: 'cmpdetail__head' },
      el('div', { class: 'detail__pid' }, it.product.supplier_pid,
        it.product.page ? ` · Seite ${it.product.page}` : ''),
      el('h2', { class: 'detail__name' }, it.product.name),
      el('div', { class: 'cmpdetail__meta' },
        el('span', {}, `Basisbezeichnung: ${it.base_name}`),
        it.reference_class
          ? el('span', {}, 'Referenz ', el('span', { class: 'code' }, it.reference_class),
              it.reference_rank ? ` · Rang ${it.reference_rank} im Retrieval` : ' · nicht im Retrieval')
          : el('span', { class: 'faint' }, 'keine Referenzklasse hinterlegt')),
    ),
    el('div', { class: 'cmpdetail__cols' },
      AnswerCard(it.answers.gemini, it, cmp),
      AnswerCard(it.answers.jev, it, cmp)),
  );
}

function ViewCompare() {
  const cmp = state.compare;
  if (!cmp) {
    return el('div', { class: 'view scroll-y' },
      JevBanner(),
      Empty('Noch kein Vergleich', 'Unter „Katalog“ einen Lauf starten — oder im Terminal ' +
            '`python -m etim compare --job <job>`.', 'scale'));
  }
  // Klassennamen aus der Retrieval-Liste, damit die Ansicht keine Codes allein zeigt.
  cmp.classNames ||= Object.fromEntries(
    cmp.items.flatMap(it => (it.retrieval || []).map(c => [c.class_id, c.description])));

  const rows = cmpRows(cmp);
  const cur = cmp.items.find(it => it.product.supplier_pid === state.cmpSelected) || rows[0] || null;
  const g = cmp.metrics.gemini, j = cmp.metrics.jev;

  return el('div', { class: 'view scroll-y' },
    JevBanner(),
    // Was das Banner oben schon sagt, muss nicht noch einmal als Notiz kommen.
    el('div', {}, (cmp.notes || [])
      .filter(n => !/dry_run|simuliert/i.test(n))
      .map(n => Note(n, 'accent'))),
    StatRow([
      { label: 'Artikel verglichen', value: cmp.items.length,
        sub: cmp.has_reference ? `${cmp.n_reference} mit Referenz` : 'ohne Referenz' },
      { label: 'Gleiche Klasse', value: cmp.agreement.same_class ?? 0,
        sub: cmp.agreement.rate === null ? '—' : `${pct(cmp.agreement.rate)} Übereinstimmung` },
      { label: 'Ohne Klasse · Gemini', value: g.no_class, tone: g.no_class ? 'warn' : 'ok',
        sub: `sieht ${cmp.top_k_gemini} Kandidaten` },
      { label: 'Ohne Klasse · Jev', value: j.no_class, tone: j.no_class ? 'warn' : 'ok',
        sub: `sieht bis zu ${cmp.top_k_jev} Kandidaten` },
    ]),
    Card('Modell gegen Modell', MetricTable(cmp), { flush: true }),
    el('div', { class: 'view__head' },
      el('h2', { class: 'view__title' }, 'Artikel im Vergleich'),
      el('span', { class: 'faint', style: 'font-size:var(--fs-sm)' }, `${rows.length} von ${cmp.items.length}`),
      el('div', { class: 'view__spacer' }),
      el('div', { style: 'width:220px' }, SearchField('Artikel oder Nummer',
        e => { state.cmpQuery = e.target.value; render(); })),
      Segmented([
        { value: 'all', label: 'Alle', count: cmp.items.length },
        { value: 'diff', label: 'Uneinig',
          count: cmp.items.filter(it => it.answers.gemini?.class_id !== it.answers.jev?.class_id).length },
        { value: 'none', label: 'Ohne Klasse',
          count: cmp.items.filter(it => !it.answers.gemini?.class_id || !it.answers.jev?.class_id).length },
      ], state.cmpFilter, v => { state.cmpFilter = v; render(); }),
    ),
    Card(null, rows.length ? Table([
      { label: 'Artikel-Nr.', render: r => el('span', { class: 'code' }, r.product.supplier_pid) },
      { label: 'Bezeichnung', render: r => r.product.name },
      { label: 'Referenz', render: r => r.reference_class
          ? el('span', { class: 'code' }, r.reference_class) : el('span', { class: 'faint' }, '—') },
      { label: 'Gemini', render: r => ClassCell(r.answers.gemini, r) },
      { label: 'Jev', render: r => ClassCell(r.answers.jev, r) },
    ], rows, { onRowClick: r => { state.cmpSelected = r.product.supplier_pid; render(); } })
      : Empty('Kein Treffer', 'Suche oder Filter anpassen.', 'search'), { flush: true }),
    cur ? Card('Beide Antworten nebeneinander', CompareDetail(cur, cmp)) : null,
  );
}

function ClassCell(a, it) {
  if (!a) return el('span', { class: 'faint' }, '—');
  if (a.error) return Badge('Fehler', 'crit');
  if (!a.class_id) return Badge('keine Klasse', 'warn');
  const tone = it.reference_class ? (a.class_id === it.reference_class ? 'ok' : 'crit') : 'idle';
  return el('div', { class: `cell cell--${tone}` },
    el('span', { class: 'code' }, a.class_id),
    el('span', { class: 'cell__conf num' }, `${Math.round(a.confidence * 100)} %`));
}

/* ---------------------------------------------------------------------- Gerüst */

const VIEWS = [
  { key: 'overview', label: 'Übersicht', icon: 'gauge', render: ViewOverview },
  { key: 'catalog', label: 'Katalog', icon: 'upload', render: ViewCatalog },
  { key: 'compare', label: 'Vergleich', icon: 'scale', render: ViewCompare },
  { key: 'articles', label: 'Artikel', icon: 'list', render: ViewArticles },
  { key: 'review', label: 'Prüfen', icon: 'check', render: ViewReview },
  { key: 'export', label: 'Export', icon: 'package', render: ViewExport },
];

function go(view) { state.view = view; render(); }

function Rail() {
  const open = state.enriched.filter(e => ['review', 'fix'].includes(statusOf(e).key)).length;
  const stages = [
    ['Artikel gelesen', state.products.length > 0],
    ['Klassifiziert', state.classified.length > 0],
    ['Modelle verglichen', !!state.compare],
    ['Merkmale befüllt', state.enriched.length > 0],
    ['BMEcat geprüft', !!state.validation],
  ];
  return el('nav', { class: 'rail', 'aria-label': 'Bereiche' },
    el('div', { class: 'rail__brand' },
      el('div', { class: 'rail__mark' }, 'ET'),
      el('span', { class: 'rail__name' }, 'ETIM-Pipeline')),
    el('div', { class: 'rail__nav' }, VIEWS.map(v => el('button', {
      class: 'rail__item', type: 'button',
      'aria-current': state.view === v.key ? 'page' : null,
      onClick: () => go(v.key),
    }, icon(v.icon, 'rail__icon'), el('span', {}, v.label),
       v.key === 'review' && open ? el('span', { class: 'rail__badge' }, String(open)) : null,
       v.key === 'catalog' && state.run?.state === 'running'
         ? el('span', { class: 'rail__badge rail__badge--live' }, '·') : null))),
    el('div', { class: 'rail__foot' },
      el('span', { class: 'label' }, 'Pipeline'),
      stages.map(([name, done]) => el('div', { class: 'rail__stage' },
        el('span', { class: `rail__dot rail__dot--${done ? 'done' : 'todo'}` }),
        el('span', { class: done ? '' : 'faint' }, name)))),
  );
}

function Topbar() {
  const dark = document.documentElement.dataset.theme === 'dark';
  return el('header', { class: 'topbar' },
    el('h1', { class: 'topbar__title' }, VIEWS.find(v => v.key === state.view).label),
    el('div', { class: 'topbar__sep' }),
    el('div', { class: 'topbar__meta' },
      el('span', {}, 'Job'),
      state.jobs.length > 1 ? el('select', {
        class: 'jobpick', 'aria-label': 'Job wählen',
        onChange: e => switchJob(e.target.value),
      }, state.jobs.map(x => el('option', { value: x.job, selected: x.job === state.job || null }, x.job)))
        : el('span', { class: 'code' }, state.job || '—'),
      el('span', { class: 'faint' }, '·'),
      el('span', {}, state.config.etim_version),
      el('span', { class: 'faint' }, '·'),
      el('span', {}, `Schwelle ${Math.round(state.config.review_threshold * 100)} %`),
      el('span', { class: 'faint' }, '·'),
      el('span', {}, `Mindestabdeckung ${Math.round(state.config.min_coverage * 100)} %`),
    ),
    el('div', { class: 'topbar__spacer' }),
    el('div', { class: 'topbar__actions' },
      state.demo ? Badge('Beispieldaten', 'accent') : null,
      state.jev ? Badge(state.jev.simulated ? 'Jev simuliert'
                        : state.jev.ready ? 'Jev bereit' : 'Jev fehlt',
                        state.jev.simulated ? 'warn' : state.jev.ready ? 'ok' : 'crit') : null,
      Button('', { variant: 'ghost', size: 'sm', iconName: dark ? 'sun' : 'moon',
        title: dark ? 'Helles Thema' : 'Dunkles Thema',
        onClick: () => {
          document.documentElement.dataset.theme = dark ? 'light' : 'dark';
          try { localStorage.setItem('etim-theme', dark ? 'light' : 'dark'); } catch {}
          render();
        } }),
    ),
  );
}

function render() {
  const root = document.getElementById('app');
  const view = VIEWS.find(v => v.key === state.view);
  root.replaceChildren(Rail(), el('div', { class: 'main' }, Topbar(), view.render()));
}

/* ------------------------------------------------------------ Entscheidungen */

function setDecision(pid, status) {
  const cur = state.decisions[pid]?.status;
  if (cur === status) delete state.decisions[pid];
  else state.decisions[pid] = { status, ts: new Date().toISOString() };
  render();
  if (state.demo) { toast('Beispielmodus — nicht gespeichert'); return; }
  fetch(`api/decisions?job=${encodeURIComponent(state.job)}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(state.decisions),
  }).then(r => toast(r.ok ? 'Gespeichert in review.decisions.json' : 'Speichern fehlgeschlagen'))
    .catch(() => toast('Speichern fehlgeschlagen'));
}

/* -------------------------------------------------------------------- Start */

async function boot() {
  try { const t = localStorage.getItem('etim-theme'); if (t) document.documentElement.dataset.theme = t; } catch {}
  await loadJobs();

  const wanted = new URLSearchParams(location.search).get('job');
  const candidates = [wanted, state.jobs.find(j => j.has_compare)?.job, state.jobs[0]?.job].filter(Boolean);
  let data = null;
  for (const job of candidates) {
    try {
      const res = await fetch(`api/job?job=${encodeURIComponent(job)}`);
      if (res.ok) { data = await res.json(); break; }
    } catch { break; }
  }
  if (!data && state.serverUp && !state.jobs.length) {
    // Server läuft, aber es gibt noch keinen Job: direkt zum Einspielen.
    state.view = 'catalog';
    render();
    return;
  }
  if (!data) { data = window.ETIM_DEMO; state.demo = true; }
  Object.assign(state, data);
  state.decisions = data.decisions || {};
  if (!state.demo && state.job) history.replaceState(null, '', `?job=${encodeURIComponent(state.job)}`);
  if (state.compare) state.view = 'compare';
  else if (!state.enriched.length) state.view = state.demo ? 'overview' : 'catalog';
  render();
  if (state.run?.state === 'running') pollRun(state.job);
}

boot();

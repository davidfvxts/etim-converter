/* Ansichten und Zustand des Prüf-Cockpits.
   Die Daten sind die Zwischenstände der Pipeline aus out/<job>/. Geschrieben
   wird nur eine Datei: review.decisions.json (Freigaben und Korrekturvermerke). */

import { el, icon, Badge, Meter, StatRow, Card, Button, SearchField, Segmented,
         Table, Dist, Empty, Note, Quote, toast } from './components.js';

const state = {
  job: '', demo: false, config: { review_threshold: 0.75, min_coverage: 0.30, etim_version: 'ETIM-10.0' },
  products: [], classified: [], enriched: [], validation: null, files: [],
  labels: {}, decisions: {},
  view: 'overview', selected: null, filter: 'review', query: '',
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

/* ---------------------------------------------------------------------- Gerüst */

const VIEWS = [
  { key: 'overview', label: 'Übersicht', icon: 'gauge', render: ViewOverview },
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
       v.key === 'review' && open ? el('span', { class: 'rail__badge' }, String(open)) : null))),
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
      el('span', {}, 'Job'), el('span', { class: 'code' }, state.job || '—'),
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
  fetch('api/decisions', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(state.decisions),
  }).then(r => toast(r.ok ? 'Gespeichert in review.decisions.json' : 'Speichern fehlgeschlagen'))
    .catch(() => toast('Speichern fehlgeschlagen'));
}

/* -------------------------------------------------------------------- Start */

async function boot() {
  try { const t = localStorage.getItem('etim-theme'); if (t) document.documentElement.dataset.theme = t; } catch {}
  let data = null;
  try {
    const res = await fetch('api/job');
    if (res.ok) data = await res.json();
  } catch { /* Datei direkt geöffnet: Beispieldaten */ }
  if (!data) { data = window.ETIM_DEMO; state.demo = true; }
  Object.assign(state, data);
  state.decisions = data.decisions || {};
  if (!state.enriched.length) state.view = 'overview';
  render();
}

boot();

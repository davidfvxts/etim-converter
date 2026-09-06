/* Komponentenbibliothek. Reines DOM, kein Framework: das Werkzeug laeuft
   offline neben einem Python-CLI, ein npm-Build waere hier reine Last.
   Jede Funktion gibt ein fertiges Element zurueck und kennt keinen Zustand. */

/** Element bauen. attrs kennt class, text, html, on* und beliebige Attribute. */
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (k === 'html') node.innerHTML = v;
    else if (k === 'dataset') Object.assign(node.dataset, v);
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2).toLowerCase(), v);
    else node.setAttribute(k, v === true ? '' : String(v));
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return node;
}

/* Strichsymbole, einheitliche 16er-Box, erben die Textfarbe. */
const PATHS = {
  gauge:   '<path d="M2 12a10 10 0 0 1 20 0"/><path d="M12 12l4.5-3.5"/>',
  list:    '<path d="M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01"/>',
  check:   '<path d="M20 6 9 17l-5-5"/>',
  package: '<path d="M21 8v8l-9 5-9-5V8l9-5 9 5Z"/><path d="m3 8 9 5 9-5M12 13v8"/>',
  search:  '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  warn:    '<path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9 2.4 17.1A2 2 0 0 0 4.1 20h15.8a2 2 0 0 0 1.7-2.9L13.7 3.9a2 2 0 0 0-3.4 0Z"/>',
  info:    '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
  file:    '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5Z"/><path d="M14 3v5h5"/>',
  inbox:   '<path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-7"/><path d="M3 12h5l1.5 2.5h5L16 12h5L18 4H6L3 12Z"/>',
  arrow:   '<path d="M5 12h14M13 6l6 6-6 6"/>',
  sun:     '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon:    '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"/>',
};

export function icon(name, cls = '') {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '1.7');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  if (cls) svg.setAttribute('class', cls);
  svg.innerHTML = PATHS[name] || '';
  return svg;
}

export function Badge(text, tone = 'idle', { dot = false } = {}) {
  return el('span', { class: `badge badge--${tone}` }, dot ? el('span', { class: 'badge__dot' }) : null, text);
}

/** Balken 0..1. Der Ton folgt dem Wert, damit der Zustand auch ohne Zahl lesbar ist. */
export function Meter(value, { tone, showPct = true } = {}) {
  const v = Math.max(0, Math.min(1, Number(value) || 0));
  const t = tone || (v >= 0.75 ? 'ok' : v >= 0.4 ? 'warn' : 'crit');
  return el('div', { class: `meter meter--${t}`, role: 'img', 'aria-label': `${Math.round(v * 100)} Prozent` },
    el('div', { class: 'meter__track' }, el('div', { class: 'meter__fill', style: `width:${v * 100}%` })),
    showPct ? el('span', { class: 'meter__pct' }, `${Math.round(v * 100)}%`) : null,
  );
}

export function Stat({ label, value, unit, sub, tone }) {
  return el('div', { class: 'stat' },
    el('div', { class: 'stat__label' }, label),
    el('div', { class: `stat__value${tone ? ` stat__value--${tone}` : ''}` },
      String(value), unit ? el('span', { class: 'stat__unit' }, unit) : null),
    sub ? el('div', { class: 'stat__sub' }, sub) : null,
  );
}

export function StatRow(stats) {
  return el('div', { class: 'stats' }, stats.map(Stat));
}

export function Card(title, body, { action, flush = false } = {}) {
  return el('section', { class: 'card' },
    title ? el('header', { class: 'card__head' },
      el('h2', { class: 'card__title' }, title), action || null) : null,
    el('div', { class: `card__body${flush ? ' card__body--flush' : ''}` }, body),
  );
}

export function Button(label, { variant = '', size = '', iconName, onClick, pressed, disabled, title } = {}) {
  return el('button', {
    class: ['btn', variant && `btn--${variant}`, size && `btn--${size}`].filter(Boolean).join(' '),
    type: 'button', onClick, disabled,
    title: title || null,
    'aria-pressed': pressed === undefined ? null : String(pressed),
  }, iconName ? icon(iconName, 'btn__icon') : null, label);
}

export function SearchField(placeholder, onInput) {
  return el('div', { class: 'field' },
    icon('search', 'field__icon'),
    el('input', { class: 'field__input', type: 'search', placeholder, 'aria-label': placeholder, onInput }),
  );
}

/** Filterumschaltung. options: [{value, label, count}] */
export function Segmented(options, active, onChange) {
  return el('div', { class: 'seg', role: 'tablist' },
    options.map(o => el('button', {
      class: 'seg__btn', type: 'button', role: 'tab',
      'aria-selected': String(o.value === active),
      onClick: () => onChange(o.value),
    }, o.label, o.count === undefined ? null : el('span', { class: 'seg__count' }, String(o.count)))),
  );
}

/** Tabelle aus Spaltendefinitionen. cols: [{key,label,num,render}] */
export function Table(cols, rows, { onRowClick } = {}) {
  return el('div', { class: 'scroll-x' },
    el('table', { class: `table${onRowClick ? ' table--clickable' : ''}` },
      el('thead', {}, el('tr', {}, cols.map(c => el('th', { class: c.num ? 'is-num' : '' }, c.label)))),
      el('tbody', {}, rows.map(r => el('tr', {
        onClick: onRowClick ? () => onRowClick(r) : null,
      }, cols.map(c => el('td', { class: c.num ? 'is-num' : '' }, c.render ? c.render(r) : String(r[c.key] ?? '—')))))),
    ),
  );
}

export function Dist(rows, { tone = '' } = {}) {
  const max = Math.max(1, ...rows.map(r => r.n));
  return el('div', { class: 'dist' },
    rows.map(r => el('div', { class: 'dist__row' },
      el('div', { class: 'dist__label' },
        el('div', { class: 'dist__name' }, r.name),
        r.code ? el('div', { class: 'dist__code' }, r.code) : null,
        el('div', { class: 'dist__track' },
          el('div', { class: `dist__fill${tone ? ` dist__fill--${tone}` : ''}`, style: `width:${(r.n / max) * 100}%` })),
      ),
      el('div', { class: 'dist__n' }, String(r.n)),
    )),
  );
}

export function Empty(title, hint, iconName = 'inbox') {
  return el('div', { class: 'empty' },
    icon(iconName, 'empty__icon'),
    el('div', { class: 'empty__title' }, title),
    hint ? el('p', { class: 'empty__hint' }, hint) : null,
  );
}

export function Note(text, tone = 'warn') {
  return el('div', { class: `note${tone === 'accent' ? ' note--accent' : ''}` },
    icon(tone === 'accent' ? 'info' : 'warn', 'note__icon'),
    el('div', {}, text),
  );
}

/** Katalogbeleg. Ohne Zitat wird der fehlende Beleg benannt, nicht verschwiegen. */
export function Quote(source, reason) {
  if (source) return el('p', { class: 'quote' }, source);
  if (reason) return el('p', { class: 'quote quote--missing' }, reason);
  return null;
}

let toastTimer;
export function toast(message) {
  let node = document.querySelector('.toast');
  if (!node) { node = el('div', { class: 'toast', role: 'status', 'aria-live': 'polite' }); document.body.append(node); }
  node.textContent = message;
  node.classList.add('is-open');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove('is-open'), 2400);
}

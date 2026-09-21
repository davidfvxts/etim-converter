/**
 * Jev-Vorschaltung fuer die ETIM-Pipeline.
 *
 * Die Pipeline spricht nicht direkt mit Workers AI, sondern mit diesem Worker.
 * Damit liegen die Zugangsdaten als Cloudflare-Secret hier und nicht in der App,
 * im Repo oder in einer .env auf einem Rechner.
 *
 * Kein offener Proxy:
 *  - nur POST /jev
 *  - Shared Secret im Authorization-Header, zeitkonstant verglichen
 *  - das Modell steht fest im Code; ein Aufrufer kann kein anderes waehlen
 *  - der Rumpf muss die Form eines System-One-Aufrufs haben
 *
 * Deployment:
 *   cd worker
 *   npx wrangler secret put ETIM_PROXY_SECRET
 *   npx wrangler deploy
 */

export interface Env {
  AI: Ai;
  ETIM_PROXY_SECRET: string;
}

/** Das einzige Modell, das dieser Worker aufrufen darf. */
const MODEL = 'typesafe/jev' as const;

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8' },
  });

/**
 * Zeitkonstanter Vergleich. Erst hashen, dann vergleichen: timingSafeEqual
 * verlangt gleiche Laenge, und die Laenge des Secrets soll nicht durchsickern.
 */
async function secretMatches(given: string, expected: string): Promise<boolean> {
  if (!expected) return false;
  const enc = new TextEncoder();
  const [a, b] = await Promise.all([
    crypto.subtle.digest('SHA-256', enc.encode(given)),
    crypto.subtle.digest('SHA-256', enc.encode(expected)),
  ]);
  return crypto.subtle.timingSafeEqual(new Uint8Array(a), new Uint8Array(b));
}

function bearer(request: Request): string {
  const header = request.headers.get('authorization') ?? '';
  const match = /^Bearer\s+(.+)$/i.exec(header.trim());
  return match ? match[1] : '';
}

/** Nur was wie ein System-One-Aufruf aussieht, geht weiter. */
function validate(body: unknown): { state: unknown; questions: Record<string, unknown> } | string {
  if (typeof body !== 'object' || body === null) return 'Rumpf ist kein Objekt.';
  const { state, questions } = body as Record<string, unknown>;
  if (state === undefined || state === null) return 'Feld "state" fehlt.';
  if (typeof questions !== 'object' || questions === null || Array.isArray(questions)) {
    return 'Feld "questions" fehlt oder ist kein Objekt.';
  }
  const keys = Object.keys(questions);
  if (keys.length === 0) return 'Keine Frage uebergeben.';
  if (keys.length > 200) return 'Zu viele Fragen in einem Aufruf.';
  for (const key of keys) {
    const q = (questions as Record<string, unknown>)[key];
    if (typeof q !== 'object' || q === null) return `Frage "${key}" ist kein Objekt.`;
    const type = (q as Record<string, unknown>).type;
    if (type !== 'choice' && type !== 'score' && type !== 'noul') {
      return `Frage "${key}" hat keinen gueltigen Typ (choice, score oder noul).`;
    }
  }
  return { state, questions: questions as Record<string, unknown> };
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const { pathname } = new URL(request.url);

    if (pathname !== '/jev' && pathname !== '/health') {
      return json({ error: 'Not found' }, 404);
    }
    if (!(await secretMatches(bearer(request), env.ETIM_PROXY_SECRET))) {
      // Auch /health verlangt das Secret: der Worker soll von aussen stumm sein.
      return json({ error: 'Unauthorized' }, 401);
    }
    if (pathname === '/health') {
      return json({ ok: true, model: MODEL });
    }
    if (request.method !== 'POST') {
      return json({ error: 'Method not allowed' }, 405);
    }

    let body: unknown;
    try {
      body = await request.json();
    } catch {
      return json({ error: 'Rumpf ist kein gueltiges JSON.' }, 400);
    }

    const checked = validate(body);
    if (typeof checked === 'string') {
      return json({ error: checked }, 422);
    }

    try {
      const result = await env.AI.run(MODEL, {
        state: checked.state,
        questions: checked.questions,
      } as never);
      return json(result);
    } catch (err) {
      // Fehlertext weitergeben, damit er im Dashboard lesbar ankommt —
      // aber nichts aus der Worker-Umgebung mitschicken.
      const message = err instanceof Error ? err.message : 'Unbekannter Fehler';
      return json({ error: `Workers AI: ${message}` }, 502);
    }
  },
} satisfies ExportedHandler<Env>;

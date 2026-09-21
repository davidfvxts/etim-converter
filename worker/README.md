# etim-converter

Cloudflare Worker, über den die Pipeline Jev (`typesafe/jev`) aufruft.

Zweck: Die Zugangsdaten für Workers AI liegen als Cloudflare-Secret beim Worker
und nicht in der App. Die App kennt nur die Worker-Adresse und ein Shared Secret.

## Warum kein offener Proxy

* nur `POST /jev` (und `GET /health`), alles andere → 404
* jede Anfrage braucht `Authorization: Bearer <ETIM_PROXY_SECRET>`, zeitkonstant
  verglichen (SHA-256 → `timingSafeEqual`), auch `/health`
* das Modell steht fest im Quelltext — ein Aufrufer kann kein anderes wählen
* der Rumpf muss die Form eines System-One-Aufrufs haben (`state` + `questions`,
  jede Frage vom Typ `choice`, `score` oder `noul`)

Wer mehr Schutz will, legt zusätzlich Cloudflare Access vor die Route; das
Shared Secret bleibt dann als zweite Schranke bestehen.

## Einrichten

```bash
cd worker
npm install
npx wrangler secret put ETIM_PROXY_SECRET     # z. B. `openssl rand -hex 32`
npx wrangler deploy
```

Danach in der `.env` der App:

```
ETIM_JEV_TRANSPORT=worker
ETIM_JEV_WORKER_URL=https://etim-converter.<subdomain>.workers.dev/jev
ETIM_JEV_WORKER_SECRET=<dasselbe Secret>
```

Prüfen:

```bash
curl -H "Authorization: Bearer $ETIM_JEV_WORKER_SECRET" \
     https://etim-converter.<subdomain>.workers.dev/health
```

## Vertrag

Anfrage:

```json
{ "state": { "article_name": "…" },
  "questions": { "etim_class": { "type": "choice", "instructions": "…", "criteria": { "EC004089": "…" } } } }
```

Antwort: die Jev-Antwort unverändert —
`{ "model": "jev-1.13.0", "answers": { … }, "usage": { … } }`.

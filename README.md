# vulkkano-amazon-reviews

Relay semanal de reseñas de Amazon para Vulkkano. Un GitHub Action (`.github/workflows/scan.yml`)
llama a la API "Real-Time Amazon Data" de OpenWeb Ninja para los ASIN activos en `tracked_products`
(Supabase, proyecto "VULKKANO MKT") en 10 marketplaces europeos, y publica el resultado combinado
en `data/amazon-reviews.json`.

Existe porque las rutinas programadas de Claude Code corren en un entorno con salida de red
restringida por política de organización (allow-list), y `api.openwebninja.com` no está permitido
ahí. GitHub Actions sí tiene salida abierta, así que hace la llamada aquí y dej a el resultado en
este repo; la rutina de Claude "Scraper reseñas Amazon (OpenWeb Ninja)" solo lee
`data/amazon-reviews.json` vía `raw.githubusercontent.com` (permitido) e inserta lo nuevo en
Supabase. Mismo patrón que `vulkkano-reddit-watch` para las menciones de Reddit.

Si se añaden o quitan ASIN de `tracked_products`, hay que actualizar a mano el array `ASINS` en
`scan.yml`.

## Cookies de Amazon (imprescindibles para tener todas las reseñas recientes)

Sin cookie, Amazon solo enseña las ~8 reseñas destacadas de cada ficha y la API ignora
`sort_by=MOST_RECENT` y no pagina (comprobado el 2026-11-17). Con la cookie de una cuenta de
**comprador** con sesión iniciada, `scripts/scan.py` pide las más recientes y pagina hasta
encontrar una reseña ya vista.

Un secret por marketplace: `AMAZON_COOKIE_ES`, `_DE`, `_FR`, `_IT`, `_UK`, `_NL`, `_BE`, `_PL`,
`_SE`, `_IE`. Valor: `session-id=...; ubid-acbXX=...; x-acbXX=...; at-acbXX=...` (XX = es, de,
fr, it, uk...), copiado de DevTools → Application → Cookies con la sesión iniciada en ese Amazon.
Si una cookie caduca, el run sale en rojo indicando qué marketplace renovar.

Carga inicial tras añadir cookies: Actions → Amazon reviews scan → Run workflow con
`backfill` marcado y `max_pages` = 10.

## Ficheros en `data/`

- `reviews-store.json`: todas las reseñas vistas alguna vez y cuándo se vieron por primera vez.
- `amazon-reviews.json`: lo que importa la rutina — vistas por primera vez en los últimos 60 días
  (ventana deslizante: una semana sin importar no pierde reseñas; el INSERT es idempotente).
- `scan-status.json`: resumen y errores de la última ejecución.


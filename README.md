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

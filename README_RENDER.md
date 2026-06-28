# Little Hotelier en Render

Este repositorio ejecuta la sincronización Little Hotelier → Limpatex en Render como **worker Docker con disco persistente**.

## Por qué worker y no cron puro

Little Hotelier/SiteMinder no expone una API estable con token fijo para este flujo. El scraper depende de cookies de sesión y a veces necesita Playwright para renovar login.

Render Cron Jobs no es ideal para esto porque no conserva bien perfil/cookies entre ejecuciones. El worker usa disco persistente:

- `/data/lh_cookies.json`
- `/data/browser-profile`
- `/data/lh_sync_state.json`
- `/data/debug`

## Qué hace ahora

- Ejecuta `python little_hotelier_sync.py --loop`.
- Comprueba cada `SCHEDULER_POLL_SECONDS` segundos si toca sincronizar.
- Sincroniza a las `RUN_AT_HOURS` en `RUN_TIMEZONE`, por defecto `09:00,14:00,20:00` en `Europe/Madrid`.
- Usa cookies existentes si siguen vivas.
- Si la sesión caduca, intenta auto-login con `LH_EMAIL` y `LH_PASSWORD`.
- Si auto-login funciona, guarda cookies/token en disco y, opcionalmente, en Supabase.
- Si falla, guarda estado y envía alerta por email si está configurado.

## Variables obligatorias en Render

Configúralas como secretas en el dashboard de Render:

- `LH_PROPERTY_UUID`
- `LH_EMAIL`
- `LH_PASSWORD`
- `LH_SESSION_TOKEN` inicial, si existe
- `LH_COOKIES_JSON` inicial, pegando el contenido completo de `lh_cookies.json`
- `APP_URL`
- `APP_API_KEY`

Tu captura muestra que ya usabas una Supabase Edge Function:

```env
APP_URL=https://qyipyygojlfhdghnraus.supabase.co/functions/v1/little-hotelier-sync
```

Ese valor es correcto si esa función es la que recibe/upserta reservas. El script ahora detecta si `APP_URL` ya es un endpoint completo `/functions/v1/...` y lo usa tal cual. Solo añade `/api/reservations` cuando `APP_URL` es una base tipo `https://gestionlimpatex.vercel.app`.

## Variables recomendadas para alertas

Opción Resend:

- `ALERT_EMAIL_FROM=alertas@limpatex.com`
- `RESEND_API_KEY`

Opción SMTP:

- `ALERT_EMAIL_FROM`
- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USER`
- `SMTP_PASSWORD`
- `SMTP_USE_TLS=1`

`ALERT_EMAIL_TO` ya queda en `render.yaml` como `dgomezlimpatex@gmail.com`.

## Supabase como backup de cookies/estado

Opcional, recomendado cuando quieras evitar tocar variables de Render tras cada login manual.

1. Ejecuta `docs/supabase-lh-state.sql` en Supabase SQL Editor.
2. En Render configura:
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_ROLE_KEY`
   - `LH_SECRET_STORE_ENABLED=1`
3. Localmente, cuando tengas que renovar manualmente:

```bash
python little_hotelier_sync.py --login --push-cookies
```

Eso guardará cookies/token localmente y también en Supabase.

## Primer despliegue

1. Sube el repo a GitHub.
2. En Render crea/actualiza el Blueprint desde `render.yaml`.
3. Rellena variables secretas.
4. Ejecuta localmente si aún no tienes cookies:

```bash
python little_hotelier_sync.py --login
```

5. Copia el contenido completo de `lh_cookies.json` en `LH_COOKIES_JSON` de Render.
6. Para smoke test temporal, pon `RUN_ON_START=1`, reinicia/deploya y revisa logs.
7. Si sincroniza correctamente, vuelve a `RUN_ON_START=0`.

## Comandos útiles

```bash
python little_hotelier_sync.py --validate-config
python little_hotelier_sync.py --status
python little_hotelier_sync.py --debug
python little_hotelier_sync.py --list --days 30
python little_hotelier_sync.py --test-alert
python little_hotelier_sync.py --login
python little_hotelier_sync.py --login --push-cookies
```

## Si el login automático falla

Puede ocurrir si SiteMinder pide captcha/2FA o bloquea IPs de datacenter.

Plan B operativo:

1. Ejecuta en local:

```bash
python little_hotelier_sync.py --login
```

2. Si Supabase store está activo:

```bash
python little_hotelier_sync.py --login --push-cookies
```

3. Si Supabase store no está activo, copia `LH_SESSION_TOKEN` y el contenido completo de `lh_cookies.json` a Render.

## Verificación técnica

```bash
python -m py_compile little_hotelier_sync.py
python -m pytest -q
python little_hotelier_sync.py --validate-config
```

## Nota de Playwright

El Dockerfile usa `mcr.microsoft.com/playwright/python:v1.60.0-jammy` y `requirements.txt` fija `playwright==1.60.0`. Mantén ambas versiones alineadas.

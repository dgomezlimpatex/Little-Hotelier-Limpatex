# Little Hotelier en Render

Este repositorio puede ejecutarse en Render como un worker continuo para sincronizar reservas de Little Hotelier sin depender de tu ordenador.

## Por que es un worker y no un cron

Little Hotelier no esta funcionando como una API estable con token fijo. El sincronizador depende de cookies de sesion y, cuando caducan, intenta renovar la sesion con navegador automatizado.

Render Cron Jobs no permite discos persistentes. Para conservar cookies y perfil de navegador entre ejecuciones necesitamos un worker con disco.

## Que hace

- Ejecuta `python little_hotelier_sync.py --loop`.
- Sincroniza cada `LOOP_INTERVAL` segundos.
- Guarda cookies en `/data/lh_cookies.json`.
- Guarda perfil persistente de navegador en `/data/browser-profile`.
- Si las cookies siguen vivas, sincroniza sin login.
- Si caducan, intenta login automatico con `LH_EMAIL` y `LH_PASSWORD`.

## Variables de entorno

Estas variables se configuran en Render. No deben subirse al repositorio.

Obligatorias:

- `LH_PROPERTY_UUID`
- `LH_EMAIL`
- `LH_PASSWORD`
- `LH_SESSION_TOKEN`
- `LH_COOKIES_JSON`
- `APP_URL`
- `APP_API_KEY`

Configuradas por el Blueprint:

- `LH_REGION=emea`
- `LH_COOKIES_PATH=/data/lh_cookies.json`
- `LH_BROWSER_PROFILE_DIR=/data/browser-profile`
- `LH_HEADLESS=1`
- `DAYS_BACK=7`
- `DAYS_AHEAD=30`
- `LOOP_INTERVAL=3600`

## Primer despliegue

1. Sube este repo a GitHub.
2. En Render, crea un Blueprint desde el repositorio.
3. Render leera `render.yaml` y creara `little-hotelier-limpatex-worker`.
4. Rellena las variables marcadas como secretas.
5. En `LH_COOKIES_JSON`, pega el contenido completo de `lh_cookies.json`.
6. Aplica el Blueprint y revisa los logs.

## Si el login automatico falla

Puede ocurrir si SiteMinder/Little Hotelier bloquea el login desde un navegador headless en la nube.

Plan B operativo:

1. Ejecuta localmente:
   `python little_hotelier_sync.py --login`
2. Copia el nuevo `LH_SESSION_TOKEN` a Render.
3. Copia el contenido actualizado de `lh_cookies.json` a `LH_COOKIES_JSON`.
4. Redeploy o reinicia el worker.

El objetivo es que esto sea excepcional. Mientras Little Hotelier mantenga vivas las cookies, Render sincronizara solo.

## Coste estimado

Este enfoque requiere un worker de pago y un disco pequeno en Render. El plan `starter` suele ser suficiente para esta primera version.

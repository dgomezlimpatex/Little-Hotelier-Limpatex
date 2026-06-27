# Runbook — Little Hotelier → Limpatex

## Objetivo

Operar la sincronización automática sin depender del PC local salvo cuando SiteMinder exija intervención humana.

## Estado normal

Render worker queda corriendo y sincroniza a:

- 09:00 Europe/Madrid
- 14:00 Europe/Madrid
- 20:00 Europe/Madrid

El estado local en Render se guarda en:

```text
/data/lh_sync_state.json
```

## Diagnóstico local

Desde la carpeta del repo:

```bash
python little_hotelier_sync.py --validate-config
python little_hotelier_sync.py --status
python little_hotelier_sync.py --debug
```

## Renovar sesión manualmente

Si llega email de sesión caducada o auto-login fallido:

```bash
python little_hotelier_sync.py --login
```

Si Supabase store está activo:

```bash
python little_hotelier_sync.py --login --push-cookies
```

Si Supabase store NO está activo:

1. Ejecuta `--login`.
2. Copia `LH_SESSION_TOKEN` actualizado desde `.env` a Render.
3. Copia el contenido completo de `lh_cookies.json` a `LH_COOKIES_JSON` en Render.
4. Reinicia/deploya el worker.

## Probar email

```bash
python little_hotelier_sync.py --test-alert
```

Si falla, revisa:

- `RESEND_API_KEY` + `ALERT_EMAIL_FROM`, o
- `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `ALERT_EMAIL_FROM`.

## Probar Render al desplegar

1. Cambia temporalmente en Render:

```env
RUN_ON_START=1
```

2. Reinicia/deploya.
3. Revisa logs:
   - cookies cargadas;
   - reservas obtenidas;
   - reservas enviadas;
   - estado guardado.
4. Vuelve a:

```env
RUN_ON_START=0
```

## Errores comunes

### `BrowserType.launch: Executable doesn't exist`

Hay mismatch entre Dockerfile y `requirements.txt` de Playwright. Deben coincidir, ahora ambos están en `1.60.0`.

### URL final contiene `authx.siteminder.com`

Sesión caducada. Si auto-login falla, ejecutar login manual.

### Captcha / 2FA / MFA

No intentar saltarlo. Ejecutar login manual y subir cookies.

### 0 reservas inesperadas

Puede ser normal si no hay entradas en rango, pero si suele haber reservas, revisar:

- filtros `DAYS_BACK` / `DAYS_AHEAD`;
- cookies caducadas;
- HTML de Little Hotelier cambiado.

## Seguridad

No subir nunca:

- `.env`
- `lh_cookies.json`
- `sync_log.txt`
- `reservas_cache.json`
- screenshots/html de debug con sesión

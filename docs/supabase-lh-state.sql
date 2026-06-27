-- Estado privado para la integración Little Hotelier → Limpatex.
-- Ejecutar en Supabase SQL Editor si quieres activar LH_SECRET_STORE_ENABLED=1.

CREATE TABLE IF NOT EXISTS public.private_lh_integration_state (
  key text PRIMARY KEY,
  value jsonb NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.private_lh_integration_state ENABLE ROW LEVEL SECURITY;

-- No creamos policies para anon/authenticated.
-- El script accede con SUPABASE_SERVICE_ROLE_KEY desde backend/Render.
-- Nunca expongas SUPABASE_SERVICE_ROLE_KEY en frontend.

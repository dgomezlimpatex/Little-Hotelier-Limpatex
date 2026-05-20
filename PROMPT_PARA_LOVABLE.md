# Integración Little Hotelier → Limpatex
## Instrucciones para Lovable — Implementación completa paso a paso

---

## CONTEXTO DEL PROYECTO

Tengo una app de gestión de limpiezas llamada **Limpatex** con este stack:
- Frontend: Vite 5 + React 18 + TypeScript + Tailwind + shadcn/ui
- Backend: Supabase (PostgreSQL + Auth + RLS + Edge Functions)
- Proyecto Supabase ref: `qyipyygojlfhdghnraus`
- URL Supabase: `https://qyipyygojlfhdghnraus.supabase.co`

Ya tengo Edge Functions desplegadas para otras integraciones (hostaway-sync, avantio-sync).
Quiero añadir la integración con **Little Hotelier** siguiendo el mismo patrón.

---

## QUÉ NECESITO QUE HAGAS

Tengo un script Python externo (`little_hotelier_sync.py`) que ya funciona y que:
1. Se conecta a Little Hotelier (sistema de gestión hotelera)
2. Extrae las reservas de los próximos 30 días
3. Envía cada reserva a mi app vía **HTTP POST**

Necesito que implementes en mi app todo lo necesario para **recibir, almacenar y mostrar** esas reservas.

---

## PASO 1 — CREAR LA TABLA EN SUPABASE

Crea la siguiente tabla en Supabase ejecutando este SQL en el **SQL Editor** de Supabase:

```sql
-- Tabla principal de reservas de Little Hotelier
CREATE TABLE IF NOT EXISTS public.lh_reservations (
  id              BIGSERIAL PRIMARY KEY,
  external_id     TEXT UNIQUE NOT NULL,     -- ID numérico interno de Little Hotelier
  uuid            TEXT,                      -- UUID de la reserva en LH
  reference       TEXT,                      -- Ej: BDC-6726731254, BBN-...
  channel         TEXT,                      -- Ej: Booking.com, Direct Booking
  check_in        DATE,
  check_out       DATE,
  room            TEXT,                      -- Ej: Habitación 2
  guest_name      TEXT,                      -- Formato: "Apellido, Nombre"
  adults          INTEGER DEFAULT 0,
  children        INTEGER DEFAULT 0,
  infants         INTEGER DEFAULT 0,
  status          TEXT DEFAULT 'confirmed',  -- confirmed, cancelled, no_show
  total           TEXT,                      -- Ej: "72 €"
  synced_at       TIMESTAMPTZ,
  source_system   TEXT DEFAULT 'little_hotelier',
  created_at      TIMESTAMPTZ DEFAULT now(),
  updated_at      TIMESTAMPTZ DEFAULT now()
);

-- Índices para búsquedas frecuentes
CREATE INDEX IF NOT EXISTS idx_lh_reservations_check_in  ON public.lh_reservations(check_in);
CREATE INDEX IF NOT EXISTS idx_lh_reservations_check_out ON public.lh_reservations(check_out);
CREATE INDEX IF NOT EXISTS idx_lh_reservations_status    ON public.lh_reservations(status);
CREATE INDEX IF NOT EXISTS idx_lh_reservations_room      ON public.lh_reservations(room);

-- Actualizar updated_at automáticamente
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_lh_reservations_updated_at
  BEFORE UPDATE ON public.lh_reservations
  FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
```

### Configurar RLS (Row Level Security)

```sql
-- Habilitar RLS
ALTER TABLE public.lh_reservations ENABLE ROW LEVEL SECURITY;

-- Permitir lectura a usuarios autenticados
CREATE POLICY "Usuarios autenticados pueden leer reservas"
  ON public.lh_reservations FOR SELECT
  TO authenticated
  USING (true);

-- Permitir escritura solo al service_role (el script Python usará service_role key)
CREATE POLICY "Solo service_role puede insertar/actualizar"
  ON public.lh_reservations FOR ALL
  TO service_role
  USING (true)
  WITH CHECK (true);
```

---

## PASO 2 — CREAR LA EDGE FUNCTION

Crea una nueva Edge Function llamada **`little-hotelier-sync`** en Supabase.

### Archivo: `supabase/functions/little-hotelier-sync/index.ts`

```typescript
import { serve } from "https://deno.land/std@0.168.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers":
    "authorization, x-client-info, apikey, content-type",
};

serve(async (req: Request) => {
  // Manejar preflight CORS
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: corsHeaders });
  }

  // Solo aceptar POST
  if (req.method !== "POST") {
    return new Response(JSON.stringify({ error: "Method not allowed" }), {
      status: 405,
      headers: { ...corsHeaders, "Content-Type": "application/json" },
    });
  }

  try {
    // Verificar API key (opcional pero recomendado)
    const authHeader = req.headers.get("Authorization");
    const expectedKey = Deno.env.get("SYNC_API_KEY");
    if (expectedKey && authHeader !== `Bearer ${expectedKey}`) {
      return new Response(JSON.stringify({ error: "Unauthorized" }), {
        status: 401,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    // Parsear el cuerpo
    const reservation = await req.json();

    // Validar campos mínimos
    if (!reservation.external_id || !reservation.check_in || !reservation.check_out) {
      return new Response(
        JSON.stringify({ error: "Faltan campos obligatorios: external_id, check_in, check_out" }),
        {
          status: 400,
          headers: { ...corsHeaders, "Content-Type": "application/json" },
        }
      );
    }

    // Cliente Supabase con service_role para saltar RLS
    const supabase = createClient(
      Deno.env.get("SUPABASE_URL") ?? "",
      Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? ""
    );

    // Upsert: insertar o actualizar si ya existe (por external_id)
    const { data, error } = await supabase
      .from("lh_reservations")
      .upsert(
        {
          external_id:   reservation.external_id,
          uuid:          reservation.uuid          ?? null,
          reference:     reservation.reference     ?? null,
          channel:       reservation.channel       ?? null,
          check_in:      reservation.check_in,
          check_out:     reservation.check_out,
          room:          reservation.room          ?? null,
          guest_name:    reservation.guest_name    ?? null,
          adults:        reservation.adults        ?? 0,
          children:      reservation.children      ?? 0,
          infants:       reservation.infants       ?? 0,
          status:        reservation.status        ?? "confirmed",
          total:         reservation.total         ?? null,
          synced_at:     reservation.synced_at     ?? new Date().toISOString(),
          source_system: "little_hotelier",
        },
        {
          onConflict: "external_id",  // actualizar si ya existe este ID
        }
      )
      .select()
      .single();

    if (error) {
      console.error("Error en upsert:", error);
      return new Response(
        JSON.stringify({ error: error.message }),
        {
          status: 500,
          headers: { ...corsHeaders, "Content-Type": "application/json" },
        }
      );
    }

    return new Response(
      JSON.stringify({ success: true, data }),
      {
        status: 200,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      }
    );
  } catch (err) {
    console.error("Error inesperado:", err);
    return new Response(
      JSON.stringify({ error: "Error interno del servidor" }),
      {
        status: 500,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      }
    );
  }
});
```

### Desplegar la Edge Function

Desde la terminal, en la raíz del proyecto:

```bash
supabase functions deploy little-hotelier-sync
```

---

## PASO 3 — CONFIGURAR LA URL EN EL SCRIPT PYTHON

Una vez desplegada la Edge Function, la URL del endpoint será:

```
https://qyipyygojlfhdghnraus.supabase.co/functions/v1/little-hotelier-sync
```

El script Python ya enviará los datos a esta URL con este formato exacto:

```json
{
  "external_id":   "55998893",
  "uuid":          "4592b7fd-1efb-4ab8-b4d0-0c0ae44f258c",
  "reference":     "BDC-6726731254",
  "channel":       "Booking.com",
  "check_in":      "2026-05-19",
  "check_out":     "2026-05-20",
  "room":          "Habitación 2",
  "guest_name":    "Fisher, Michelle",
  "adults":        1,
  "children":      0,
  "infants":       0,
  "status":        "confirmed",
  "total":         "72 €",
  "synced_at":     "2026-05-19T09:14:57Z",
  "source_system": "little_hotelier"
}
```

---

## PASO 4 — MOSTRAR LAS RESERVAS EN LA UI

Crea un nuevo componente/página en la app para visualizar las reservas sincronizadas.

### Hook para obtener reservas: `src/hooks/useLHReservations.ts`

```typescript
import { useQuery } from "@tanstack/react-query";
import { supabase } from "@/integrations/supabase/client";

export interface LHReservation {
  id: number;
  external_id: string;
  uuid: string | null;
  reference: string | null;
  channel: string | null;
  check_in: string;
  check_out: string;
  room: string | null;
  guest_name: string | null;
  adults: number;
  children: number;
  infants: number;
  status: string;
  total: string | null;
  synced_at: string | null;
  created_at: string;
}

export function useLHReservations(dateFrom?: string, dateTo?: string) {
  return useQuery({
    queryKey: ["lh_reservations", dateFrom, dateTo],
    queryFn: async () => {
      let query = supabase
        .from("lh_reservations")
        .select("*")
        .order("check_in", { ascending: true });

      if (dateFrom) query = query.gte("check_in", dateFrom);
      if (dateTo)   query = query.lte("check_in", dateTo);

      const { data, error } = await query;
      if (error) throw error;
      return data as LHReservation[];
    },
  });
}
```

### Componente de tabla: `src/components/LHReservationsTable.tsx`

```typescript
import { useLHReservations } from "@/hooks/useLHReservations";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const STATUS_COLORS: Record<string, string> = {
  confirmed: "bg-green-100 text-green-800",
  cancelled: "bg-red-100 text-red-800",
  no_show:   "bg-gray-100 text-gray-800",
};

const STATUS_LABELS: Record<string, string> = {
  confirmed: "Confirmada",
  cancelled: "Cancelada",
  no_show:   "No show",
};

export function LHReservationsTable() {
  const { data: reservations, isLoading, error } = useLHReservations();

  if (isLoading) return <p className="text-muted-foreground">Cargando reservas...</p>;
  if (error)     return <p className="text-red-500">Error al cargar reservas.</p>;
  if (!reservations?.length) return <p className="text-muted-foreground">Sin reservas sincronizadas.</p>;

  return (
    <div className="rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Estado</TableHead>
            <TableHead>Huésped</TableHead>
            <TableHead>Referencia</TableHead>
            <TableHead>Canal</TableHead>
            <TableHead>Habitación</TableHead>
            <TableHead>Entrada</TableHead>
            <TableHead>Salida</TableHead>
            <TableHead>Huéspedes</TableHead>
            <TableHead>Total</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {reservations.map((r) => (
            <TableRow key={r.id}>
              <TableCell>
                <Badge className={STATUS_COLORS[r.status] ?? "bg-gray-100"}>
                  {STATUS_LABELS[r.status] ?? r.status}
                </Badge>
              </TableCell>
              <TableCell className="font-medium">{r.guest_name}</TableCell>
              <TableCell className="font-mono text-sm">{r.reference}</TableCell>
              <TableCell>{r.channel}</TableCell>
              <TableCell>{r.room}</TableCell>
              <TableCell>{r.check_in}</TableCell>
              <TableCell>{r.check_out}</TableCell>
              <TableCell>
                {r.adults}A {r.children > 0 ? `· ${r.children}N` : ""}{" "}
                {r.infants > 0 ? `· ${r.infants}B` : ""}
              </TableCell>
              <TableCell>{r.total}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
```

### Página completa: `src/pages/LHReservationsPage.tsx`

```typescript
import { LHReservationsTable } from "@/components/LHReservationsTable";

export default function LHReservationsPage() {
  return (
    <div className="container mx-auto py-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Reservas Little Hotelier</h1>
        <p className="text-muted-foreground">
          Sincronizadas automáticamente desde Little Hotelier
        </p>
      </div>
      <LHReservationsTable />
    </div>
  );
}
```

### Añadir la ruta en React Router (`src/App.tsx` o donde tengas las rutas):

```typescript
// Importar la página
import LHReservationsPage from "@/pages/LHReservationsPage";

// Añadir la ruta dentro de tu <Routes>
<Route path="/reservas-lh" element={<LHReservationsPage />} />
```

### Añadir enlace en el menú de navegación:

Busca el componente de navegación (sidebar o navbar) y añade un enlace:

```typescript
<Link to="/reservas-lh">Reservas LH</Link>
```

---

## PASO 5 — ACTUALIZAR EL SCRIPT PYTHON (.env)

Una vez que Lovable haya desplegado la Edge Function, actualiza el archivo `.env` del script Python:

```env
# Cambiar APP_URL a la Edge Function de Supabase
APP_URL=https://qyipyygojlfhdghnraus.supabase.co/functions/v1/little-hotelier-sync

# La anon key de Supabase como API key (la encuentras en Settings → API → anon key)
APP_API_KEY=<tu_supabase_anon_key>
```

---

## RESUMEN DE LO QUE DEBE QUEDAR HECHO

- [ ] Tabla `lh_reservations` creada en Supabase con RLS configurado
- [ ] Edge Function `little-hotelier-sync` desplegada y funcionando
- [ ] Hook `useLHReservations` para consultar las reservas desde React
- [ ] Componente `LHReservationsTable` para mostrarlas en tabla
- [ ] Página `/reservas-lh` accesible desde el menú de navegación
- [ ] `.env` del script Python actualizado con la URL de la Edge Function

---

## NOTA SOBRE EL SCRIPT PYTHON

El script Python (`little_hotelier_sync.py`) ya está completamente implementado y funciona.
Extrae automáticamente las reservas de Little Hotelier y las envía a la Edge Function.
Solo necesita que le des la URL correcta (`APP_URL`) en el `.env`.

Para probarlo una vez implementada la Edge Function:
```bash
python little_hotelier_sync.py --debug   # ver datos sin enviar
python little_hotelier_sync.py           # sincronización real
python little_hotelier_sync.py --loop    # bucle automático cada hora
```

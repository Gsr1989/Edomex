"""
EDOMEX — Configuración compartida entre el panel web (Flask) y el bot (aiogram).

Todo lo que estaba duplicado en los dos archivos vive aquí una sola vez:
cliente de Supabase, coordenadas del PDF, constantes y los DOS candados
globales (PDF y folio).
"""

import os
import sys
import logging
import threading
from datetime import datetime, date
from zoneinfo import ZoneInfo
from supabase import create_client, Client

# ===================== LOGGING =====================
sys.dont_write_bytecode = True
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("edomex")

# ===================== ZONA HORARIA =====================
TZ_CDMX = ZoneInfo("America/Mexico_City")


def now_cdmx() -> datetime:
    return datetime.now(TZ_CDMX)


def today_cdmx() -> date:
    return now_cdmx().date()


def parse_date_any(value) -> date:
    import re
    if not value:
        raise ValueError("Fecha vacía")
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=TZ_CDMX)
        else:
            value = value.astimezone(TZ_CDMX)
        return value.date()
    s = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return date.fromisoformat(s)
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ_CDMX)
    else:
        dt = dt.astimezone(TZ_CDMX)
    return dt.date()


# ===================== SUPABASE =====================
# Por variable de entorno. Los defaults son los que ya tenías, para que no se
# rompa si aún no las configuras en Render. Recomendado: ponlas en Render →
# Environment y borra los defaults de aquí.
SUPABASE_URL = os.getenv(
    "SUPABASE_URL",
    "https://xsagwqepoljfsogusubw.supabase.co"
)
SUPABASE_KEY = os.getenv(
    "SUPABASE_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InhzYWd3cWVwb2xqZnNvZ3VzdWJ3Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDM5NjM3NTUsImV4cCI6MjA1OTUzOTc1NX0.NUixULn0m2o49At8j6X58UqbXre2O2_JStqzls_8Gws"
)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ===================== CONFIG GENERAL =====================
BOT_TOKEN         = os.getenv("BOT_TOKEN", "")
BASE_URL          = os.getenv(
    "BASE_URL",
    "https://sfpyaedomexicoconsultapermisodigital.onrender.com"
).rstrip("/")
URL_CONSULTA_BASE = BASE_URL       # el QR apunta al mismo servicio fusionado

OUTPUT_DIR        = "documentos"
PLANTILLA_PDF     = "edomex_plantilla_alta_res.pdf"
PLANTILLA_BUENO   = "labuena3.0.pdf"

ENTIDAD           = "edomex"
DIAS_PERMISO      = 30
HORAS_LIMITE_PAGO = 48       # limpieza del panel
HORAS_TIMER_BOT   = 36       # timer del bot
PRECIO_PERMISO    = 180
BUCKET_NAME       = "permisos-edomex"
PAGE_SIZE         = 100

ADMIN_USER        = os.getenv("ADMIN_USER", "Serg890105tm3")
ADMIN_PASS        = os.getenv("ADMIN_PASS", "Serg890105tm3")
SECRET_KEY        = os.getenv("SECRET_KEY", "clave_muy_segura_123456")

# ⚠️ EN EDOMEX EL PANEL Y EL BOT COMPARTEN LA MISMA SERIE DE FOLIO (331).
# A diferencia de CDMX (412 panel / 122 bot), aquí los dos leen y escriben el
# mismo renglón de folio_watermark. Cuando eran servicios separados podían
# pedir el mismo número al mismo tiempo. Ahora que corren juntos, el FOLIO_LOCK
# de abajo garantiza que sólo uno genere folio a la vez.
FOLIO_PREFIJO     = "331"
FOLIO_INICIO      = 2        # si no hay watermark, arranca en 3312

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ===================== CANDADOS GLOBALES =====================
# PDF_LOCK — CRÍTICO: PyMuPDF (fitz) no es thread-safe. El panel ya tenía su
# lock; el BOT NO TENÍA NINGUNO. Como ahora comparten proceso, sin este candado
# compartido los PDFs se corromperían entre sí. Los dos módulos importan ESTE.
PDF_LOCK = threading.Lock()

# FOLIO_LOCK — serializa la asignación de folios entre panel y bot, que en
# EdoMex comparten la serie 331.
FOLIO_LOCK = threading.Lock()

# ===================== COORDENADAS PDF EDOMEX =====================
# Una sola copia. Antes estaban duplicadas idénticas en los dos archivos.
COORDS_EDOMEX = {
    "folio":     (535, 135, 14, (1, 0, 0)),
    "marca":     (109, 190,  9, (0, 0, 0)),
    "serie":     (230, 233,  9, (0, 0, 0)),
    "linea":     (238, 190,  9, (0, 0, 0)),
    "motor":     (104, 233,  9, (0, 0, 0)),
    "anio":      (410, 190,  9, (0, 0, 0)),
    "color":     (400, 233,  9, (0, 0, 0)),
    "fecha_exp": (190, 280,  9, (0, 0, 0)),
    "fecha_ven": (380, 280,  9, (0, 0, 0)),
    "nombre":    (394, 320,  9, (0, 0, 0)),
}

# ===================== TABLAS DEL PANEL =====================
TABLAS_DISPONIBLES = {
    'folios_registrados': {
        'nombre':      'Folios Registrados',
        'pk_col':      'folio',
        'search_cols': ['folio', 'marca', 'linea', 'numero_serie',
                        'numero_motor', 'nombre', 'estado', 'entidad', 'creado_por'],
        'columnas':    ['folio', 'marca', 'linea', 'anio', 'numero_serie',
                        'numero_motor', 'nombre', 'fecha_expedicion',
                        'fecha_vencimiento', 'entidad', 'estado', 'creado_por'],
    },
    'verificaciondigitalcdmx': {
        'nombre':      'Usuarios del Sistema',
        'pk_col':      'id',
        'search_cols': ['username', 'password'],
        'columnas':    ['id', 'username', 'password', 'folios_asignac', 'folios_usados'],
    },
}


# ===================== GENERADOR DE FOLIO COMPARTIDO =====================
def generar_folio_edomex() -> str:
    """
    ÚNICO generador de folios 331 — lo usan el panel Y el bot.

    Antes había dos implementaciones distintas:
      · Panel: bloques de 500 con una sola consulta (.in_)  ← rápido
      · Bot:   folio por folio, una consulta de red por candidato  ← lento
    Se quedó la del panel para los dos, y el FOLIO_LOCK evita que se pisen.
    """
    with FOLIO_LOCK:
        try:
            wm = supabase.table("folio_watermark") \
                .select("ultimo_asignado").eq("prefijo", FOLIO_PREFIJO).execute()
            inicio = (wm.data[0]["ultimo_asignado"] + 1) if wm.data else FOLIO_INICIO
        except Exception:
            inicio = FOLIO_INICIO

        BLOQUE = 500
        for _ in range(0, 10_000_000, BLOQUE):
            candidatos = [f"{FOLIO_PREFIJO}{inicio + i}" for i in range(BLOQUE)]
            try:
                resp = supabase.table("folios_registrados") \
                    .select("folio").in_("folio", candidatos).execute()
                ocupados = {r["folio"] for r in (resp.data or [])}
            except Exception as e:
                logger.error(f"[FOLIO] Error bloque: {e}")
                ocupados = set()

            logger.info(f"[FOLIO] bloque {inicio}–{inicio+BLOQUE-1}, ocupados={len(ocupados)}")
            for i, folio in enumerate(candidatos):
                if folio not in ocupados:
                    numero_final = inicio + i
                    try:
                        supabase.table("folio_watermark").upsert({
                            "prefijo": FOLIO_PREFIJO,
                            "ultimo_asignado": numero_final
                        }).execute()
                    except Exception as e:
                        logger.error(f"[WATERMARK] {e}")
                    logger.info(f"[FOLIO] ✅ Asignado: {folio}")
                    return folio
            inicio += BLOQUE

        raise Exception("Sin folio disponible tras 10,000,000 intentos")


def leer_siguiente_folio() -> str:
    """Sólo para mostrar en /health — no reserva nada."""
    try:
        wm = supabase.table("folio_watermark") \
            .select("ultimo_asignado").eq("prefijo", FOLIO_PREFIJO).execute()
        n = (wm.data[0]["ultimo_asignado"] + 1) if wm.data else FOLIO_INICIO
    except Exception:
        n = FOLIO_INICIO
    return f"{FOLIO_PREFIJO}{n}"

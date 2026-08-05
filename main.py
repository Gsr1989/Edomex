"""
EDOMEX — FUSIÓN: panel web (Flask) + bot de Telegram (aiogram) en UN SOLO servicio.

Antes eran dos servicios de Render ($14/mes). Ahora es uno solo ($7/mes).

Cómo funciona:
  · FastAPI atiende /webhook (async) → se lo pasa al Dispatcher de aiogram
  · TODO lo demás (/login, /admin_folios, /consulta/..., /static, etc.) se lo
    pasa a tu app Flask con WSGIMiddleware, sin tocar una sola ruta

Orden IMPORTANTE: las rutas de FastAPI se declaran ANTES del mount("/"),
porque el mount atrapa todo lo que no se haya resuelto antes.

Start command en Render:
    gunicorn main:app -k uvicorn.workers.UvicornWorker -w 1 --timeout 120

El -w 1 es obligatorio: con más workers los timers del bot viven en memorias
separadas y el candado de folio 331 deja de servir.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# Adaptador WSGI→ASGI. a2wsgi es el recomendado; si no está, cae al de Starlette.
try:
    from a2wsgi import WSGIMiddleware
except ImportError:  # pragma: no cover
    from starlette.middleware.wsgi import WSGIMiddleware

import config_edomex as cfg
import bot_edomex
import panel_edomex


# ===================== LIFESPAN =====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg.logger.info("=" * 60)
    cfg.logger.info("[SISTEMA] EDOMEX FUSIONADO v6.0 — panel + bot en un servicio")
    cfg.logger.info("=" * 60)

    # 1) Bot: registra el webhook
    try:
        await bot_edomex.arranque_bot()
    except Exception as e:
        cfg.logger.error(f"[ARRANQUE BOT] {e}")

    # 2) Panel: scheduler de limpieza 48h (antes arrancaba al importar)
    try:
        panel_edomex.iniciar_scheduler()
    except Exception as e:
        cfg.logger.error(f"[ARRANQUE SCHEDULER] {e}")

    try:
        cfg.logger.info(f"[SISTEMA] Siguiente folio: {cfg.leer_siguiente_folio()}")
    except Exception:
        pass
    cfg.logger.info("[SISTEMA] Panel en /  ·  Webhook en /webhook")

    yield

    cfg.logger.info("[CIERRE] Deteniendo servicios...")
    try:
        panel_edomex.detener_scheduler()
    except Exception:
        pass
    try:
        await bot_edomex.cierre_bot()
    except Exception:
        pass
    cfg.logger.info("[CIERRE] Listo")


app = FastAPI(
    lifespan=lifespan,
    title="EDOMEX — Panel + Bot",
    version="6.0",
    docs_url=None,       # sin /docs público
    redoc_url=None,
)


# ===================== WEBHOOK DE TELEGRAM =====================

@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
        await bot_edomex.procesar_update(data)
        return {"ok": True}
    except Exception as e:
        cfg.logger.error(f"[WEBHOOK] {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=200)


# ===================== SALUD / DIAGNÓSTICO =====================
# El health del bot vivía en "/", pero ahí ahora va el login del panel.
# Se movió a /health y /status para no chocar.

@app.get("/health")
async def health():
    return {
        "ok":               True,
        "sistema":          "EDOMEX FUSIONADO v6.0",
        "panel":            "Flask montado en /",
        "bot":              "aiogram vía /webhook",
        "vigencia":         f"{cfg.DIAS_PERMISO} dias",
        "precio":           f"${cfg.PRECIO_PERMISO}",
        "timer_bot":        f"{cfg.HORAS_TIMER_BOT} horas",
        "limpieza_panel":   f"{cfg.HORAS_LIMITE_PAGO} horas",
        "timers_activos":   len(bot_edomex.timers_activos),
        "siguiente_folio":  cfg.leer_siguiente_folio(),
        "nota_folio":       "Panel y bot comparten la serie 331 con candado compartido",
    }


@app.get("/status")
async def status_detail():
    from datetime import datetime
    return {
        "sistema":         "EDOMEX FUSIONADO v6.0",
        "timers_activos":  len(bot_edomex.timers_activos),
        "folios":          bot_edomex.snapshot_timers(),
        "siguiente_folio": cfg.leer_siguiente_folio(),
        "timestamp":       datetime.now().isoformat(),
    }


# ===================== PANEL FLASK (va al final, atrapa todo lo demás) ========
# Cualquier ruta que no sea /webhook, /health o /status llega a Flask:
#   /  /login  /admin  /admin_folios  /consulta/<folio>  /static/...  etc.
app.mount("/", WSGIMiddleware(panel_edomex.flask_app))


if __name__ == "__main__":
    import os
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

"""
EDOMEX — Bot de Telegram (aiogram).

Es TU archivo original. Cambios:
  · Ya NO crea su propia app FastAPI (eso lo hace main.py)
  · Supabase, coordenadas y constantes vienen de config_edomex
  · ⚠️ SE LE AGREGÓ EL CANDADO DE PDF: el bot generaba PDFs SIN lock. Ahora que
    comparte proceso con el panel (que sí generaba con lock), sin candado los
    documentos se corromperían entre sí. Es el fix más importante de la fusión.
  · La generación de folio usa la función compartida (serie 331 con candado)
  · Se agregó snapshot_timers() para que el panel web pueda listarlos
  · El lifespan se partió en arranque_bot() / cierre_bot(), que llama main.py
Los handlers, el FSM y las coordenadas quedaron idénticos.
"""

from datetime import datetime, timedelta
import os
import asyncio
import aiohttp
import qrcode
import fitz
from io import BytesIO

from aiogram import Bot, Dispatcher, types
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command
from aiogram.types import (FSInputFile, ContentType, InlineKeyboardMarkup,
                           InlineKeyboardButton, CallbackQuery)

from config_edomex import (
    supabase, logger, BOT_TOKEN, BASE_URL, URL_CONSULTA_BASE,
    OUTPUT_DIR, PLANTILLA_PDF, PLANTILLA_BUENO, ENTIDAD, BUCKET_NAME,
    PRECIO_PERMISO, DIAS_PERMISO, HORAS_TIMER_BOT,
    PDF_LOCK, COORDS_EDOMEX, FOLIO_PREFIJO,
    generar_folio_edomex as _generar_folio_compartido,
)

PLANTILLA_FLASK = PLANTILLA_BUENO
coords_edomex   = COORDS_EDOMEX

_bot_session = AiohttpSession(timeout=aiohttp.ClientTimeout(total=300))
bot     = Bot(token=BOT_TOKEN, session=_bot_session)
storage = MemoryStorage()
dp      = Dispatcher(storage=storage)

# ------------ TIMER MANAGEMENT ------------
timers_activos       = {}
user_folios          = {}
pending_comprobantes = {}
TOTAL_MINUTOS_TIMER  = HORAS_TIMER_BOT * 60   # 2160

# El candado de PDF es el COMPARTIDO con el panel.
# ⚠️ ANTES DE LA FUSIÓN EL BOT NO TENÍA NINGUNO.
_pdf_generation_lock = PDF_LOCK

_folio_lock = asyncio.Lock()


async def generar_folio_edomex() -> str:
    """Usa el generador compartido (mismo candado que el panel)."""
    async with _folio_lock:
        return await asyncio.to_thread(_generar_folio_compartido)


# ── STORAGE ───────────────────────────────────────────────────────────────────

def subir_pdf_a_storage(ruta_local: str, folio: str) -> str:
    """Sube el PDF al bucket de Supabase Storage. Igual que el panel."""
    try:
        with open(ruta_local, "rb") as f:
            contenido = f.read()
        nombre_archivo = f"{folio}.pdf"
        supabase.storage.from_(BUCKET_NAME).upload(
            path=nombre_archivo,
            file=contenido,
            file_options={"content-type": "application/pdf", "upsert": "true"}
        )
        url = supabase.storage.from_(BUCKET_NAME).get_public_url(nombre_archivo)
        print(f"[STORAGE] Subido: {url}")
        return url
    except Exception as e:
        print(f"[STORAGE] Error {folio}: {e}")
        return ""


# ── TIMERS ────────────────────────────────────────────────────────────────────

async def eliminar_folio_automatico(folio: str):
    try:
        user_id = None
        if folio in timers_activos:
            user_id = timers_activos[folio]["user_id"]
        await asyncio.to_thread(lambda: (
            supabase.table("folios_registrados").delete().eq("folio", folio).execute(),
            supabase.table("borradores_registros").delete().eq("folio", folio).execute(),
        ))
        try:
            supabase.storage.from_(BUCKET_NAME).remove([f"{folio}.pdf"])
        except Exception:
            pass
        if user_id:
            await bot.send_message(user_id,
                f"TIEMPO AGOTADO - EDOMEX\n\n"
                f"El folio {folio} ha sido eliminado por no completar el pago en {HORAS_TIMER_BOT} horas.\n\n"
                f"Para generar otro permiso use /banamex")
        limpiar_timer_folio(folio)
    except Exception as e:
        print(f"Error eliminando folio {folio}: {e}")


async def enviar_recordatorio(folio: str, minutos_restantes: int):
    try:
        if folio not in timers_activos:
            return
        user_id = timers_activos[folio]["user_id"]
        await bot.send_message(user_id,
            f"RECORDATORIO DE PAGO - EDOMEX\n\n"
            f"Folio: {folio}\n"
            f"Tiempo restante: {minutos_restantes} minutos\n"
            f"Monto: ${PRECIO_PERMISO}\n\n"
            f"Envie su comprobante de pago (imagen) para validar el tramite.\n\n"
            f"Para generar otro permiso use /banamex")
    except Exception as e:
        print(f"Error enviando recordatorio para folio {folio}: {e}")


async def iniciar_timer_eliminacion(user_id: int, folio: str, nombre: str = ""):
    async def timer_task():
        print(f"[TIMER] Iniciado folio {folio}, usuario {user_id} ({HORAS_TIMER_BOT}h)")
        await asyncio.sleep(34.5 * 3600)
        if folio not in timers_activos: return
        await enviar_recordatorio(folio, 90)
        await asyncio.sleep(30 * 60)
        if folio not in timers_activos: return
        await enviar_recordatorio(folio, 60)
        await asyncio.sleep(30 * 60)
        if folio not in timers_activos: return
        await enviar_recordatorio(folio, 30)
        await asyncio.sleep(20 * 60)
        if folio not in timers_activos: return
        await enviar_recordatorio(folio, 10)
        await asyncio.sleep(10 * 60)
        if folio in timers_activos:
            print(f"[TIMER] Expirado folio {folio} - eliminando")
            await eliminar_folio_automatico(folio)

    task = asyncio.create_task(timer_task())
    timers_activos[folio] = {
        "task":       task,
        "user_id":    user_id,
        "start_time": datetime.now(),
        "nombre":     nombre,
    }
    user_folios.setdefault(user_id, []).append(folio)
    print(f"[SISTEMA] Timer {HORAS_TIMER_BOT}h iniciado folio {folio} ({nombre})")


def cancelar_timer_folio(folio: str):
    if folio in timers_activos:
        try:
            timers_activos[folio]["task"].cancel()
        except Exception:
            pass
        user_id = timers_activos[folio]["user_id"]
        del timers_activos[folio]
        if user_id in user_folios and folio in user_folios[user_id]:
            user_folios[user_id].remove(folio)
            if not user_folios[user_id]:
                del user_folios[user_id]
        print(f"[SISTEMA] Timer cancelado folio {folio}")
        return True
    return False


def limpiar_timer_folio(folio: str):
    if folio in timers_activos:
        user_id = timers_activos[folio]["user_id"]
        del timers_activos[folio]
        if user_id in user_folios and folio in user_folios[user_id]:
            user_folios[user_id].remove(folio)
            if not user_folios[user_id]:
                del user_folios[user_id]


def obtener_folios_usuario(user_id: int) -> list:
    return user_folios.get(user_id, [])


def snapshot_timers() -> list:
    """
    Lista los timers activos para que el PANEL WEB pueda mostrarlos.
    Sólo es posible porque ahora bot y panel viven en el mismo proceso.
    """
    salida = []
    for folio, info in timers_activos.items():
        mins = max(0, TOTAL_MINUTOS_TIMER - int(
            (datetime.now() - info["start_time"]).total_seconds() / 60))
        salida.append({
            "folio":    folio,
            "nombre":   info.get("nombre", ""),
            "user_id":  info.get("user_id"),
            "restante": f"{mins//60}h {mins%60}min",
            "minutos":  mins,
        })
    salida.sort(key=lambda x: x["minutos"])
    return salida


# ------------ FSM ------------
class PermisoForm(StatesGroup):
    marca  = State()
    linea  = State()
    anio   = State()
    serie  = State()
    motor  = State()
    color  = State()
    nombre = State()


def generar_qr_dinamico_edomex(folio):
    try:
        url = f"{URL_CONSULTA_BASE}/consulta/{folio}"
        qr  = qrcode.QRCode(version=2, error_correction=qrcode.constants.ERROR_CORRECT_M,
                            box_size=4, border=1)
        qr.add_data(url); qr.make(fit=True)
        img_qr = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        return img_qr, url
    except Exception as e:
        print(f"[ERROR QR EDOMEX] {e}")
        return None, None


def generar_pdf_unificado(datos: dict) -> str:
    """
    ⚠️ FIX DE LA FUSIÓN: ahora todo el cuerpo va dentro del candado compartido.
    Antes el bot generaba sin lock; al compartir proceso con el panel eso
    corrompía documentos cuando coincidían dos generaciones.
    """
    with _pdf_generation_lock:
        fol           = datos["folio"]
        fecha_exp_dt  = datos["fecha_exp"]
        fecha_ven_str = datos["fecha_ven"]
        fecha_exp_str = datos["fecha_exp_str"]

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        out = os.path.join(OUTPUT_DIR, f"{fol}.pdf")   # mismo nombre que el panel

        try:
            doc1 = fitz.open(PLANTILLA_PDF)
            pg1  = doc1[0]

            pg1.insert_text(coords_edomex["folio"][:2], fol,
                            fontsize=coords_edomex["folio"][2], color=coords_edomex["folio"][3])
            pg1.insert_text(coords_edomex["fecha_exp"][:2], fecha_exp_str,
                            fontsize=coords_edomex["fecha_exp"][2], color=coords_edomex["fecha_exp"][3])
            pg1.insert_text(coords_edomex["fecha_ven"][:2], fecha_ven_str,
                            fontsize=coords_edomex["fecha_ven"][2], color=coords_edomex["fecha_ven"][3])

            for campo in ["marca", "serie", "linea", "motor", "anio", "color"]:
                if campo in coords_edomex and campo in datos:
                    x, y, s, col = coords_edomex[campo]
                    pg1.insert_text((x, y), str(datos.get(campo, "")), fontsize=s, color=col)

            pg1.insert_text(coords_edomex["nombre"][:2], str(datos.get("nombre", "")),
                            fontsize=coords_edomex["nombre"][2], color=coords_edomex["nombre"][3])

            img_qr, _ = generar_qr_dinamico_edomex(fol)
            if img_qr:
                buf = BytesIO(); img_qr.save(buf, format="PNG"); buf.seek(0)
                qr_pix = fitz.Pixmap(buf.read())
                pg1.insert_image(fitz.Rect(493, 35, 493+82, 35+82), pixmap=qr_pix, overlay=True)

            doc2 = fitz.open(PLANTILLA_FLASK)
            pg2  = doc2[0]
            pg2.insert_text((80,  142), fecha_exp_dt.strftime("%d/%m/%Y"), fontsize=15, fontname="helv", color=(0,0,0))
            pg2.insert_text((218, 142), fecha_exp_dt.strftime("%d/%m/%Y"), fontsize=15, fontname="helv", color=(0,0,0))
            pg2.insert_text((182, 283), fecha_exp_dt.strftime("%d/%m/%Y"), fontsize=9,  fontname="helv", color=(0,0,0))
            pg2.insert_text((130, 435), fecha_exp_dt.strftime("%d/%m/%Y"), fontsize=20, fontname="helv", color=(0,0,0))
            pg2.insert_text((162, 185), str(datos["serie"]),               fontsize=9,  fontname="helv", color=(0,0,0))

            doc_final = fitz.open()
            doc_final.insert_pdf(doc1)
            doc_final.insert_pdf(doc2)
            doc_final.save(out)
            doc_final.close(); doc1.close(); doc2.close()
            print(f"[PDF EDOMEX] Generado: {out}")

        except Exception as e:
            print(f"[ERROR] PDF EDOMEX: {e}")
            doc_fallback = fitz.open()
            doc_fallback.new_page().insert_text((50, 50), f"ERROR - Folio: {fol}", fontsize=12)
            doc_fallback.save(out); doc_fallback.close()

        return out


# ── BACKGROUND TASK ───────────────────────────────────────────────────────────

async def _generar_y_enviar_background(chat_id: int, datos: dict, user_id: int):
    nombre      = datos["nombre"]
    hoy         = datos["fecha_exp"]
    fecha_ven   = hoy + timedelta(days=DIAS_PERMISO)
    folio_final = datos["folio"]

    try:
        pdf_path = await asyncio.to_thread(generar_pdf_unificado, datos)
        pdf_url  = await asyncio.to_thread(subir_pdf_a_storage, pdf_path, folio_final)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Validar Admin",  callback_data=f"validar_{folio_final}"),
            InlineKeyboardButton(text="Detener Timer",  callback_data=f"detener_{folio_final}")
        ]])

        await bot.send_document(
            chat_id,
            FSInputFile(pdf_path),
            caption=(
                f"PERMISO DE CIRCULACION - EDOMEX\n"
                f"Folio: {folio_final}\n"
                f"Titular: {nombre}\n"
                f"Vigencia: {DIAS_PERMISO} dias\n\n"
                f"TIMER ACTIVO ({HORAS_TIMER_BOT} horas)"
            ),
            reply_markup=keyboard
        )

        def _insert(folio_usar: str):
            supabase.table("folios_registrados").insert({
                "folio":             folio_usar,
                "marca":             datos["marca"],
                "linea":             datos["linea"],
                "anio":              datos["anio"],
                "numero_serie":      datos["serie"],
                "numero_motor":      datos["motor"],
                "color":             datos["color"],
                "nombre":            nombre,
                "fecha_expedicion":  hoy.date().isoformat(),
                "fecha_vencimiento": fecha_ven.date().isoformat(),
                "entidad":           ENTIDAD,
                "estado":            "ACTIVO",
                "estado_pago":       "PENDIENTE_PAGO",
                "creado_por":        f"BOT_TG_{datos.get('username', 'unknown')}",
                "user_id":           user_id,
                "pdf_url":           pdf_url,
                "folio_origen":      None,
            }).execute()

            supabase.table("borradores_registros").insert({
                "folio":             folio_usar,
                "entidad":           "EDOMEX",
                "numero_serie":      datos["serie"],
                "marca":             datos["marca"],
                "linea":             datos["linea"],
                "numero_motor":      datos["motor"],
                "anio":              datos["anio"],
                "color":             datos["color"],
                "fecha_expedicion":  hoy.isoformat(),
                "fecha_vencimiento": fecha_ven.isoformat(),
                "contribuyente":     nombre,
                "estado":            "PENDIENTE",
                "user_id":           user_id
            }).execute()

        for _ in range(20):
            try:
                await asyncio.to_thread(_insert, folio_final)
                print(f"[DB] Insertado folio {folio_final}")
                break
            except Exception as e:
                em = str(e).lower()
                if any(k in em for k in ("duplicate", "unique", "23505")):
                    print(f"[DB] Folio {folio_final} duplicado — obteniendo nuevo...")
                    folio_final = await generar_folio_edomex()
                else:
                    print(f"[DB ERROR] {e}"); break

        await iniciar_timer_eliminacion(user_id, folio_final, nombre)

        await bot.send_message(user_id,
            f"INSTRUCCIONES DE PAGO\n\n"
            f"Folio: {folio_final}\n"
            f"Monto: ${PRECIO_PERMISO}\n"
            f"Tiempo limite: {HORAS_TIMER_BOT} horas\n\n"
            f"TRANSFERENCIA:\n"
            f"Banco: AZTECA\n"
            f"Titular: LIZBETH LAZCANO MOSCO\n"
            f"Cuenta: 127180013037579543\n"
            f"Concepto: Permiso {folio_final}\n\n"
            f"OXXO:\n"
            f"Referencia: 2242170180385581\n"
            f"Titular: LIZBETH LAZCANO MOSCO\n"
            f"Monto: ${PRECIO_PERMISO}\n\n"
            f"Envia la foto del comprobante para validar.\n"
            f"Si no pagas en {HORAS_TIMER_BOT} horas el folio se elimina automaticamente.\n\n"
            f"Para generar otro permiso use /banamex")

    except Exception as e:
        print(f"[ERROR] background folio {folio_final}: {e}")
        try:
            await bot.send_message(user_id,
                f"Error generando documentacion: {e}\n\nUse /banamex para reintentar.")
        except Exception:
            pass


# ── VALIDAR PAGO (bot + panel comparten lógica) ───────────────────────────────

async def _validar_folio_db(folio: str):
    """Marca el folio como VALIDADO. Mismo campo que usa el panel."""
    now = datetime.now().isoformat()
    await asyncio.to_thread(lambda: (
        supabase.table("folios_registrados").update({
            "estado_pago":       "VALIDADO",
            "fecha_comprobante": now
        }).eq("folio", folio).execute(),
        supabase.table("borradores_registros").update({
            "estado":            "VALIDADO_ADMIN",
            "fecha_comprobante": now
        }).eq("folio", folio).execute()
    ))


# ------------ HANDLERS ------------

@dp.message(Command("start"))
async def start_cmd(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "SISTEMA DIGITAL DEL ESTADO DE MEXICO\n\n"
        f"Costo: ${PRECIO_PERMISO}\n"
        f"Tiempo limite: {HORAS_TIMER_BOT} horas\n\n"
        "Su folio sera eliminado automaticamente si no realiza el pago dentro del tiempo limite"
    )


@dp.message(Command("banamex"))
async def banamex_cmd(message: types.Message, state: FSMContext):
    await state.clear()
    mis_folios = [f for f in timers_activos
                  if timers_activos[f].get("user_id") == message.from_user.id]

    if mis_folios:
        texto   = "FOLIOS ACTIVOS CON TIMER\n" + "─" * 28 + "\n\n"
        botones = []
        for f in mis_folios:
            info   = timers_activos[f]
            nombre = info.get("nombre", "Sin nombre")
            mins   = max(0, TOTAL_MINUTOS_TIMER - int((datetime.now() - info["start_time"]).total_seconds() / 60))
            texto += f"Folio: {f}\n{nombre}\n{mins//60}h {mins%60}min restantes\n\n"
            botones.append([InlineKeyboardButton(
                text=f"Detener timer {f}", callback_data=f"detener_{f}")])
        await message.answer(texto.strip(),
                             reply_markup=InlineKeyboardMarkup(inline_keyboard=botones))
        await message.answer(
            f"Para NUEVO permiso escribe la MARCA del vehiculo:\n\nCosto: ${PRECIO_PERMISO} | Plazo: {HORAS_TIMER_BOT}h")
    else:
        await message.answer(
            f"NUEVO PERMISO - EDOMEX\n\n"
            f"Costo: ${PRECIO_PERMISO}\n"
            f"Plazo de pago: {HORAS_TIMER_BOT} horas\n\n"
            f"Primer paso: MARCA del vehiculo:")
    await state.set_state(PermisoForm.marca)


@dp.message(PermisoForm.marca)
async def get_marca(message: types.Message, state: FSMContext):
    await state.update_data(marca=message.text.strip().upper())
    await message.answer("LINEA/MODELO del vehiculo:")
    await state.set_state(PermisoForm.linea)


@dp.message(PermisoForm.linea)
async def get_linea(message: types.Message, state: FSMContext):
    await state.update_data(linea=message.text.strip().upper())
    await message.answer("ANO del vehiculo (4 digitos):")
    await state.set_state(PermisoForm.anio)


@dp.message(PermisoForm.anio)
async def get_anio(message: types.Message, state: FSMContext):
    anio = message.text.strip()
    if not anio.isdigit() or len(anio) != 4:
        await message.answer("Formato invalido. Use 4 digitos (ej. 2021):")
        return
    await state.update_data(anio=anio)
    await message.answer("NUMERO DE SERIE:")
    await state.set_state(PermisoForm.serie)


@dp.message(PermisoForm.serie)
async def get_serie(message: types.Message, state: FSMContext):
    await state.update_data(serie=message.text.strip().upper())
    await message.answer("NUMERO DE MOTOR:")
    await state.set_state(PermisoForm.motor)


@dp.message(PermisoForm.motor)
async def get_motor(message: types.Message, state: FSMContext):
    await state.update_data(motor=message.text.strip().upper())
    await message.answer("COLOR del vehiculo:")
    await state.set_state(PermisoForm.color)


@dp.message(PermisoForm.color)
async def get_color(message: types.Message, state: FSMContext):
    await state.update_data(color=message.text.strip().upper())
    await message.answer("NOMBRE COMPLETO del propietario:")
    await state.set_state(PermisoForm.nombre)


@dp.message(PermisoForm.nombre)
async def get_nombre(message: types.Message, state: FSMContext):
    datos             = await state.get_data()
    nombre            = message.text.strip().upper()
    datos["nombre"]   = nombre
    datos["username"] = message.from_user.username or "Sin username"
    datos["folio"]    = await generar_folio_edomex()

    hoy       = datetime.now()
    fecha_ven = hoy + timedelta(days=DIAS_PERMISO)

    datos["fecha_exp"]     = hoy
    datos["fecha_exp_str"] = hoy.strftime("%d/%m/%Y")
    datos["fecha_ven"]     = fecha_ven.strftime("%d/%m/%Y")

    await state.clear()

    await message.answer(
        f"Generando documentacion...\n"
        f"Folio: {datos['folio']}\n"
        f"Titular: {nombre}"
    )

    asyncio.create_task(
        _generar_y_enviar_background(message.chat.id, datos, message.from_user.id)
    )


# ------------ CALLBACKS ------------

@dp.callback_query(lambda c: c.data and c.data.startswith("validar_"))
async def callback_validar_admin(callback: CallbackQuery):
    folio = callback.data.replace("validar_", "")
    if not folio.startswith(FOLIO_PREFIJO):
        await callback.answer("Folio invalido", show_alert=True); return
    if folio in timers_activos:
        uid    = timers_activos[folio]["user_id"]
        nombre = timers_activos[folio].get("nombre", "")
        cancelar_timer_folio(folio)
        try:
            await _validar_folio_db(folio)
        except Exception as e:
            print(f"Error BD validar {folio}: {e}")
        await callback.answer("Folio validado", show_alert=True)
        await callback.message.edit_reply_markup(reply_markup=None)
        try:
            await bot.send_message(uid,
                f"PAGO VALIDADO - EDOMEX\n"
                f"Folio: {folio}\nTitular: {nombre}\n"
                f"Tu permiso esta activo para circular.\n\n"
                f"Para generar otro permiso use /banamex")
        except Exception as e:
            print(f"Error notificando usuario {uid}: {e}")
    else:
        await callback.answer("Folio no encontrado en timers activos", show_alert=True)


@dp.callback_query(lambda c: c.data and c.data.startswith("detener_"))
async def callback_detener_timer(callback: CallbackQuery):
    folio = callback.data.replace("detener_", "")
    if folio in timers_activos:
        nombre = timers_activos[folio].get("nombre", "")
        cancelar_timer_folio(folio)
        try:
            await asyncio.to_thread(lambda: supabase.table("folios_registrados").update(
                {"estado": "TIMER_DETENIDO"}
            ).eq("folio", folio).execute())
        except Exception as e:
            print(f"Error BD detener {folio}: {e}")
        await callback.answer("Timer detenido", show_alert=True)
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            f"TIMER DETENIDO\nFolio: {folio}\nTitular: {nombre}\n\n"
            f"El folio ya NO se eliminara automaticamente.\n\n"
            f"Para generar otro permiso use /banamex")
    else:
        await callback.answer("Timer ya no esta activo", show_alert=True)


@dp.message(lambda message: message.text and message.text.strip().upper().startswith("SERO"))
async def codigo_admin(message: types.Message):
    texto = message.text.strip().upper()
    if len(texto) <= 4:
        await message.answer(
            f"Formato: SERO[folio]  Ejemplo: SERO{FOLIO_PREFIJO}2\n\nPara generar otro permiso use /banamex"); return
    folio_admin = texto[4:]
    if not folio_admin.startswith(FOLIO_PREFIJO):
        await message.answer(
            f"FOLIO INVALIDO\nEl folio {folio_admin} no es EDOMEX (debe comenzar con {FOLIO_PREFIJO})\n\n"
            f"Para generar otro permiso use /banamex"); return
    if folio_admin in timers_activos:
        uid    = timers_activos[folio_admin]["user_id"]
        nombre = timers_activos[folio_admin].get("nombre", "")
        cancelar_timer_folio(folio_admin)
        try:
            await _validar_folio_db(folio_admin)
        except Exception as e:
            print(f"Error BD SERO {folio_admin}: {e}")
        await message.answer(
            f"VALIDACION ADMINISTRATIVA OK\nFolio: {folio_admin}\nTitular: {nombre}\n"
            f"Timer cancelado.\n\nPara generar otro permiso use /banamex")
        try:
            await bot.send_message(uid,
                f"PAGO VALIDADO - EDOMEX\n"
                f"Folio: {folio_admin}\nTu permiso esta activo.\n\n"
                f"Para generar otro permiso use /banamex")
        except Exception as e:
            print(f"Error notificando usuario {uid}: {e}")
    else:
        await message.answer(
            f"FOLIO NO LOCALIZADO EN TIMERS ACTIVOS\nFolio: {folio_admin}\n\n"
            f"Para generar otro permiso use /banamex")


@dp.message(lambda message: message.content_type == ContentType.PHOTO)
async def recibir_comprobante(message: types.Message):
    try:
        uid    = message.from_user.id
        folios = obtener_folios_usuario(uid)
        if not folios:
            await message.answer(
                "No hay tramites pendientes de pago.\n\n"
                "Para generar otro permiso use /banamex"); return
        if len(folios) > 1:
            lista = '\n'.join([f"- {f}" for f in folios])
            pending_comprobantes[uid] = "waiting_folio"
            await message.answer(
                f"Tienes varios folios activos:\n\n{lista}\n\n"
                f"Responde con el NUMERO DE FOLIO al que corresponde este comprobante.\n\n"
                f"Para generar otro permiso use /banamex"); return
        folio = folios[0]
        cancelar_timer_folio(folio)
        try:
            now = datetime.now().isoformat()
            await asyncio.to_thread(lambda: (
                supabase.table("folios_registrados").update(
                    {"estado": "COMPROBANTE_ENVIADO", "fecha_comprobante": now}
                ).eq("folio", folio).execute(),
                supabase.table("borradores_registros").update(
                    {"estado": "COMPROBANTE_ENVIADO", "fecha_comprobante": now}
                ).eq("folio", folio).execute()
            ))
        except Exception as e:
            print(f"Error actualizando estado comprobante: {e}")
        await message.answer(
            f"Comprobante recibido.\nFolio: {folio}\nTimer detenido.\n\n"
            f"Para generar otro permiso use /banamex")
    except Exception as e:
        print(f"[ERROR] recibir_comprobante: {e}")
        await message.answer(
            f"Error procesando el comprobante.\n\nPara generar otro permiso use /banamex")


@dp.message(lambda message: message.from_user.id in pending_comprobantes
            and pending_comprobantes[message.from_user.id] == "waiting_folio")
async def especificar_folio_comprobante(message: types.Message):
    try:
        uid                = message.from_user.id
        folio_especificado = message.text.strip().upper()
        folios_usuario     = obtener_folios_usuario(uid)
        if folio_especificado not in folios_usuario:
            await message.answer(
                "Ese folio no esta entre tus expedientes activos.\n\n"
                "Para generar otro permiso use /banamex"); return
        cancelar_timer_folio(folio_especificado)
        del pending_comprobantes[uid]
        try:
            now = datetime.now().isoformat()
            await asyncio.to_thread(lambda: (
                supabase.table("folios_registrados").update(
                    {"estado": "COMPROBANTE_ENVIADO", "fecha_comprobante": now}
                ).eq("folio", folio_especificado).execute(),
                supabase.table("borradores_registros").update(
                    {"estado": "COMPROBANTE_ENVIADO", "fecha_comprobante": now}
                ).eq("folio", folio_especificado).execute()
            ))
        except Exception as e:
            print(f"Error actualizando estado: {e}")
        await message.answer(
            f"Comprobante asociado.\nFolio: {folio_especificado}\nTimer detenido.\n\n"
            f"Para generar otro permiso use /banamex")
    except Exception as e:
        print(f"[ERROR] especificar_folio_comprobante: {e}")
        if message.from_user.id in pending_comprobantes:
            del pending_comprobantes[message.from_user.id]
        await message.answer(
            f"Error procesando el folio.\n\nPara generar otro permiso use /banamex")


@dp.message(Command("folios"))
async def ver_folios_activos(message: types.Message):
    try:
        uid    = message.from_user.id
        folios = obtener_folios_usuario(uid)
        if not folios:
            await message.answer(
                "NO HAY FOLIOS ACTIVOS\n\nPara generar otro permiso use /banamex"); return
        lista   = []
        botones = []
        for folio in folios:
            if folio in timers_activos:
                info   = timers_activos[folio]
                nombre = info.get("nombre", "Sin nombre")
                mins   = max(0, TOTAL_MINUTOS_TIMER - int(
                    (datetime.now() - info["start_time"]).total_seconds() / 60))
                lista.append(f"- {folio} — {nombre}\n  {mins//60}h {mins%60}min restantes")
            else:
                lista.append(f"- {folio} (sin timer)")
            botones.append([InlineKeyboardButton(
                text=f"Detener timer {folio}", callback_data=f"detener_{folio}")])
        await message.answer(
            f"FOLIOS EDOMEX ACTIVOS ({len(folios)})\n\n" + '\n\n'.join(lista) +
            f"\n\nPara generar otro permiso use /banamex",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=botones))
    except Exception as e:
        print(f"[ERROR] ver_folios_activos: {e}")
        await message.answer(f"Error consultando expedientes.\n\nPara generar otro permiso use /banamex")


@dp.message(lambda message: message.text and any(p in message.text.lower() for p in
    ['costo','precio','cuanto','cuánto','deposito','depósito','pago','valor','monto']))
async def responder_costo(message: types.Message):
    await message.answer(
        f"INFORMACION DE COSTO\n\n"
        f"El costo del permiso es ${PRECIO_PERMISO}.\n\n"
        "Para generar otro permiso use /banamex")


@dp.message()
async def fallback(message: types.Message):
    await message.answer("Sistema Digital EDOMEX.")


# ── ARRANQUE / CIERRE (los llama main.py) ─────────────────────────────────────

_keep_task = None


async def _keep_alive():
    while True:
        await asyncio.sleep(600)
        print(f"[HEARTBEAT] EDOMEX activo — timers: {len(timers_activos)}")


async def arranque_bot():
    """Registra el webhook. Lo llama el lifespan de main.py."""
    global _keep_task
    if not BOT_TOKEN:
        print("[BOT] Sin BOT_TOKEN — el bot queda inactivo")
        return
    await bot.delete_webhook(drop_pending_updates=True)
    if BASE_URL:
        webhook_url = f"{BASE_URL}/webhook"
        await bot.set_webhook(webhook_url, allowed_updates=["message", "callback_query"])
        print(f"[WEBHOOK] Configurado: {webhook_url}")
    else:
        print("[BOT] Sin BASE_URL — no se registró webhook")
    _keep_task = asyncio.create_task(_keep_alive())


async def cierre_bot():
    global _keep_task
    if _keep_task:
        _keep_task.cancel()
        try:
            await _keep_task
        except asyncio.CancelledError:
            pass
        _keep_task = None
    try:
        await bot.session.close()
    except Exception:
        pass


async def procesar_update(data: dict):
    """Procesa un update de Telegram. Lo llama la ruta /webhook de main.py."""
    update = types.Update(**data)
    await dp.feed_webhook_update(bot, update)

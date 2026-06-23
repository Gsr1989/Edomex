from flask import Flask, render_template, request, redirect, url_for, flash, session, \
    send_file, abort, jsonify, Response
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from supabase import create_client, Client
import fitz
import os
import qrcode
from PIL import Image
from io import BytesIO, StringIO
import time
import re
import threading
import logging
import sys

from werkzeug.middleware.proxy_fix import ProxyFix

# ===================== LOGGING =====================
sys.dont_write_bytecode = True
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ===================== ZONA HORARIA =====================
TZ_CDMX = ZoneInfo("America/Mexico_City")

def now_cdmx() -> datetime:
    return datetime.now(TZ_CDMX)

def today_cdmx() -> date:
    return now_cdmx().date()

def parse_date_any(value) -> date:
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

# ===================== FLASK CONFIG =====================
app = Flask(__name__)
app.secret_key = 'clave_muy_segura_123456'
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=2, x_proto=2, x_host=2, x_prefix=1)
app.config.update(
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=False,
    SESSION_COOKIE_HTTPONLY=True,
    MAX_CONTENT_LENGTH=32 * 1024 * 1024,
    SEND_FILE_MAX_AGE_DEFAULT=0,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=24)
)

# ===================== SUPABASE =====================
SUPABASE_URL = "https://xsagwqepoljfsogusubw.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InhzYWd3cWVwb2xqZnNvZ3VzdWJ3Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDM5NjM3NTUsImV4cCI6MjA1OTUzOTc1NX0.NUixULn0m2o49At8j6X58UqbXre2O2_JStqzls_8Gws"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ===================== CONFIG GENERAL =====================
OUTPUT_DIR        = "documentos"
PLANTILLA_PDF     = "edomex_plantilla_alta_res.pdf"
PLANTILLA_BUENO   = "labuena3.0.pdf"
URL_CONSULTA_BASE = "https://sfpyaedomexicoconsultapermisodigital.onrender.com"
ENTIDAD           = "edomex"
DIAS_PERMISO      = 30
HORAS_LIMITE_PAGO = 48
BUCKET_NAME       = "permisos-edomex"
PAGE_SIZE         = 100

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ── LOCK GLOBAL PDF ───────────────────────────────────────────────────────────
_pdf_generation_lock = threading.Lock()

# ===================== COORDENADAS PDF EDOMEX =====================
coords_edomex = {
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

# ===================== TABLAS DISPONIBLES =====================
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

# ===================== FOLIOS EDOMEX =====================
PREFIJO_EDOMEX = "331"

def generar_folio_automatico_edomex() -> str:
    try:
        wm = supabase.table("folio_watermark") \
            .select("ultimo_asignado").eq("prefijo", PREFIJO_EDOMEX).execute()
        inicio = (wm.data[0]["ultimo_asignado"] + 1) if wm.data else 2
    except Exception:
        inicio = 2

    BLOQUE = 500
    for _ in range(0, 10_000_000, BLOQUE):
        candidatos = [f"{PREFIJO_EDOMEX}{inicio + i}" for i in range(BLOQUE)]
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
                        "prefijo": PREFIJO_EDOMEX,
                        "ultimo_asignado": numero_final
                    }).execute()
                except Exception as e:
                    logger.error(f"[WATERMARK] {e}")
                logger.info(f"[FOLIO] ✅ Asignado: {folio}")
                return folio
        inicio += BLOQUE

    raise Exception("Sin folio disponible tras 10,000,000 intentos")


def guardar_folio_con_reintento(datos, username):
    fexp_date = parse_date_any(datos["fecha_exp"])
    fven_date = parse_date_any(datos["fecha_ven"])

    def _row(folio):
        return {
            "folio":             folio,
            "marca":             str(datos["marca"]),
            "linea":             str(datos["linea"]),
            "anio":              str(datos["anio"]),
            "numero_serie":      str(datos["serie"]),
            "numero_motor":      str(datos["motor"]),
            "color":             str(datos.get("color", "BLANCO")),
            "nombre":            str(datos.get("nombre", "SIN NOMBRE")),
            "fecha_expedicion":  fexp_date.isoformat(),
            "fecha_vencimiento": fven_date.isoformat(),
            "entidad":           ENTIDAD,
            "estado":            "ACTIVO",
            "creado_por":        username,
            "estado_pago":       datos.get("estado_pago", "VALIDADO"),
            "folio_origen":      datos.get("folio_origen", None),
            "user_id":           datos.get("user_id", None),
        }

    # MANUAL
    if datos.get("folio") and str(datos["folio"]).strip():
        fm = str(datos["folio"]).strip()
        try:
            supabase.table("folios_registrados").insert(_row(fm)).execute()
            datos["folio"] = fm
            logger.info(f"[DB] ✅ Folio MANUAL {fm}")
            return True
        except Exception as e:
            em = str(e).lower()
            if any(k in em for k in ("duplicate", "unique", "23505")):
                logger.error(f"[ERROR] Folio {fm} YA EXISTE")
            else:
                logger.error(f"[ERROR BD] {e}")
            return False

    # AUTO con bloque
    for intento in range(10_000_000):
        try:
            c = generar_folio_automatico_edomex()
        except Exception as e:
            logger.error(f"[ERROR] {e}")
            return False
        try:
            supabase.table("folios_registrados").insert(_row(c)).execute()
            datos["folio"] = c
            logger.info(f"[DB] ✅ Folio AUTO {c} (intento {intento+1})")
            return True
        except Exception as e:
            em = str(e).lower()
            if any(k in em for k in ("duplicate", "unique", "23505")):
                continue
            logger.error(f"[ERROR BD] {e}")
            return False

    logger.error("[ERROR] Sin folio tras 10,000,000 intentos")
    return False

# ===================== SUPABASE STORAGE =====================

def subir_pdf_a_storage(ruta_local: str, folio: str) -> str:
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
        logger.info(f"[STORAGE] Subido: {url}")
        return url
    except Exception as e:
        logger.error(f"[STORAGE] Error {folio}: {e}")
        return ""

# ===================== TIMER DE PAGO =====================
def get_timer_info(usuario: dict):
    if usuario.get("pagado"):
        return None
    creado_en = usuario.get("created_at")
    if not creado_en:
        return None
    try:
        if isinstance(creado_en, str):
            creado_en = datetime.fromisoformat(
                creado_en.replace("Z", "+00:00")).replace(tzinfo=None)
        limite    = creado_en + timedelta(hours=2)
        restante  = (datetime.utcnow() - creado_en).total_seconds()
        secs_left = max(0, int(7200 - restante))
        return {
            "limite_iso":         limite.isoformat(),
            "segundos_restantes": secs_left,
            "vencido":            secs_left <= 0
        }
    except Exception as e:
        logger.error(f"[TIMER] {e}")
        return None

# ===================== QR =====================
def generar_qr_dinamico(folio):
    try:
        url = f"{URL_CONSULTA_BASE}/consulta/{folio}"
        qr  = qrcode.QRCode(version=2,
                             error_correction=qrcode.constants.ERROR_CORRECT_M,
                             box_size=4, border=1)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        logger.info(f"[QR] ✅ {folio}")
        return img, url
    except Exception as e:
        logger.error(f"[ERROR QR] {e}")
        return None, None

# ===================== PDF =====================
def generar_pdf_unificado(datos: dict) -> str:
    with _pdf_generation_lock:
        fol          = datos["folio"]
        fecha_exp_dt = datos["fecha_exp"]
        fecha_ven_dt = datos["fecha_ven"]

        if isinstance(fecha_exp_dt, date) and not isinstance(fecha_exp_dt, datetime):
            fecha_exp_dt = datetime.combine(fecha_exp_dt, datetime.min.time()).replace(tzinfo=TZ_CDMX)
        elif fecha_exp_dt.tzinfo is None:
            fecha_exp_dt = fecha_exp_dt.replace(tzinfo=TZ_CDMX)
        else:
            fecha_exp_dt = fecha_exp_dt.astimezone(TZ_CDMX)

        if isinstance(fecha_ven_dt, str):
            fecha_ven_str = fecha_ven_dt
        else:
            if isinstance(fecha_ven_dt, date) and not isinstance(fecha_ven_dt, datetime):
                fecha_ven_dt = datetime.combine(fecha_ven_dt, datetime.min.time()).replace(tzinfo=TZ_CDMX)
            elif fecha_ven_dt.tzinfo is None:
                fecha_ven_dt = fecha_ven_dt.replace(tzinfo=TZ_CDMX)
            else:
                fecha_ven_dt = fecha_ven_dt.astimezone(TZ_CDMX)
            fecha_ven_str = fecha_ven_dt.strftime("%d/%m/%Y")

        out = os.path.join(OUTPUT_DIR, f"{fol}.pdf")

        try:
            doc1 = fitz.open(PLANTILLA_PDF)
            pg1  = doc1[0]

            pg1.insert_text(coords_edomex["folio"][:2], fol,
                            fontsize=coords_edomex["folio"][2],
                            color=coords_edomex["folio"][3])
            pg1.insert_text(coords_edomex["fecha_exp"][:2], fecha_exp_dt.strftime("%d/%m/%Y"),
                            fontsize=coords_edomex["fecha_exp"][2],
                            color=coords_edomex["fecha_exp"][3])
            pg1.insert_text(coords_edomex["fecha_ven"][:2], fecha_ven_str,
                            fontsize=coords_edomex["fecha_ven"][2],
                            color=coords_edomex["fecha_ven"][3])

            for campo in ["marca", "serie", "linea", "motor", "anio", "color"]:
                if campo in coords_edomex and campo in datos:
                    x, y, s, col = coords_edomex[campo]
                    pg1.insert_text((x, y), str(datos.get(campo, "")), fontsize=s, color=col)

            pg1.insert_text(coords_edomex["nombre"][:2], str(datos.get("nombre", "")),
                            fontsize=coords_edomex["nombre"][2],
                            color=coords_edomex["nombre"][3])

            img_qr, _ = generar_qr_dinamico(fol)
            if img_qr:
                buf = BytesIO()
                img_qr.save(buf, format="PNG")
                buf.seek(0)
                qr_pix = fitz.Pixmap(buf.read())
                pg1.insert_image(fitz.Rect(493, 35, 493+82, 35+82), pixmap=qr_pix, overlay=True)

            doc2 = fitz.open(PLANTILLA_BUENO)
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
            doc_final.close()
            doc1.close()
            doc2.close()
            logger.info(f"[PDF] ✅ {out}")

            # Sube a Storage y guarda url
            url = subir_pdf_a_storage(out, fol)
            if url:
                try:
                    supabase.table("folios_registrados") \
                        .update({"pdf_url": url}).eq("folio", fol).execute()
                except Exception as e:
                    logger.error(f"[WARN] No se pudo guardar pdf_url: {e}")

        except Exception as e:
            logger.error(f"[ERROR PDF] {e}")
            doc_fallback = fitz.open()
            page = doc_fallback.new_page()
            page.insert_text((50, 50), f"ERROR - Folio: {fol}", fontsize=12)
            doc_fallback.save(out)
            doc_fallback.close()

        return out


def generar_pdf_en_background(datos: dict):
    generar_pdf_unificado(datos)

# ===================== ARMAR RESULTADO =====================

def _armar_resultado_edomex(r: dict, folio: str) -> dict:
    """
    PARCHE ANTI-ROBO:
    puede_renovar = True solo si VENCIDO + no es lote + NO tiene renovación PENDIENTE_PAGO.
    ya_renovado   = True si ya existe folio_origen=folio con PENDIENTE_PAGO.
    Evaluación server-side — aplica desde cualquier cel/browser.
    """
    fe = parse_date_any(r.get('fecha_expedicion'))
    fv = parse_date_any(r.get('fecha_vencimiento'))
    hoy    = today_cdmx()
    estado = "VIGENTE" if hoy <= fv else "VENCIDO"

    es_de_lote = r.get('user_id') is not None

    ya_renovado = False
    if estado == "VENCIDO" and not es_de_lote:
        try:
            chk = supabase.table("folios_registrados") \
                .select("folio") \
                .eq("folio_origen", folio) \
                .eq("estado_pago", "PENDIENTE_PAGO") \
                .limit(1).execute()
            ya_renovado = bool(chk.data)
        except Exception as e:
            logger.error(f"[RENOVAR CHECK] {e}")

    puede_renovar = (estado == "VENCIDO") and not es_de_lote and not ya_renovado

    return {
        "estado":            estado,
        "color":             "verde" if estado == "VIGENTE" else "cafe",
        "folio":             folio,
        "folio_actual":      folio,
        "fecha_expedicion":  fe.strftime('%d/%m/%Y'),
        "fecha_vencimiento": fv.strftime('%d/%m/%Y'),
        "marca":             r.get('marca', ''),
        "linea":             r.get('linea', ''),
        "año":               r.get('anio', ''),
        "numero_serie":      r.get('numero_serie', ''),
        "numero_motor":      r.get('numero_motor', ''),
        "entidad":           r.get('entidad', ENTIDAD),
        "puede_renovar":     puede_renovar,
        "ya_renovado":       ya_renovado,
    }

# ===================== RUTAS =====================

@app.route('/')
def inicio():
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        if username == 'Serg890105tm3' and password == 'Serg890105tm3':
            session['admin']    = True
            session['username'] = 'Serg890105tm3'
            return redirect(url_for('admin'))

        resp = supabase.table("verificaciondigitalcdmx") \
            .select("*").eq("username", username).eq("password", password).execute()
        if resp.data:
            u = resp.data[0]
            session['user_id']  = u.get('id')
            session['username'] = u['username']
            session['admin']    = False
            return redirect(url_for('registro_usuario'))

        flash('Usuario o contraseña incorrectos', 'error')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ─── ADMIN ───────────────────────────────────────────────────────────────────

@app.route('/admin')
def admin():
    if not session.get('admin'):
        return redirect(url_for('login'))
    return render_template('panel.html')


@app.route('/crear_usuario', methods=['GET', 'POST'])
def crear_usuario():
    if not session.get('admin'):
        return redirect(url_for('login'))
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password'].strip()
        folios   = int(request.form['folios'])
        existe   = supabase.table("verificaciondigitalcdmx") \
            .select("id").eq("username", username).limit(1).execute()
        if existe.data:
            flash('Error: el usuario ya existe.', 'error')
        else:
            supabase.table("verificaciondigitalcdmx").insert({
                "username":       username,
                "password":       password,
                "folios_asignac": folios,
                "folios_usados":  0,
                "pagado":         False
            }).execute()
            flash('Usuario creado.', 'success')
    return render_template('crear_usuario.html')


@app.route('/registro_admin', methods=['GET', 'POST'])
def registro_admin():
    if not session.get('admin'):
        return redirect(url_for('login'))

    if request.method == 'POST':
        folio_manual = request.form.get('folio', '').strip()
        marca        = request.form.get('marca', '').strip().upper()
        linea        = request.form.get('linea', '').strip().upper()
        anio         = request.form.get('anio', '').strip()
        serie        = request.form.get('serie', '').strip().upper()
        motor        = request.form.get('motor', '').strip().upper()
        color        = request.form.get('color', '').strip().upper() or 'BLANCO'
        nombre       = request.form.get('nombre', '').strip().upper() or 'SIN NOMBRE'
        fecha_str    = request.form.get('fecha_inicio', '').strip()

        if not all([marca, linea, anio, serie, motor, fecha_str]):
            flash("❌ Faltan campos.", "error")
            return redirect(url_for('registro_admin'))

        try:
            fecha_inicio = datetime.strptime(fecha_str, '%Y-%m-%d').replace(tzinfo=TZ_CDMX)
        except Exception:
            flash("❌ Fecha inválida.", "error")
            return redirect(url_for('registro_admin'))

        datos = {
            "folio":     folio_manual or None,
            "marca":     marca, "linea": linea, "anio": anio,
            "serie":     serie, "motor": motor, "color": color, "nombre": nombre,
            "fecha_exp": fecha_inicio,
            "fecha_ven": fecha_inicio + timedelta(days=DIAS_PERMISO),
            "estado_pago": "VALIDADO",
            # SIN user_id → folio oficial, puede renovarse
        }

        if not guardar_folio_con_reintento(datos, "ADMIN"):
            flash("❌ Error al registrar.", "error")
            return redirect(url_for('registro_admin'))

        threading.Thread(target=generar_pdf_en_background, args=(dict(datos),), daemon=True).start()
        flash('✅ Permiso generado.', 'success')
        return render_template('exitoso.html',
                               folio=datos["folio"], serie=serie,
                               fecha_generacion=fecha_inicio.strftime('%d/%m/%Y %H:%M'))

    return render_template('registro_admin.html')


@app.route('/admin_folios')
def admin_folios():
    if not session.get('admin'):
        return redirect(url_for('login'))

    folios = supabase.table("folios_registrados") \
        .select("*").eq("entidad", ENTIDAD) \
        .order("fecha_expedicion", desc=True).execute().data or []

    hoy = today_cdmx()
    for f in folios:
        try:
            fv = parse_date_any(f.get('fecha_vencimiento'))
            f['estado'] = "VIGENTE" if hoy <= fv else "VENCIDO"
        except Exception:
            f['estado'] = 'ERROR'

    return render_template('admin_folios.html', folios=folios)


@app.route('/editar_folio/<folio>', methods=['GET', 'POST'])
def editar_folio(folio):
    if not session.get('admin'):
        return redirect(url_for('login'))
    if request.method == 'POST':
        supabase.table("folios_registrados").update({
            "marca":             request.form['marca'],
            "linea":             request.form['linea'],
            "anio":              request.form['anio'],
            "numero_serie":      request.form['serie'],
            "numero_motor":      request.form['motor'],
            "fecha_expedicion":  request.form['fecha_expedicion'],
            "fecha_vencimiento": request.form['fecha_vencimiento'],
        }).eq("folio", folio).execute()
        flash("Folio actualizado.", "success")
        return redirect(url_for('admin_folios'))
    resp = supabase.table("folios_registrados").select("*").eq("folio", folio).execute()
    if not resp.data:
        flash("Folio no encontrado.", "error")
        return redirect(url_for('admin_folios'))
    return render_template("editar_folio.html", folio=resp.data[0])


@app.route('/eliminar_folio', methods=['POST'])
def eliminar_folio():
    if not session.get('admin'):
        return redirect(url_for('login'))
    supabase.table("folios_registrados").delete().eq("folio", request.form['folio']).execute()
    flash("Folio eliminado.", "success")
    return redirect(url_for('admin_folios'))


# ── Gestión de usuarios ───────────────────────────────────────────────────────

@app.route('/admin/usuarios')
def admin_usuarios():
    if not session.get('admin'):
        return redirect(url_for('login'))
    resp     = supabase.table("verificaciondigitalcdmx") \
        .select("*").order("id", desc=True).execute()
    usuarios = resp.data or []
    for u in usuarios:
        u['timer_info'] = get_timer_info(u)
    return render_template("admin_usuarios.html", usuarios=usuarios)


@app.route('/admin/marcar_pagado/<int:user_id>', methods=['POST'])
def marcar_pagado(user_id):
    if not session.get('admin'):
        return redirect(url_for('login'))
    supabase.table("verificaciondigitalcdmx") \
        .update({"pagado": True}).eq("id", user_id).execute()
    flash("Usuario marcado como PAGADO. Timer desactivado.", "success")
    return redirect(url_for('admin_usuarios'))


@app.route('/admin/marcar_pendiente/<int:user_id>', methods=['POST'])
def marcar_pendiente(user_id):
    if not session.get('admin'):
        return redirect(url_for('login'))
    supabase.table("verificaciondigitalcdmx") \
        .update({"pagado": False, "created_at": datetime.utcnow().isoformat()}) \
        .eq("id", user_id).execute()
    flash("Marcado como PENDIENTE. Timer reiniciado.", "warning")
    return redirect(url_for('admin_usuarios'))


@app.route('/admin/validar_pago/<folio>', methods=['POST'])
def validar_pago(folio):
    if not session.get('admin'):
        return jsonify({"ok": False, "error": "no autorizado"}), 403
    folio = folio.strip().upper()
    try:
        supabase.table("folios_registrados") \
            .update({"estado_pago": "VALIDADO"}).eq("folio", folio).execute()
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' \
                or request.accept_mimetypes.best == 'application/json':
            return jsonify({"ok": True})
        flash(f"Folio {folio} validado.", "success")
        return redirect(request.referrer or url_for('admin_folios'))
    except Exception as e:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"ok": False, "error": str(e)}), 500
        flash(f"Error: {e}", "error")
        return redirect(url_for('admin_folios'))


# ── API timer ────────────────────────────────────────────────────────────────

@app.route('/api/timer_estado')
def api_timer_estado():
    if not session.get('username') or session.get('admin'):
        return jsonify({"error": "no auth"}), 401
    resp = supabase.table("verificaciondigitalcdmx") \
        .select("pagado, created_at, folios_asignac, folios_usados") \
        .eq("username", session['username']).limit(1).execute()
    if not resp.data:
        return jsonify({"error": "no encontrado"}), 404
    u     = resp.data[0]
    asig  = int(u.get("folios_asignac", 0))
    usados = int(u.get("folios_usados", 0))
    return jsonify({
        "pagado":     u.get("pagado", False),
        "timer":      get_timer_info(u),
        "porcentaje": round((usados / asig) * 100) if asig > 0 else 0
    })


# ─── USUARIO (3RO) ───────────────────────────────────────────────────────────

@app.route('/registro_usuario', methods=['GET', 'POST'])
def registro_usuario():
    if not session.get('username'):
        return redirect(url_for('login'))
    if session.get('admin'):
        return redirect(url_for('admin'))

    ud = supabase.table("verificaciondigitalcdmx") \
        .select("*").eq("username", session['username']).limit(1).execute()
    if not ud.data:
        flash("Usuario no encontrado.", "error")
        return redirect(url_for('login'))

    usuario            = ud.data[0]
    folios_asignados   = int(usuario.get('folios_asignac', 0))
    folios_usados      = int(usuario.get('folios_usados', 0))
    folios_disponibles = folios_asignados - folios_usados
    folios_info        = {
        "folios_asignac": folios_asignados,
        "folios_usados":  folios_usados,
        "pagado":         usuario.get("pagado", False),
        "created_at":     usuario.get("created_at")
    }
    pct = round((folios_usados / folios_asignados) * 100) if folios_asignados > 0 else 0

    if request.method == 'POST':
        if folios_disponibles <= 0:
            flash("⚠️ Sin folios disponibles.", "error")
            return render_template('registro_usuario.html',
                                   folios_info=folios_info, porcentaje=pct,
                                   fecha_hoy=today_cdmx().isoformat())

        marca     = request.form.get('marca', '').strip().upper()
        linea     = request.form.get('linea', '').strip().upper()
        anio      = request.form.get('anio', '').strip()
        serie     = request.form.get('serie', '').strip().upper()
        motor     = request.form.get('motor', '').strip().upper()
        color     = request.form.get('color', '').strip().upper() or 'BLANCO'
        nombre    = request.form.get('nombre', '').strip().upper() or 'SIN NOMBRE'
        fecha_str = request.form.get('fecha_inicio', '').strip()

        if not all([marca, linea, anio, serie, motor, fecha_str]):
            flash("❌ Faltan campos obligatorios.", "error")
            return render_template('registro_usuario.html',
                                   folios_info=folios_info, porcentaje=pct,
                                   fecha_hoy=today_cdmx().isoformat())

        try:
            fecha_inicio = datetime.strptime(fecha_str, '%Y-%m-%d').replace(tzinfo=TZ_CDMX)
        except Exception:
            flash("❌ Fecha inválida.", "error")
            return render_template('registro_usuario.html',
                                   folios_info=folios_info, porcentaje=pct,
                                   fecha_hoy=today_cdmx().isoformat())

        datos = {
            "folio":       None,
            "marca":       marca, "linea": linea, "anio": anio,
            "serie":       serie, "motor": motor, "color": color, "nombre": nombre,
            "fecha_exp":   fecha_inicio,
            "fecha_ven":   fecha_inicio + timedelta(days=DIAS_PERMISO),
            # CON user_id → folio de lote, sin autoservicio de renovación
            "user_id":     session.get('user_id'),
            "estado_pago": "VALIDADO",
        }

        if not guardar_folio_con_reintento(datos, session['username']):
            flash("❌ Error al registrar.", "error")
            return render_template('registro_usuario.html',
                                   folios_info=folios_info, porcentaje=pct,
                                   fecha_hoy=today_cdmx().isoformat())

        threading.Thread(target=generar_pdf_en_background, args=(dict(datos),), daemon=True).start()

        supabase.table("verificaciondigitalcdmx") \
            .update({"folios_usados": folios_usados + 1}) \
            .eq("username", session['username']).execute()

        flash(f'✅ Folio: {datos["folio"]}', 'success')
        return render_template('exitoso.html',
                               folio=datos["folio"], serie=serie,
                               fecha_generacion=fecha_inicio.strftime('%d/%m/%Y %H:%M'))

    return render_template('registro_usuario.html',
                           folios_info=folios_info,
                           timer_info=get_timer_info(folios_info),
                           porcentaje=pct,
                           fecha_hoy=today_cdmx().isoformat())


@app.route('/mis_permisos')
def mis_permisos():
    if not session.get('username') or session.get('admin'):
        return redirect(url_for('login'))

    permisos = supabase.table("folios_registrados") \
        .select("*").eq("creado_por", session['username']) \
        .order("fecha_expedicion", desc=True).execute().data or []

    hoy = today_cdmx()
    for p in permisos:
        try:
            fe = parse_date_any(p.get('fecha_expedicion'))
            fv = parse_date_any(p.get('fecha_vencimiento'))
            p['fecha_formateada'] = fe.strftime('%d/%m/%Y')
            p['estado']           = "VIGENTE" if hoy <= fv else "VENCIDO"
            p['tiene_pdf']        = os.path.exists(os.path.join(OUTPUT_DIR, f"{p['folio']}.pdf"))
        except Exception:
            p['fecha_formateada'] = 'Error'
            p['estado']           = 'ERROR'
            p['tiene_pdf']        = False

    usr_data = supabase.table("verificaciondigitalcdmx") \
        .select("folios_asignac, folios_usados") \
        .eq("username", session['username']).limit(1).execute().data
    usr_row  = usr_data[0] if usr_data else {"folios_asignac": 0, "folios_usados": 0}

    return render_template('mis_permisos.html',
                           permisos=permisos, total_generados=len(permisos),
                           folios_asignados=int(usr_row.get('folios_asignac', 0)),
                           folios_usados=int(usr_row.get('folios_usados', 0)))


# ─── CONSULTA PÚBLICA ────────────────────────────────────────────────────────

@app.route('/consulta_folio', methods=['GET', 'POST'])
def consulta_folio():
    if request.method == 'POST':
        folio = request.form['folio'].strip()
        rows  = supabase.table("folios_registrados") \
            .select("*").eq("folio", folio).limit(1).execute().data
        if not rows:
            resultado = {"estado": "NO REGISTRADO", "color": "rojo", "folio": folio,
                         "puede_renovar": False, "ya_renovado": False}
        else:
            resultado = _armar_resultado_edomex(rows[0], folio)
        return render_template('resultado_consulta.html', resultado=resultado)
    return render_template('consulta_folio.html')


@app.route('/consulta/<folio>')
def consulta_folio_directo(folio):
    row = supabase.table("folios_registrados") \
        .select("*").eq("folio", folio).limit(1).execute().data
    if not row:
        return render_template("resultado_consulta.html",
                               resultado={"estado": "NO REGISTRADO", "color": "rojo",
                                          "folio": folio, "puede_renovar": False,
                                          "ya_renovado": False})
    resultado = _armar_resultado_edomex(row[0], folio)
    return render_template("resultado_consulta.html", resultado=resultado)


# ═══════════════════════════════════════════════════════════════════════════
# RENOVACIÓN — solo folios OFICIALES (sin user_id). Nunca para lotes.
# PARCHE ANTI-ROBO: doble check server-side antes de crear renovación.
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/renovar_folio/<folio_viejo>', methods=['POST'])
def renovar_folio(folio_viejo):
    t0 = time.time()
    folio_viejo = folio_viejo.strip().upper()
    logger.info(f"[RENOVAR] INICIO folio_viejo={folio_viejo}")

    resp = supabase.table("folios_registrados").select("*").eq("folio", folio_viejo).execute()
    if not resp.data:
        return jsonify({"ok": False, "error": "Folio original no encontrado"}), 404

    original = resp.data[0]

    # Bloqueo 1: folios de lote
    if original.get("user_id"):
        return jsonify({
            "ok": False,
            "error": "Este folio fue emitido por un proveedor. Contacta a quien te lo entregó para renovarlo."
        }), 403

    # Bloqueo 2: ya existe renovación pendiente
    ya_existente = supabase.table("folios_registrados") \
        .select("folio") \
        .eq("folio_origen", folio_viejo) \
        .eq("estado_pago", "PENDIENTE_PAGO") \
        .order("fecha_expedicion", desc=True) \
        .limit(1).execute()

    if ya_existente.data:
        folio_existente = ya_existente.data[0]["folio"]
        logger.info(f"[RENOVAR] Bloqueado — ya existe pendiente: {folio_existente}")
        return jsonify({
            "ok": False,
            "error": "Ya tienes una renovación pendiente de pago. Envía tu comprobante para activarla o espera 48 horas.",
            "folio_pendiente": folio_existente
        }), 409

    fecha_exp = now_cdmx()
    fecha_ven = fecha_exp + timedelta(days=DIAS_PERMISO)

    datos = {
        "folio":       None,
        "marca":       original.get("marca", ""),
        "linea":       original.get("linea", ""),
        "anio":        original.get("anio", ""),
        "serie":       original.get("numero_serie", ""),
        "motor":       original.get("numero_motor", ""),
        "color":       original.get("color", "BLANCO"),
        "nombre":      original.get("nombre", "SIN NOMBRE"),
        "fecha_exp":   fecha_exp,
        "fecha_ven":   fecha_ven,
        "estado_pago": "PENDIENTE_PAGO",
        "folio_origen": folio_viejo,
        # SIN user_id → sigue siendo oficial
    }

    if not guardar_folio_con_reintento(datos, "RENOVACION"):
        return jsonify({"ok": False, "error": "No se pudo registrar la renovación"}), 500

    folio_nuevo = datos["folio"]
    threading.Thread(target=generar_pdf_en_background, args=(dict(datos),), daemon=True).start()

    logger.info(f"[RENOVAR] FIN {time.time()-t0:.2f}s folio_nuevo={folio_nuevo}")
    return jsonify({
        "ok": True,
        "folio_nuevo": folio_nuevo,
        "horas_limite": HORAS_LIMITE_PAGO
    })


@app.route('/estado_pdf/<folio>')
def estado_pdf(folio):
    resp    = supabase.table("folios_registrados").select("pdf_url").eq("folio", folio).execute()
    pdf_url = resp.data[0].get("pdf_url", "") if resp.data else ""
    return jsonify({"pdf_url": pdf_url})


# ===================== LIMPIEZA 48H =====================

def limpiar_folios_no_pagados_edomex():
    try:
        limite = (now_cdmx() - timedelta(hours=HORAS_LIMITE_PAGO)).isoformat()
        vencidos = supabase.table("folios_registrados") \
            .select("folio") \
            .eq("estado_pago", "PENDIENTE_PAGO") \
            .eq("entidad", ENTIDAD) \
            .lt("fecha_expedicion", limite) \
            .execute()

        for row in (vencidos.data or []):
            folio = row["folio"]
            supabase.table("folios_registrados").delete().eq("folio", folio).execute()
            ruta = os.path.join(OUTPUT_DIR, f"{folio}.pdf")
            if os.path.exists(ruta):
                os.remove(ruta)
            try:
                supabase.storage.from_(BUCKET_NAME).remove([f"{folio}.pdf"])
            except Exception:
                pass
            logger.info(f"[LIMPIEZA 48H] {folio} eliminado")
    except Exception as e:
        logger.error(f"[LIMPIEZA 48H] {e}")

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler(timezone="America/Mexico_City")
    scheduler.add_job(limpiar_folios_no_pagados_edomex, 'interval', minutes=15)
    scheduler.start()
    logger.info("[SCHEDULER] Limpieza 48h activa")
except ImportError:
    logger.warning("[SCHEDULER] APScheduler no instalado, limpieza 48h desactivada")

# ===================== ADMIN TEST FECHAS =====================

@app.route('/admin/test_fechas', methods=['GET', 'POST'])
def admin_test_fechas():
    if not session.get('admin'):
        return redirect(url_for('login'))

    if request.method == 'POST':
        accion = request.form.get('accion')
        folio  = request.form.get('folio', '').strip().upper()

        if not folio:
            flash("Escribe un folio.", "error")
            return redirect(url_for('admin_test_fechas'))

        resp = supabase.table("folios_registrados").select("*").eq("folio", folio).execute()
        if not resp.data:
            flash(f"Folio {folio} no encontrado.", "error")
            return redirect(url_for('admin_test_fechas'))

        if accion == 'vencer_permiso':
            nueva_ven = now_cdmx() - timedelta(days=1)
            supabase.table("folios_registrados") \
                .update({"fecha_vencimiento": nueva_ven.date().isoformat()}) \
                .eq("folio", folio).execute()
            flash(f"Folio {folio} marcado VENCIDO.", "success")

        elif accion == 'vencer_pago_48h':
            nueva_exp = now_cdmx() - timedelta(hours=49)
            supabase.table("folios_registrados") \
                .update({"fecha_expedicion": nueva_exp.isoformat()}) \
                .eq("folio", folio).execute()
            flash(f"Folio {folio}: expedición movida 49h atrás.", "success")

        elif accion == 'restaurar':
            hoy = now_cdmx()
            ven = hoy + timedelta(days=DIAS_PERMISO)
            supabase.table("folios_registrados") \
                .update({
                    "fecha_expedicion": hoy.date().isoformat(),
                    "fecha_vencimiento": ven.date().isoformat()
                }).eq("folio", folio).execute()
            flash(f"Folio {folio} restaurado.", "success")

        return redirect(url_for('admin_test_fechas') + f"?folio={folio}")

    folio_buscar = request.args.get('folio', '').strip().upper()
    resultado = None
    if folio_buscar:
        resp = supabase.table("folios_registrados").select("*").eq("folio", folio_buscar).execute()
        if resp.data:
            resultado = resp.data[0]
        else:
            flash(f"Folio {folio_buscar} no encontrado.", "error")

    return render_template('admin_test_fechas.html', resultado=resultado, folio_buscar=folio_buscar)


# ─── DESCARGAS ───────────────────────────────────────────────────────────────

@app.route('/descargar_pdf/<folio>')
def descargar_pdf(folio):
    # Prioridad: Storage > disco local
    resp = supabase.table("folios_registrados").select("pdf_url").eq("folio", folio).execute()
    if resp.data and resp.data[0].get("pdf_url"):
        return redirect(resp.data[0]["pdf_url"])

    ruta = os.path.join(OUTPUT_DIR, f"{folio}.pdf")
    if not os.path.exists(ruta):
        abort(404)
    return send_file(ruta, as_attachment=True,
                     download_name=f"{folio}_edomex.pdf",
                     mimetype='application/pdf')


@app.route('/descargar_recibo/<folio>')
def descargar_recibo(folio):
    if not session.get('username'):
        return redirect(url_for('login'))
    if not session.get('admin'):
        resp = supabase.table("folios_registrados") \
            .select("creado_por").eq("folio", folio).limit(1).execute()
        if resp.data and resp.data[0].get("creado_por") != session['username']:
            flash("No tienes permiso.", "error")
            return redirect(url_for('mis_permisos'))
    ruta_pdf = os.path.join(OUTPUT_DIR, f"{folio}.pdf")
    if not os.path.exists(ruta_pdf):
        flash("PDF no encontrado.", "error")
        return redirect(url_for('mis_permisos') if not session.get('admin') else url_for('admin_folios'))
    return send_file(ruta_pdf, as_attachment=True,
                     download_name=f"{folio}_edomex.pdf", mimetype='application/pdf')


# ===================== ADMIN TABLAS =====================

@app.route('/admin_tablas')
def admin_tablas():
    if not session.get('admin'):
        return redirect(url_for('login'))
    return render_template('admin_tablas.html', tablas=TABLAS_DISPONIBLES)


@app.route('/admin_tabla/<nombre_tabla>')
def admin_tabla(nombre_tabla):
    if not session.get('admin'):
        return redirect(url_for('login'))
    if nombre_tabla not in TABLAS_DISPONIBLES:
        flash('Tabla no encontrada', 'error')
        return redirect(url_for('admin_tablas'))

    info_tabla = TABLAS_DISPONIBLES[nombre_tabla]
    pk_col     = info_tabla['pk_col']
    q          = request.args.get('q', '').strip()
    page       = max(1, int(request.args.get('page', 1) or 1))

    try:
        todos = supabase.table(nombre_tabla).select("*").limit(20000).execute().data or []
        if q:
            q_lower   = q.lower()
            filtrados = [r for r in todos
                         if any(q_lower in str(v).lower() for v in r.values() if v is not None)]
        else:
            filtrados = todos

        total     = len(filtrados)
        offset    = (page - 1) * PAGE_SIZE
        registros = filtrados[offset: offset + PAGE_SIZE]
    except Exception as e:
        flash(f'Error al cargar datos: {e}', 'error')
        registros, total, offset, todos = [], 0, 0, []

    columnas    = list(registros[0].keys()) if registros \
                  else (list(todos[0].keys()) if todos else info_tabla.get('columnas', []))
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    return render_template('admin_tabla_detalle.html',
                           nombre_tabla=nombre_tabla, info_tabla=info_tabla,
                           registros=registros, columnas=columnas, pk_col=pk_col,
                           q=q, page=page, offset=offset, total=total,
                           total_pages=total_pages)


@app.route('/api/update_cell', methods=['POST'])
def api_update_cell():
    if not session.get('admin'):
        return jsonify({"ok": False, "error": "no autorizado"}), 403
    d      = request.get_json(force=True)
    tabla  = d.get('tabla')
    pk_col = d.get('pk_col')
    pk_val = d.get('pk_val')
    col    = d.get('col')
    val    = d.get('val', '')
    if tabla not in TABLAS_DISPONIBLES or not col or not pk_val:
        return jsonify({"ok": False, "error": "datos inválidos"}), 400
    try:
        supabase.table(tabla).update({col: val or None}).eq(pk_col, pk_val).execute()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route('/api/delete_row', methods=['POST'])
def api_delete_row():
    if not session.get('admin'):
        return jsonify({"ok": False, "error": "no autorizado"}), 403
    d      = request.get_json(force=True)
    tabla  = d.get('tabla')
    pk_col = d.get('pk_col')
    pk_val = d.get('pk_val')
    if tabla not in TABLAS_DISPONIBLES or not pk_val:
        return jsonify({"ok": False, "error": "datos inválidos"}), 400
    try:
        supabase.table(tabla).delete().eq(pk_col, pk_val).execute()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ─── MAIN ────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    logger.info("🚀 SERVIDOR EDOMEX INICIADO")
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)

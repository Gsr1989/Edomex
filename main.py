from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file, abort
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from supabase import create_client, Client
import fitz
import os
import qrcode
from PIL import Image
from io import BytesIO
import time
import re
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

# ===================== SUPABASE CONFIG =====================
SUPABASE_URL = "https://xsagwqepoljfsogusubw.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InhzYWd3cWVwb2xqZnNvZ3VzdWJ3Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDM5NjM3NTUsImV4cCI6MjA1OTUzOTc1NX0.NUixULn0m2o49At8j6X58UqbXre2O2_JStqzls_8Gws"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ===================== CONFIG GENERAL =====================
OUTPUT_DIR = "documentos"
PLANTILLA_PDF = "edomex_plantilla_alta_res.pdf"
PLANTILLA_BUENO = "labuena3.0.pdf"
URL_CONSULTA_BASE = "https://sfpyaedomexicoconsultapermisodigital.onrender.com"
ENTIDAD = "edomex"

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ===================== COORDENADAS PDF EDOMEX =====================
coords_edomex = {
    "folio": (535, 135, 14, (1, 0, 0)),
    "marca": (109, 190, 9, (0, 0, 0)),
    "serie": (230, 233, 9, (0, 0, 0)),
    "linea": (238, 190, 9, (0, 0, 0)),
    "motor": (104, 233, 9, (0, 0, 0)),
    "anio": (410, 190, 9, (0, 0, 0)),
    "color": (400, 233, 9, (0, 0, 0)),
    "fecha_exp": (190, 280, 9, (0, 0, 0)),
    "fecha_ven": (380, 280, 9, (0, 0, 0)),
    "nombre": (394, 320, 9, (0, 0, 0)),
}

# ===================== FOLIOS EDOMEX: 331 + CONSECUTIVO =====================
PREFIJO_EDOMEX = "331"

def generar_folio_automatico_edomex():
    """
    Genera folio con prefijo 331 + consecutivo
    Formato: 3312, 3313, 3314... infinito
    Intenta 10,000,000 de veces hasta encontrar uno disponible
    """
    logger.info("[FOLIO] Iniciando generación automática EDOMEX")
    
    # 1. TRAER TODOS LOS FOLIOS DE EDOMEX
    todos = supabase.table("folios_registrados")\
        .select("folio")\
        .eq("entidad", ENTIDAD)\
        .execute().data or []
    
    logger.info(f"[FOLIO] Total folios en BD: {len(todos)}")
    
    # 2. FILTRAR SOLO LOS QUE SON DE EDOMEX (empiezan con 331)
    consecutivos = []
    for f in todos:
        folio_str = str(f['folio'])
        if folio_str.startswith(PREFIJO_EDOMEX):
            try:
                # Extraer el número después de "331"
                num = int(folio_str[3:])
                consecutivos.append(num)
            except:
                pass
    
    logger.info(f"[FOLIO] Consecutivos válidos: {len(consecutivos)}")
    
    # 3. ENCONTRAR EL SIGUIENTE DISPONIBLE
    if not consecutivos:
        siguiente = 2  # Empezar en 3312
        logger.info(f"[FOLIO] Sin folios, empezando en {PREFIJO_EDOMEX}{siguiente}")
    else:
        ultimo = max(consecutivos)
        siguiente = ultimo + 1
        logger.info(f"[FOLIO] Último: {PREFIJO_EDOMEX}{ultimo}, siguiente: {PREFIJO_EDOMEX}{siguiente}")
    
    # 4. BUSCAR HASTA 10,000,000 DE VECES
    for intento in range(10000000):
        folio_candidato = f"{PREFIJO_EDOMEX}{siguiente + intento}"
        
        # Verificar si existe
        existe = supabase.table("folios_registrados")\
            .select("folio")\
            .eq("folio", folio_candidato)\
            .limit(1)\
            .execute().data
        
        if not existe:
            logger.info(f"[FOLIO] ✅ Encontrado: {folio_candidato} (intento {intento + 1})")
            return folio_candidato
        
        # Log cada 10,000 intentos
        if intento > 0 and intento % 10000 == 0:
            logger.info(f"[FOLIO] Buscando... intento {intento}")
    
    raise Exception("No se encontró folio disponible después de 10,000,000 intentos")

def guardar_folio_con_reintento(datos, username):
    """
    Guarda folio en BD con reintentos
    Si falla por duplicado, busca el siguiente disponible
    """
    max_intentos = 10000000
    
    # Si no hay folio, generar uno
    if not datos.get("folio"):
        try:
            datos["folio"] = generar_folio_automatico_edomex()
        except Exception as e:
            logger.error(f"[ERROR] No se pudo generar folio: {e}")
            return False
    
    fexp_date = parse_date_any(datos["fecha_exp"])
    fven_date = parse_date_any(datos["fecha_ven"])
    
    folio_base = datos["folio"]
    
    # Extraer el número después de "331"
    try:
        num_inicial = int(folio_base[3:])
    except:
        num_inicial = 2
    
    for intento in range(max_intentos):
        folio_actual = f"{PREFIJO_EDOMEX}{num_inicial + intento}"
        
        try:
            supabase.table("folios_registrados").insert({
                "folio": folio_actual,
                "marca": datos["marca"],
                "linea": datos["linea"],
                "anio": datos["anio"],
                "numero_serie": datos["serie"],
                "numero_motor": datos["motor"],
                "color": datos.get("color", "BLANCO"),
                "nombre": datos.get("nombre", "SIN NOMBRE"),
                "fecha_expedicion": fexp_date.isoformat(),
                "fecha_vencimiento": fven_date.isoformat(),
                "entidad": ENTIDAD,
                "estado": "ACTIVO",
                "creado_por": username
            }).execute()
            
            datos["folio"] = folio_actual
            logger.info(f"[DB] ✅ Folio {folio_actual} guardado (intento {intento + 1})")
            return True
            
        except Exception as e:
            em = str(e).lower()
            if "duplicate" in em or "unique constraint" in em or "23505" in em:
                logger.warning(f"[DUP] {folio_actual} existe, probando siguiente...")
                continue
            
            logger.error(f"[ERROR BD] {e}")
            return False
        
        # Log cada 10,000 intentos
        if intento > 0 and intento % 10000 == 0:
            logger.info(f"[DB] Guardando... intento {intento}")
    
    logger.error(f"[ERROR] No se encontró folio disponible tras {max_intentos} intentos")
    return False

# ===================== GENERACIÓN PDF =====================
def generar_qr_dinamico(folio):
    try:
        url_directa = f"{URL_CONSULTA_BASE}/consulta/{folio}"
        qr = qrcode.QRCode(
            version=2,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=4,
            border=1
        )
        qr.add_data(url_directa)
        qr.make(fit=True)
        img_qr = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        logger.info(f"[QR] ✅ {folio}")
        return img_qr, url_directa
    except Exception as e:
        logger.error(f"[ERROR QR] {e}")
        return None, None

def generar_pdf_unificado(datos: dict) -> str:
    """Genera UN SOLO PDF con ambas plantillas (2 páginas)"""
    fol = datos["folio"]
    fecha_exp_dt = datos["fecha_exp"]
    fecha_ven_dt = datos["fecha_ven"]

    if fecha_exp_dt.tzinfo is None:
        fecha_exp_dt = fecha_exp_dt.replace(tzinfo=TZ_CDMX)
    else:
        fecha_exp_dt = fecha_exp_dt.astimezone(TZ_CDMX)

    if isinstance(fecha_ven_dt, str):
        fecha_ven_str = fecha_ven_dt
    else:
        if fecha_ven_dt.tzinfo is None:
            fecha_ven_dt = fecha_ven_dt.replace(tzinfo=TZ_CDMX)
        else:
            fecha_ven_dt = fecha_ven_dt.astimezone(TZ_CDMX)
        fecha_ven_str = fecha_ven_dt.strftime("%d/%m/%Y")

    out = os.path.join(OUTPUT_DIR, f"{fol}.pdf")

    try:
        # ===== PÁGINA 1: PLANTILLA PRINCIPAL =====
        doc1 = fitz.open(PLANTILLA_PDF)
        pg1 = doc1[0]

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

        pg1.insert_text(coords_edomex["nombre"][:2], datos.get("nombre", ""),
                       fontsize=coords_edomex["nombre"][2],
                       color=coords_edomex["nombre"][3])

        # QR dinámico
        img_qr, _ = generar_qr_dinamico(fol)
        
        if img_qr:
            buf = BytesIO()
            img_qr.save(buf, format="PNG")
            buf.seek(0)
            qr_pix = fitz.Pixmap(buf.read())

            x_qr = 493
            y_qr = 35
            ancho_qr = 82
            alto_qr = 82

            pg1.insert_image(
                fitz.Rect(x_qr, y_qr, x_qr + ancho_qr, y_qr + alto_qr),
                pixmap=qr_pix,
                overlay=True
            )

        # ===== PÁGINA 2: PLANTILLA SIMPLE =====
        doc2 = fitz.open(PLANTILLA_BUENO)
        pg2 = doc2[0]
        
        pg2.insert_text((80, 142), fecha_exp_dt.strftime("%d/%m/%Y"), fontsize=15, fontname="helv", color=(0, 0, 0))
        pg2.insert_text((218, 142), fecha_exp_dt.strftime("%d/%m/%Y"), fontsize=15, fontname="helv", color=(0, 0, 0))
        pg2.insert_text((182, 283), fecha_exp_dt.strftime("%d/%m/%Y"), fontsize=9, fontname="helv", color=(0, 0, 0))
        pg2.insert_text((130, 435), fecha_exp_dt.strftime("%d/%m/%Y"), fontsize=20, fontname="helv", color=(0, 0, 0))
        pg2.insert_text((162, 185), datos["serie"], fontsize=9, fontname="helv", color=(0, 0, 0))

        # ===== UNIR AMBAS PÁGINAS =====
        doc_final = fitz.open()
        doc_final.insert_pdf(doc1)
        doc_final.insert_pdf(doc2)
        
        doc_final.save(out)
        
        doc_final.close()
        doc1.close()
        doc2.close()
        
        logger.info(f"[PDF] ✅ {out}")

    except Exception as e:
        logger.error(f"[ERROR PDF] {e}")
        doc_fallback = fitz.open()
        page = doc_fallback.new_page()
        page.insert_text((50, 50), f"ERROR - Folio: {fol}", fontsize=12)
        doc_fallback.save(out)
        doc_fallback.close()

    return out

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
            session['admin'] = True
            session['username'] = 'Serg890105tm3'
            logger.info("[LOGIN] Admin")
            return redirect(url_for('admin'))

        resp = supabase.table("verificaciondigitalcdmx")\
            .select("*")\
            .eq("username", username)\
            .eq("password", password)\
            .execute()

        if resp.data:
            session['user_id'] = resp.data[0].get('id')
            session['username'] = resp.data[0]['username']
            session['admin'] = False
            logger.info(f"[LOGIN] {username}")
            return redirect(url_for('registro_usuario'))

        flash('Usuario o contraseña incorrectos', 'error')

    return render_template('login.html')

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
        folios = int(request.form['folios'])

        existe = supabase.table("verificaciondigitalcdmx")\
            .select("id")\
            .eq("username", username)\
            .limit(1)\
            .execute()

        if existe.data:
            flash('Error: el usuario ya existe.', 'error')
        else:
            supabase.table("verificaciondigitalcdmx").insert({
                "username": username,
                "password": password,
                "folios_asignac": folios,
                "folios_usados": 0
            }).execute()
            flash('Usuario creado.', 'success')

    return render_template('crear_usuario.html')

@app.route('/registro_usuario', methods=['GET', 'POST'])
def registro_usuario():
    if not session.get('username'):
        return redirect(url_for('login'))

    if session.get('admin'):
        return redirect(url_for('admin'))

    user_data = supabase.table("verificaciondigitalcdmx")\
        .select("*")\
        .eq("username", session['username'])\
        .limit(1)\
        .execute()

    if not user_data.data:
        flash("Usuario no encontrado.", "error")
        return redirect(url_for('login'))

    usuario = user_data.data[0]
    folios_asignados = int(usuario.get('folios_asignac', 0))
    folios_usados = int(usuario.get('folios_usados', 0))
    folios_disponibles = folios_asignados - folios_usados

    folios_info = {"folios_asignac": folios_asignados, "folios_usados": folios_usados}

    if request.method == 'POST':
        if folios_disponibles <= 0:
            flash("⚠️ Sin folios disponibles.", "error")
            return render_template('registro_usuario.html', folios_info=folios_info)

        marca = request.form.get('marca', '').strip().upper()
        linea = request.form.get('linea', '').strip().upper()
        anio = request.form.get('anio', '').strip()
        numero_serie = request.form.get('serie', '').strip().upper()
        numero_motor = request.form.get('motor', '').strip().upper()
        color = request.form.get('color', '').strip().upper() or 'BLANCO'
        nombre = request.form.get('nombre', '').strip().upper() or 'SIN NOMBRE'

        if not all([marca, linea, anio, numero_serie, numero_motor]):
            flash("❌ Faltan campos obligatorios.", "error")
            return render_template('registro_usuario.html', folios_info=folios_info)

        ahora = now_cdmx()
        vigencia = int(request.form.get('vigencia', 30))
        venc = ahora + timedelta(days=vigencia)

        datos = {
            "folio": None,
            "marca": marca,
            "linea": linea,
            "anio": anio,
            "serie": numero_serie,
            "motor": numero_motor,
            "color": color,
            "nombre": nombre,
            "fecha_exp": ahora,
            "fecha_ven": venc
        }

        ok = guardar_folio_con_reintento(datos, session['username'])
        if not ok:
            flash("❌ Error al registrar.", "error")
            return render_template('registro_usuario.html', folios_info=folios_info)

        folio_final = datos["folio"]
        generar_pdf_unificado(datos)

        supabase.table("verificaciondigitalcdmx")\
            .update({"folios_usados": folios_usados + 1})\
            .eq("username", session['username'])\
            .execute()

        flash(f'✅ Folio: {folio_final}', 'success')
        return render_template(
            'exitoso.html',
            folio=folio_final,
            serie=numero_serie,
            fecha_generacion=ahora.strftime('%d/%m/%Y %H:%M')
        )

    return render_template('registro_usuario.html', folios_info=folios_info)

@app.route('/mis_permisos')
def mis_permisos():
    if not session.get('username') or session.get('admin'):
        return redirect(url_for('login'))

    permisos = supabase.table("folios_registrados")\
        .select("*")\
        .eq("creado_por", session['username'])\
        .order("fecha_expedicion", desc=True)\
        .execute().data or []

    hoy = today_cdmx()

    for p in permisos:
        try:
            fe = parse_date_any(p.get('fecha_expedicion'))
            fv = parse_date_any(p.get('fecha_vencimiento'))
            p['fecha_formateada'] = fe.strftime('%d/%m/%Y')
            p['hora_formateada'] = "00:00:00"
            p['estado'] = "VIGENTE" if hoy <= fv else "VENCIDO"
        except:
            p['fecha_formateada'] = 'Error'
            p['hora_formateada'] = 'Error'
            p['estado'] = 'ERROR'

    usr_data = supabase.table("verificaciondigitalcdmx")\
        .select("folios_asignac, folios_usados")\
        .eq("username", session['username'])\
        .limit(1)\
        .execute().data

    usr_row = usr_data[0] if usr_data else {"folios_asignac": 0, "folios_usados": 0}

    return render_template(
        'mis_permisos.html',
        permisos=permisos,
        total_generados=len(permisos),
        folios_asignados=int(usr_row.get('folios_asignac', 0)),
        folios_usados=int(usr_row.get('folios_usados', 0))
    )

@app.route('/registro_admin', methods=['GET', 'POST'])
def registro_admin():
    if not session.get('admin'):
        return redirect(url_for('login'))

    if request.method == 'POST':
        folio_manual = request.form.get('folio', '').strip()
        
        marca = request.form.get('marca', '').strip().upper()
        linea = request.form.get('linea', '').strip().upper()
        anio = request.form.get('anio', '').strip()
        numero_serie = request.form.get('serie', '').strip().upper()
        numero_motor = request.form.get('motor', '').strip().upper()
        color = request.form.get('color', '').strip().upper() or 'BLANCO'
        nombre = request.form.get('nombre', '').strip().upper() or 'SIN NOMBRE'

        if not all([marca, linea, anio, numero_serie, numero_motor]):
            flash("❌ Faltan campos.", "error")
            return redirect(url_for('registro_admin'))

        ahora = now_cdmx()
        vigencia = int(request.form.get('vigencia', 30))
        venc = ahora + timedelta(days=vigencia)

        datos = {
            "folio": folio_manual if folio_manual else None,
            "marca": marca,
            "linea": linea,
            "anio": anio,
            "serie": numero_serie,
            "motor": numero_motor,
            "color": color,
            "nombre": nombre,
            "fecha_exp": ahora,
            "fecha_ven": venc
        }

        ok = guardar_folio_con_reintento(datos, "ADMIN")
        if not ok:
            flash("❌ Error al registrar.", "error")
            return redirect(url_for('registro_admin'))

        folio_final = datos["folio"]
        generar_pdf_unificado(datos)

        flash('✅ Permiso generado.', 'success')
        return render_template(
            'exitoso.html',
            folio=folio_final,
            serie=numero_serie,
            fecha_generacion=ahora.strftime('%d/%m/%Y %H:%M')
        )

    return render_template('registro_admin.html')

@app.route('/consulta_folio', methods=['GET', 'POST'])
def consulta_folio():
    resultado = None

    if request.method == 'POST':
        folio = request.form['folio'].strip()
        registros = supabase.table("folios_registrados")\
            .select("*")\
            .eq("folio", folio)\
            .limit(1)\
            .execute().data

        if not registros:
            resultado = {"estado": "NO REGISTRADO", "color": "rojo", "folio": folio}
        else:
            r = registros[0]
            fexp = parse_date_any(r.get('fecha_expedicion'))
            fven = parse_date_any(r.get('fecha_vencimiento'))
            hoy = today_cdmx()
            estado = "VIGENTE" if hoy <= fven else "VENCIDO"
            color = "verde" if estado == "VIGENTE" else "cafe"

            resultado = {
                "estado": estado,
                "color": color,
                "folio": folio,
                "fecha_expedicion": fexp.strftime('%d/%m/%Y'),
                "fecha_vencimiento": fven.strftime('%d/%m/%Y'),
                "marca": r.get('marca', ''),
                "linea": r.get('linea', ''),
                "año": r.get('anio', ''),
                "numero_serie": r.get('numero_serie', ''),
                "numero_motor": r.get('numero_motor', ''),
                "entidad": r.get('entidad', ENTIDAD)
            }

        return render_template('resultado_consulta.html', resultado=resultado)

    return render_template('consulta_folio.html')

@app.route('/consulta/<folio>')
def consulta_folio_directo(folio):
    row = supabase.table("folios_registrados")\
        .select("*")\
        .eq("folio", folio)\
        .limit(1)\
        .execute().data

    if not row:
        return render_template("resultado_consulta.html", resultado={
            "estado": "NO REGISTRADO",
            "color": "rojo",
            "folio": folio
        })

    r = row[0]
    fe = parse_date_any(r.get('fecha_expedicion'))
    fv = parse_date_any(r.get('fecha_vencimiento'))
    hoy = today_cdmx()
    estado = "VIGENTE" if hoy <= fv else "VENCIDO"
    color = "verde" if estado == "VIGENTE" else "cafe"

    resultado = {
        "estado": estado,
        "color": color,
        "folio": folio,
        "fecha_expedicion": fe.strftime("%d/%m/%Y"),
        "fecha_vencimiento": fv.strftime("%d/%m/%Y"),
        "marca": r.get('marca', ''),
        "linea": r.get('linea', ''),
        "año": r.get('anio', ''),
        "numero_serie": r.get('numero_serie', ''),
        "numero_motor": r.get('numero_motor', ''),
        "entidad": r.get('entidad', ENTIDAD)
    }

    return render_template("resultado_consulta.html", resultado=resultado)

@app.route('/descargar_recibo/<folio>')
def descargar_recibo(folio):
    ruta_pdf = os.path.join(OUTPUT_DIR, f"{folio}.pdf")
    if not os.path.exists(ruta_pdf):
        abort(404)

    return send_file(
        ruta_pdf,
        as_attachment=True,
        download_name=f"{folio}_edomex.pdf",
        mimetype='application/pdf'
    )

@app.route('/admin_folios')
def admin_folios():
    if not session.get('admin'):
        return redirect(url_for('login'))
    
    folios = supabase.table("folios_registrados")\
        .select("*")\
        .eq("entidad", ENTIDAD)\
        .order("fecha_expedicion", desc=True)\
        .execute().data or []
    
    hoy = today_cdmx()
    
    for f in folios:
        try:
            fe = parse_date_any(f.get('fecha_expedicion'))
            fv = parse_date_any(f.get('fecha_vencimiento'))
            f['estado'] = "VIGENTE" if hoy <= fv else "VENCIDO"
        except:
            f['estado'] = 'ERROR'
    
    return render_template('admin_folios.html', folios=folios)

@app.route('/editar_folio/<folio>', methods=['GET', 'POST'])
def editar_folio(folio):
    if not session.get('admin'):
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        data = {
            "marca": request.form['marca'],
            "linea": request.form['linea'],
            "anio": request.form['anio'],
            "numero_serie": request.form['serie'],
            "numero_motor": request.form['motor'],
            "fecha_expedicion": request.form['fecha_expedicion'],
            "fecha_vencimiento": request.form['fecha_vencimiento']
        }
        supabase.table("folios_registrados").update(data).eq("folio", folio).execute()
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
    
    folio = request.form['folio']
    supabase.table("folios_registrados").delete().eq("folio", folio).execute()
    flash("Folio eliminado.", "success")
    return redirect(url_for('admin_folios'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    logger.info("🚀 SERVIDOR EDOMEX INICIADO")
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)

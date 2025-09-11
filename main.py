from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
from datetime import datetime, timedelta
from supabase import create_client, Client
import fitz
import os

app = Flask(__name__)
app.secret_key = 'clave_muy_segura_123456'

SUPABASE_URL = "https://xsagwqepoljfsogusubw.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InhzYWd3cWVwb2xqZnNvZ3VzdWJ3Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDM5NjM3NTUsImV4cCI6MjA1OTUzOTc1NX0.NUixULn0m2o49At8j6X58UqbXre2O2_JStqzls_8Gws"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

ENTIDAD = "edomex"

@app.route('/')
def inicio():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        u = request.form['username']
        p = request.form['password']
        if u == 'Serg890105tm3' and p == 'Serg890105tm3':
            session['admin'] = True
            return redirect(url_for('admin'))
        resp = supabase.table("verificaciondigitalcdmx").select("*").eq("username", u).eq("password", p).execute()
        if resp.data:
            session['user_id'] = resp.data[0]['id']
            session['username'] = resp.data[0]['username']
            return redirect(url_for('registro_usuario'))
        
        return render_template('bloqueado.html')
    return render_template('login.html')

@app.route('/admin')
def admin():
    if 'admin' not in session:
        return redirect(url_for('login'))
    return render_template('panel.html')

@app.route('/registro_usuario', methods=['GET','POST'])
def registro_usuario():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    if request.method == 'POST':
        folio = request.form['folio']
        marca = request.form['marca']
        linea = request.form['linea']
        anio = request.form['anio']
        numero_serie = request.form['serie']
        numero_motor = request.form['motor']
        vigencia = int(request.form['vigencia'])

        if supabase.table("folios_registrados").select("*").eq("folio", folio).execute().data:
            flash('Error: el folio ya existe.', 'error')
            return redirect(url_for('registro_usuario'))

        ud = supabase.table("verificaciondigitalcdmx").select("folios_asignac,folios_usados").eq("id", user_id).execute().data
        if not ud:
            flash('No se pudo obtener info de usuario.', 'error')
            return redirect(url_for('registro_usuario'))
        info = ud[0]
        if info['folios_asignac'] - info['folios_usados'] <= 0:
            flash('No tienes folios disponibles.', 'error')
            return redirect(url_for('registro_usuario'))

        fecha_expedicion = datetime.now()
        fecha_vencimiento = fecha_expedicion + timedelta(days=vigencia)

        supabase.table("folios_registrados").insert({
            "folio": folio,
            "marca": marca,
            "linea": linea,
            "anio": anio,
            "numero_serie": numero_serie,
            "numero_motor": numero_motor,
            "fecha_expedicion": fecha_expedicion.isoformat(),
            "fecha_vencimiento": fecha_vencimiento.isoformat(),
            "entidad": ENTIDAD
        }).execute()

        supabase.table("verificaciondigitalcdmx").update({
            "folios_usados": info['folios_usados'] + 1
        }).eq("id", user_id).execute()

        try:
            doc = fitz.open("labuena3.0.pdf")
            page = doc[0]
            page.insert_text((80,142), fecha_expedicion.strftime("%d/%m/%Y"), fontsize=15, fontname="helv", color=(0,0,0))
            page.insert_text((218,142), fecha_expedicion.strftime("%d/%m/%Y"), fontsize=15, fontname="helv", color=(0,0,0))
            page.insert_text((182,283), fecha_expedicion.strftime("%d/%m/%Y"), fontsize=9, fontname="helv", color=(0,0,0))
            page.insert_text((130,435), fecha_expedicion.strftime("%d/%m/%Y"), fontsize=20, fontname="helv", color=(0,0,0))
            page.insert_text((162,185), numero_serie, fontsize=9, fontname="helv", color=(0,0,0))
            os.makedirs("documentos", exist_ok=True)
            doc.save(f"documentos/{folio}.pdf")
            doc.close()
        except Exception as e:
            flash(f"Error al generar PDF: {e}", 'error')
            return redirect(url_for('registro_usuario'))

        return render_template("exitoso.html", folio=folio, serie=numero_serie, fecha_generacion=fecha_expedicion.strftime("%d/%m/%Y"))

    folios_info = supabase.table("verificaciondigitalcdmx").select("folios_asignac,folios_usados").eq("id", user_id).execute().data[0]
    return render_template("registro_usuario.html", folios_info=folios_info)

@app.route('/registro_admin', methods=['GET','POST'])
def registro_admin():
    if 'admin' not in session:
        return redirect(url_for('login'))
    if request.method == 'POST':
        folio = request.form['folio']
        marca = request.form['marca']
        linea = request.form['linea']
        anio = request.form['anio']
        numero_serie = request.form['serie']
        numero_motor = request.form['motor']
        vigencia = int(request.form['vigencia'])

        if supabase.table("folios_registrados").select("*").eq("folio", folio).execute().data:
            flash('Error: el folio ya existe.', 'error')
            return render_template('registro_admin.html')

        fecha_expedicion = datetime.now()
        fecha_vencimiento = fecha_expedicion + timedelta(days=vigencia)

        supabase.table("folios_registrados").insert({
            "folio": folio,
            "marca": marca,
            "linea": linea,
            "anio": anio,
            "numero_serie": numero_serie,
            "numero_motor": numero_motor,
            "fecha_expedicion": fecha_expedicion.isoformat(),
            "fecha_vencimiento": fecha_vencimiento.isoformat(),
            "entidad": ENTIDAD
        }).execute()

        try:
            doc = fitz.open("labuena3.0.pdf")
            page = doc[0]
            page.insert_text((80,142), fecha_expedicion.strftime("%d/%m/%Y"), fontsize=15, fontname="helv", color=(0,0,0))
            page.insert_text((218,142), fecha_expedicion.strftime("%d/%m/%Y"), fontsize=15, fontname="helv", color=(0,0,0))
            page.insert_text((182,283), fecha_expedicion.strftime("%d/%m/%Y"), fontsize=9, fontname="helv", color=(0,0,0))
            page.insert_text((130,435), fecha_expedicion.strftime("%d/%m/%Y"), fontsize=20, fontname="helv", color=(0,0,0))
            page.insert_text((162,185), numero_serie, fontsize=9, fontname="helv", color=(0,0,0))
            os.makedirs("documentos", exist_ok=True)
            doc.save(f"documentos/{folio}.pdf")
            doc.close()
        except Exception as e:
            flash(f"Error al generar PDF: {e}", 'error')
            return render_template('registro_admin.html')

        return render_template("exitoso.html", folio=folio, serie=numero_serie, fecha_generacion=fecha_expedicion.strftime("%d/%m/%Y"))
    return render_template('registro_admin.html')

@app.route('/descargar_pdf/<folio>')
def descargar_pdf(folio):
    return send_file(f"documentos/{folio}.pdf", as_attachment=True)

@app.route('/consulta_folio', methods=['GET','POST'])
def consulta_folio():
    resultado = None
    if request.method == 'POST':
        folio = request.form['folio'].strip().upper()
        resp = supabase.table("folios_registrados").select("*").eq("folio", folio).execute()
        if not resp.data:
            resultado = {"estado": "No encontrado", "folio": folio}
        else:
            reg = resp.data[0]
            fe = datetime.fromisoformat(reg['fecha_expedicion'])
            fv = datetime.fromisoformat(reg['fecha_vencimiento'])
            estado = "VIGENTE" if datetime.now() <= fv else "VENCIDO"
            resultado = {
                "estado": estado,
                "folio": folio,
                "fecha_expedicion": fe.strftime("%d/%m/%Y"),
                "fecha_vencimiento": fv.strftime("%d/%m/%Y"),
                "marca": reg['marca'],
                "linea": reg['linea'],
                "año": reg['anio'],
                "numero_serie": reg['numero_serie'],
                "numero_motor": reg['numero_motor'],
                "entidad": reg.get('entidad', '')
            }
        return render_template("resultado_consulta.html", resultado=resultado)
    return render_template("consulta_folio.html")

@app.route('/admin_folios')
def admin_folios():
    if 'admin' not in session:
        return redirect(url_for('login'))
    resp = supabase.table("folios_registrados").select("*").execute()
    return render_template("admin_folios.html", folios=resp.data or [])

@app.route('/editar_folio/<folio>', methods=['GET','POST'])
def editar_folio(folio):
    if 'admin' not in session:
        return redirect(url_for('login'))
    if request.method == 'POST':
        data = {
            "marca": request.form['marca'],
            "linea": request.form['linea'],
            "anio": request.form['anio'],
            "numero_serie": request.form['numero_serie'],
            "numero_motor": request.form['numero_motor'],
            "fecha_expedicion": request.form['fecha_expedicion'],
            "fecha_vencimiento": request.form['fecha_vencimiento'],
            "entidad": ENTIDAD
        }
        supabase.table("folios_registrados").update(data).eq("folio", folio).execute()
        flash("Folio actualizado correctamente.", "success")
        return redirect(url_for('admin_folios'))
    resp = supabase.table("folios_registrados").select("*").eq("folio", folio).execute()
    if not resp.data:
        flash("Folio no encontrado.", "error")
        return redirect(url_for('admin_folios'))
    return render_template("editar_folio.html", folio=resp.data[0])

@app.route('/eliminar_folio', methods=['POST'])
def eliminar_folio():
    if 'admin' not in session:
        return redirect(url_for('login'))
    folio = request.form['folio']
    supabase.table("folios_registrados").delete().eq("folio", folio).execute()
    flash("Folio eliminado correctamente.", "success")
    return redirect(url_for('admin_folios'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/crear_usuario', methods=['GET','POST'])
def crear_usuario():
    if 'admin' not in session:
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        u = request.form['username']
        p = request.form['password']
        fol = int(request.form['folios'])
        rol = request.form['rol']
        
        # Verificar si ya existe
        exists = supabase.table("verificaciondigitalcdmx")\
            .select("id")\
            .eq("username", u)\
            .execute()
        if exists.data:
            flash('Error: el usuario ya existe.', 'error')
            return render_template('crear_usuario.html')
        
        # Insertar nuevo usuario
        supabase.table("verificaciondigitalcdmx").insert({
            "username": u,
            "password": p,
            "rol": rol,
            "parent_id": None,   # Admin supremo
            "folios_asignac": fol,
            "folios_usados": 0
        }).execute()
        
        flash('Usuario creado exitosamente.', 'success')
    
    return render_template('crear_usuario.html')

@app.route('/crear_cliente', methods=['GET', 'POST'])
def crear_cliente():
    # Debe ser distribuidor para entrar
    if 'user_id' not in session or session.get('rol') != 'distribuidor':
        flash('Acceso no autorizado.', 'error')
        return redirect(url_for('login'))

    distribuidor_id = session['user_id']

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        folios_asignados = int(request.form['folios'])

        # 1️⃣ Verificar saldo del distribuidor
        datos_distribuidor = supabase.table("verificaciondigitalcdmx")\
            .select("folios_asignac", "folios_usados")\
            .eq("id", distribuidor_id).execute().data

        if not datos_distribuidor:
            flash('Error al obtener información del distribuidor.', 'error')
            return redirect(url_for('crear_cliente'))

        saldo_disponible = datos_distribuidor[0]['folios_asignac'] - datos_distribuidor[0]['folios_usados']
        if folios_asignados > saldo_disponible:
            flash('No tienes suficientes folios para asignar.', 'error')
            return redirect(url_for('crear_cliente'))

        # 2️⃣ Verificar que el username no exista ya
        existe = supabase.table("verificaciondigitalcdmx")\
            .select("id")\
            .eq("username", username)\
            .execute()
        if existe.data:
            flash('Error: el nombre de usuario ya existe.', 'error')
            return redirect(url_for('crear_cliente'))

        # 3️⃣ Crear el cliente
        supabase.table("verificaciondigitalcdmx").insert({
            "username": username,
            "password": password,
            "rol": "cliente",
            "parent_id": distribuidor_id,
            "folios_asignac": folios_asignados,
            "folios_usados": 0
        }).execute()

        # 4️⃣ Descontar los folios del distribuidor
        supabase.table("verificaciondigitalcdmx").update({
            "folios_usados": datos_distribuidor[0]['folios_usados'] + folios_asignados
        }).eq("id", distribuidor_id).execute()

        flash('Cliente creado exitosamente.', 'success')
        return redirect(url_for('crear_cliente'))

    return render_template('crear_cliente.html')

@app.route('/crear_usuario_hijo', methods=['GET', 'POST'])
def crear_usuario_hijo():
    if 'user' not in session:
        return redirect(url_for('login'))

    # Verifica que sea distribuidor
    user_data = supabase.table("usuarios").select("*").eq("nombre_de_usuario", session["user"]).execute().data
    if not user_data or user_data[0].get("rol") != "distribuidor":
        return "No autorizado"

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        folios = request.form['folios']

        supabase.table("usuarios").insert({
            "nombre_de_usuario": username,
            "contraseña": password,
            "folios_asignac": int(folios),
            "folios_usados": 0,
            "rol": "vendedor",
            "id_padre": user_data[0]["id"]
        }).execute()

        flash('Usuario creado exitosamente', 'success')
        return redirect(url_for('crear_usuario_hijo'))

    return render_template('crear_usuario_hijo.html')

# Agregar esta ruta antes del if __name__ == '__main__':

@app.route('/consulta/<folio>')
def consulta_qr(folio):
    folio = folio.strip().upper()
    resp = supabase.table("folios_registrados").select("*").eq("folio", folio).execute()
    
    if not resp.data:
        resultado = {"estado": "No encontrado", "folio": folio}
    else:
        reg = resp.data[0]
        from datetime import date
        fe = date.fromisoformat(reg['fecha_expedicion'])
        fv = date.fromisoformat(reg['fecha_vencimiento'])
        estado = "VIGENTE" if date.today() <= fv else "VENCIDO"
        resultado = {
            "estado": estado,
            "folio": folio,
            "fecha_expedicion": fe.strftime("%d/%m/%Y"),
            "fecha_vencimiento": fv.strftime("%d/%m/%Y"),
            "marca": reg['marca'],
            "linea": reg['linea'],
            "año": reg['anio'],
            "numero_serie": reg['numero_serie'],
            "numero_motor": reg['numero_motor'],
            "entidad": reg.get('entidad', 'EDOMEX')
        }
    
    return render_template("resultado_consulta.html", resultado=resultado)
    
if __name__ == '__main__':
    app.run(debug=True)

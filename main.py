from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
from datetime import datetime, timedelta
from supabase import create_client, Client
import fitz    # PyMuPDF para manipular PDFs
import os      # Para crear carpetas y guardar archivos

app = Flask(__name__)
app.secret_key = 'clave_muy_segura_123456'

SUPABASE_URL = "https://xsagwqepoljfsogusubw.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InhzYWd3cWVwb2xqZnNvZ3VzdWJ3Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDM5NjM3NTUsImV4cCI6MjA1OTUzOTc1NX0.NUixULn0m2o49At8j6X58UqbXre2O2_JStqzls_8Gws"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

@app.route('/')
def inicio():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        # Admin hardcodeado
        if username == 'Gsr89roja.' and password == 'serg890105':
            session['admin'] = True
            return redirect(url_for('admin'))

        # Usuario Supabase
        resp = supabase.table("verificaciondigitalcdmx") \
            .select("*") \
            .eq("username", username).eq("password", password).execute()
        if resp.data:
            session['user_id'] = resp.data[0]['id']
            session['username'] = resp.data[0]['username']
            return redirect(url_for('registro_usuario'))
        flash('Credenciales incorrectas', 'error')

    return render_template('login.html')

@app.route('/admin')
def admin():
    if 'admin' not in session:
        return redirect(url_for('login'))
    return render_template('panel.html')

@app.route('/crear_usuario', methods=['GET', 'POST'])
def crear_usuario():
    if 'admin' not in session:
        return redirect(url_for('login'))
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        folios = int(request.form['folios'])
        exists = supabase.table("verificaciondigitalcdmx") \
            .select("id").eq("username", username).execute()
        if exists.data:
            flash('Error: el usuario ya existe.', 'error')
        else:
            supabase.table("verificaciondigitalcdmx").insert({
                "username": username,
                "password": password,
                "folios_asignac": folios,
                "folios_usados": 0
            }).execute()
            flash('Usuario creado exitosamente.', 'success')
    return render_template('crear_usuario.html')

@app.route('/registro_usuario', methods=['GET', 'POST'])
def registro_usuario():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']

    if request.method == 'POST':
        folio        = request.form['folio']
        marca        = request.form['marca']
        linea        = request.form['linea']
        anio         = request.form['anio']
        numero_serie = request.form['serie']
        numero_motor = request.form['motor']
        vigencia     = int(request.form['vigencia'])

        # Validar existencia de folio
        if supabase.table("folios_registrados") \
           .select("*").eq("folio", folio).execute().data:
            flash('Error: el folio ya existe.', 'error')
            return redirect(url_for('registro_usuario'))

        # Validar folios disponibles
        ud = supabase.table("verificaciondigitalcdmx") \
            .select("folios_asignac,folios_usados") \
            .eq("id", user_id).execute().data
        if not ud:
            flash('No se pudo obtener info de usuario.', 'error')
            return redirect(url_for('registro_usuario'))
        info = ud[0]
        if info['folios_asignac'] - info['folios_usados'] <= 0:
            flash('No tienes folios disponibles.', 'error')
            return redirect(url_for('registro_usuario'))

        # Fechas
        fecha_expedicion  = datetime.now()
        fecha_vencimiento = fecha_expedicion + timedelta(days=vigencia)

        # Insertar en BD
        supabase.table("folios_registrados").insert({
            "folio": folio,
            "marca": marca,
            "linea": linea,
            "anio": anio,
            "numero_serie": numero_serie,
            "numero_motor": numero_motor,
            "fecha_expedicion": fecha_expedicion.isoformat(),
            "fecha_vencimiento": fecha_vencimiento.isoformat()
        }).execute()
        supabase.table("verificaciondigitalcdmx").update({
            "folios_usados": info['folios_usados'] + 1
        }).eq("id", user_id).execute()

        # Generación de PDF
        try:
            doc = fitz.open("labuena3.0.pdf")
            page = doc[0]
            # 4 fechas con coordenadas y fuentes
            page.insert_text((166, 178), fecha_expedicion.strftime("%d/%m/%Y"),
                             fontsize=19, fontname="helv", color=(0,0,0))
            page.insert_text((346, 178), fecha_expedicion.strftime("%d/%m/%Y"),
                             fontsize=19, fontname="helv", color=(0,0,0))
            page.insert_text((296, 383), fecha_expedicion.strftime("%d/%m/%Y"),
                             fontsize=12, fontname="helv", color=(0,0,0))
            page.insert_text((225, 590), fecha_expedicion.strftime("%d/%m/%Y"),
                             fontsize=26, fontname="helv", color=(0,0,0))
            # número de serie
            page.insert_text((256, 245), numero_serie,
                             fontsize=12, fontname="helv", color=(0,0,0))

            os.makedirs("documentos", exist_ok=True)
            salida = f"documentos/{folio}.pdf"
            doc.save(salida)
            doc.close()
        except Exception as e:
            flash(f"Error al generar PDF: {e}", 'error')
            return redirect(url_for('registro_usuario'))

        return render_template("exitoso.html",
                               folio=folio,
                               serie=numero_serie,
                               fecha_generacion=fecha_expedicion.strftime("%d/%m/%Y"))

    # GET
    folios_info = supabase.table("verificaciondigitalcdmx") \
        .select("folios_asignac,folios_usados") \
        .eq("id", user_id).execute().data[0]
    return render_template("registro_usuario.html", folios_info=folios_info)

@app.route('/registro_admin', methods=['GET', 'POST'])
def registro_admin():
    if 'admin' not in session:
        return redirect(url_for('login'))
    if request.method == 'POST':
        # Lógica similar a registro_usuario
        # ...
        # Genera PDF con 4 fechas y serie
        # ...
        return render_template("exitoso.html",
                               folio=folio,
                               serie=numero_serie,
                               fecha_generacion=fecha_expedicion.strftime("%d/%m/%Y"))
    return render_template('registro_admin.html')

@app.route('/descargar_pdf/<folio>')
def descargar_pdf(folio):
    return send_file(f"documentos/{folio}.pdf", as_attachment=True)

@app.route('/consulta_folio', methods=['GET','POST'])
def consulta_folio():
    # Implementación de consulta...
    return render_template("consulta_folio.html")

@app.route('/admin_folios')
def admin_folios():
    # Implementación admin folios...
    return render_template("admin_folios.html")

@app.route('/editar_folio/<folio>', methods=['GET','POST'])
def editar_folio(folio):
    # Implementación editar...
    return render_template("editar_folio.html")

@app.route('/eliminar_folio', methods=['POST'])
def eliminar_folio():
    # Implementación eliminar...
    return redirect(url_for('admin_folios'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True)

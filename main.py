from flask import Flask, render_template, request, redirect, url_for, flash, session
from datetime import datetime, timedelta
from supabase import create_client, Client

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

        if username == 'Gsr89roja.' and password == 'serg890105':
            session['admin'] = True
            return redirect(url_for('admin'))

        response = supabase.table("verificaciondigitalcdmx").select("*").eq("username", username).eq("password", password).execute()
        usuarios = response.data

        if usuarios:
            session['user_id'] = usuarios[0]['id']
            session['username'] = usuarios[0]['username']
            return redirect(url_for('registro_usuario'))
        else:
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

        existe = supabase.table("verificaciondigitalcdmx").select("id").eq("username", username).execute()
        if existe.data:
            flash('Error: el nombre de usuario ya existe.', 'error')
            return render_template('crear_usuario.html')

        data = {
            "username": username,
            "password": password,
            "folios_asignac": folios,
            "folios_usados": 0
        }
        supabase.table("verificaciondigitalcdmx").insert(data).execute()
        flash('Usuario creado exitosamente.', 'success')

    return render_template('crear_usuario.html')


@app.route('/registro_usuario', methods=['GET', 'POST'])
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

        existente = supabase.table("folios_registrados").select("*").eq("folio", folio).execute()
        if existente.data:
            flash('Error: el folio ya existe.', 'error')
            return redirect(url_for('registro_usuario'))

        usuario_data = supabase.table("verificaciondigitalcdmx").select("folios_asignac, folios_usados").eq("id", user_id).execute()
        if not usuario_data.data:
            flash("No se pudo obtener la información del usuario.", "error")
            return redirect(url_for('registro_usuario'))

        folios = usuario_data.data[0]
        restantes = folios['folios_asignac'] - folios['folios_usados']
        if restantes <= 0:
            flash("No tienes folios disponibles para registrar.", "error")
            return redirect(url_for('registro_usuario'))

        fecha_expedicion = datetime.now()
        fecha_vencimiento = fecha_expedicion + timedelta(days=vigencia)

        data = {
            "folio": folio,
            "marca": marca,
            "linea": linea,
            "anio": anio,
            "numero_serie": numero_serie,
            "numero_motor": numero_motor,
            "fecha_expedicion": fecha_expedicion.isoformat(),
            "fecha_vencimiento": fecha_vencimiento.isoformat()
        }

        supabase.table("folios_registrados").insert(data).execute()
        supabase.table("verificaciondigitalcdmx").update({
            "folios_usados": folios["folios_usados"] + 1
        }).eq("id", user_id).execute()
        flash("Folio registrado correctamente.", "success")
        return redirect(url_for('registro_usuario'))

    response = supabase.table("verificaciondigitalcdmx").select("folios_asignac, folios_usados").eq("id", user_id).execute()
    folios_info = response.data[0] if response.data else {}
    return render_template("registro_usuario.html", folios_info=folios_info)


@app.route('/registro_admin', methods=['GET', 'POST'])
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

        existente = supabase.table("folios_registrados").select("*").eq("folio", folio).execute()
        if existente.data:
            flash('Error: el folio ya existe.', 'error')
            return render_template('registro_admin.html')

        fecha_expedicion = datetime.now()
        fecha_vencimiento = fecha_expedicion + timedelta(days=vigencia)

        data = {
            "folio": folio,
            "marca": marca,
            "linea": linea,
            "anio": anio,
            "numero_serie": numero_serie,
            "numero_motor": numero_motor,
            "fecha_expedicion": fecha_expedicion.isoformat(),
            "fecha_vencimiento": fecha_vencimiento.isoformat()
        }

        supabase.table("folios_registrados").insert(data).execute()
        flash('Folio registrado correctamente.', 'success')

    return render_template('registro_admin.html')


@app.route('/admin_folios')
def admin_folios():
    if 'admin' not in session:
        return redirect(url_for('login'))

    response = supabase.table("folios_registrados").select("*").execute()
    folios = response.data
    return render_template("admin_folios.html", folios=folios)


@app.route('/eliminar_folio', methods=['POST'])
def eliminar_folio():
    if 'admin' not in session:
        return redirect(url_for('login'))

    folio = request.form['folio']
    supabase.table("folios_registrados").delete().eq("folio", folio).execute()
    flash('Folio eliminado correctamente.', 'success')
    return redirect(url_for('admin_folios'))


@app.route('/editar_folio/<folio>', methods=['GET', 'POST'])
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
            "fecha_vencimiento": request.form['fecha_vencimiento']
        }
        supabase.table("folios_registrados").update(data).eq("folio", folio).execute()
        flash("Folio actualizado correctamente.", "success")
        return redirect(url_for('admin_folios'))

    resultado = supabase.table("folios_registrados").select("*").eq("folio", folio).execute()
    if resultado.data:
        return render_template("editar_folio.html", folio=resultado.data[0])
    else:
        flash("Folio no encontrado.", "error")
        return redirect(url_for('admin_folios'))


@app.route('/consulta_folio', methods=['GET', 'POST'])
def consulta_folio():
    resultado = None
    if request.method == 'POST':
        folio = request.form['folio']
        response = supabase.table("folios_registrados").select("*").eq("folio", folio).execute()
        registros = response.data

        if not registros:
            resultado = {"estado": "No encontrado", "folio": folio}
        else:
            registro = registros[0]
            fecha_expedicion = datetime.fromisoformat(registro['fecha_expedicion'])
            fecha_vencimiento = datetime.fromisoformat(registro['fecha_vencimiento'])
            hoy = datetime.now()
            estado = "VIGENTE" if hoy <= fecha_vencimiento else "VENCIDO"

            resultado = {
                "estado": estado,
                "folio": folio,
                "fecha_expedicion": fecha_expedicion.strftime("%d/%m/%Y"),
                "fecha_vencimiento": fecha_vencimiento.strftime("%d/%m/%Y"),
                "marca": registro['marca'],
                "linea": registro['linea'],
                "año": registro['anio'],
                "numero_serie": registro['numero_serie'],
                "numero_motor": registro['numero_motor']
            }

        return render_template("resultado_consulta.html", resultado=resultado)

    return render_template("consulta_folio.html")


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


if __name__ == '__main__':
    app.run(debug=True)

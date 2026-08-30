from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import threading
import time
import uuid
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote_plus, urlparse


HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8000"))
CONTRASENA = os.environ.get("APP_PASSWORD", "cambia-esta-contrasena")
ARCHIVO_DATOS = Path(__file__).with_name("tareas_datos.json")
SESIONES = set()
PERSONAS = ("Montserrat", "Iñaki")
EMAIL_POR_PERSONA = {
    "Iñaki": os.environ.get("REMINDER_EMAIL_INAKI", "i.reymelloni@gmail.com"),
    "Montserrat": os.environ.get("REMINDER_EMAIL_MONTSERRAT", ""),
}
RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
EMAIL_REMITENTE = os.environ.get("RESEND_FROM_EMAIL", "onboarding@resend.dev")
RECORDATORIOS = Path(__file__).with_name("recordatorios_enviados.json")
ARCHIVO_HISTORIAL = Path(__file__).with_name("historial_datos.json")
ARCHIVO_AVISOS = Path(__file__).with_name("avisos_datos.json")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_ACTIVO = bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)
TAREAS_PREDEFINIDAS = {"baño": 7, "cocina": 3, "encerar piso": 14}


def supabase_request(endpoint, method="GET", payload=None, query=""):
    """Acceso REST opcional; la clave service role nunca se envía al navegador."""
    if not SUPABASE_ACTIVO:
        return None
    url = f"{SUPABASE_URL}/rest/v1/{endpoint}{query}"
    datos = json.dumps(payload).encode("utf-8") if payload is not None else None
    solicitud = Request(url, data=datos, method=method, headers={
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": "B" + "earer " + SUPABASE_SERVICE_ROLE_KEY,
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    })
    with urlopen(solicitud, timeout=10) as respuesta:
        contenido = respuesta.read()
        return json.loads(contenido.decode("utf-8")) if contenido else []


def cargar_datos_supabase():
    tareas = {}
    filas = supabase_request("tareas", query="?select=*&order=id.asc")
    subtareas = supabase_request("subtareas", query="?select=*&order=id.asc")
    for fila in filas or []:
        ultima = fila.get("ultima_realizacion")
        tareas[fila["nombre"]] = {
            "nombre": fila["nombre"], "intervalo": fila["intervalo_dias"],
            "ultima": datetime.fromisoformat(ultima.replace("Z", "+00:00")).replace(tzinfo=None) if ultima else datetime.now(),
            "realizada_por": fila.get("realizada_por"), "asignada_a": fila.get("asignada_a"),
            "temporal": not fila.get("es_recurrente", False), "subtareas": [], "_id": fila.get("id"),
        }
    for fila in subtareas or []:
        tarea = next((t for t in tareas.values() if t.get("_id") == fila.get("tarea_id")), None)
        if tarea is None:
            padre = next((t for t in tareas.values() if t["nombre"] == fila.get("tarea_nombre")), None)
        else:
            padre = tarea
        if padre:
            ultima = fila.get("ultima_realizacion")
            padre["subtareas"].append({"nombre": fila["nombre"], "dias": fila["intervalo_dias"],
                "ultima": datetime.fromisoformat(ultima.replace("Z", "+00:00")).replace(tzinfo=None) if ultima else None,
                "realizada_por": fila.get("realizada_por")})
    return tareas


def datos_predeterminados():
    ahora = datetime.now()
    return {
        nombre: {
            "nombre": nombre,
            "intervalo": intervalo,
            "ultima": ahora - timedelta(days=5 if nombre == "baño" else 2 if nombre == "cocina" else 10),
            "realizada_por": None,
            "asignada_a": None,
            "temporal": False,
            "subtareas": [],
        }
        for nombre, intervalo in TAREAS_PREDEFINIDAS.items()
    }


def cargar_historial():
    historial_local = []
    if ARCHIVO_HISTORIAL.exists():
        try:
            with ARCHIVO_HISTORIAL.open("r", encoding="utf-8") as archivo:
                historial_local = json.load(archivo)
        except (OSError, ValueError) as error:
            print(f"No se pudo leer el historial local: {error}")
    if not SUPABASE_ACTIVO:
        return historial_local
    try:
        filas = supabase_request("historial_tareas", query="?select=*&order=completada_en.desc")
        historial_remoto = [
            {"id": str(f.get("id", "")), "tarea": f["tarea_nombre"], "subtarea": f.get("subtarea_nombre"),
             "persona": f["completada_por"], "fecha": f["completada_en"],
             "snapshot": f.get("datos_anteriores")}
            for f in filas or []
        ]
        claves = {(e["tarea"], e.get("subtarea"), e["persona"], e["fecha"]) for e in historial_remoto}
        return historial_remoto + [
            e for e in historial_local
            if (e["tarea"], e.get("subtarea"), e["persona"], e["fecha"]) not in claves
        ]
    except (OSError, ValueError, HTTPError, URLError) as error:
        print(f"No se pudo leer el historial de Supabase: {error}")
        return historial_local


def registrar_historial(nombre, subtarea, persona, fecha, snapshot=None):
    evento = {"id": uuid.uuid4().hex, "tarea": nombre, "subtarea": subtarea,
              "persona": persona, "fecha": fecha.isoformat(), "snapshot": snapshot}
    if SUPABASE_ACTIVO:
        supabase_request("historial_tareas", method="POST", payload={
            "tarea_nombre": nombre, "subtarea_nombre": subtarea,
            "completada_por": persona, "completada_en": evento["fecha"],
            "datos_anteriores": snapshot,
        })
    historial = []
    if ARCHIVO_HISTORIAL.exists():
        try:
            with ARCHIVO_HISTORIAL.open("r", encoding="utf-8") as archivo:
                historial = json.load(archivo)
        except (OSError, ValueError) as error:
            print(f"No se pudo leer el historial local: {error}")
    historial.insert(0, evento)
    with ARCHIVO_HISTORIAL.open("w", encoding="utf-8") as archivo:
        json.dump(historial, archivo, indent=2, ensure_ascii=False)


def cargar_avisos():
    avisos_locales = []
    if ARCHIVO_AVISOS.exists():
        try:
            with ARCHIVO_AVISOS.open("r", encoding="utf-8") as archivo:
                avisos_locales = json.load(archivo)
        except (OSError, ValueError) as error:
            print(f"No se pudieron leer los avisos locales: {error}")
    if not SUPABASE_ACTIVO:
        return avisos_locales
    try:
        filas = supabase_request("avisos", query="?select=*&order=creado_en.desc")
        remotos = [{"id": str(f["id"]), "titulo": f["titulo"], "descripcion": f["descripcion"],
                    "creado_en": f["creado_en"]} for f in filas or []]
        claves = {(a["titulo"], a["descripcion"]) for a in remotos}
        return remotos + [a for a in avisos_locales if (a["titulo"], a["descripcion"]) not in claves]
    except (OSError, ValueError, HTTPError, URLError) as error:
        print(f"No se pudieron leer los avisos de Supabase: {error}")
        return avisos_locales


def guardar_avisos(avisos):
    with ARCHIVO_AVISOS.open("w", encoding="utf-8") as archivo:
        json.dump(avisos, archivo, indent=2, ensure_ascii=False)


def sincronizar_aviso(aviso):
    if SUPABASE_ACTIVO:
        supabase_request("avisos", method="POST", payload={
            "titulo": aviso["titulo"], "descripcion": aviso["descripcion"],
            "creado_en": aviso["creado_en"],
        })


def sincronizar_tarea(nombre):
    if not SUPABASE_ACTIVO:
        return
    tarea = tareas[nombre]
    filas = supabase_request("tareas", method="POST", payload={
        "nombre": nombre, "intervalo_dias": tarea["intervalo"],
        "es_recurrente": not tarea.get("temporal", False),
        "asignada_a": tarea.get("asignada_a"), "ultima_realizacion": tarea["ultima"].isoformat(),
        "realizada_por": tarea.get("realizada_por"),
    })
    if filas:
        tarea["_id"] = filas[0].get("id")
        for sub in tarea.get("subtareas", []):
            supabase_request("subtareas", method="POST", payload={
                "tarea_id": tarea["_id"], "nombre": sub["nombre"],
                "intervalo_dias": sub["dias"], "ultima_realizacion": None,
            })


def cargar_datos():
    predeterminadas = datos_predeterminados()
    if not ARCHIVO_DATOS.exists():
        return predeterminadas
    with ARCHIVO_DATOS.open("r", encoding="utf-8") as archivo:
        datos = json.load(archivo)
    if all(isinstance(fecha, str) for fecha in datos.values()):
        for nombre, fecha in datos.items():
            if nombre in predeterminadas:
                predeterminadas[nombre]["ultima"] = datetime.fromisoformat(fecha)
        return predeterminadas
    tareas = {}
    for nombre, dato in datos.items():
        tarea = dict(dato)
        tarea["temporal"] = tarea.get("temporal", nombre not in TAREAS_PREDEFINIDAS)
        tarea["asignada_a"] = tarea.get("asignada_a")
        tarea["ultima"] = datetime.fromisoformat(tarea["ultima"])
        for subtarea in tarea.get("subtareas", []):
            subtarea["ultima"] = datetime.fromisoformat(subtarea["ultima"]) if subtarea.get("ultima") else None
        tareas[nombre] = tarea
    return tareas


def guardar_datos(tareas):
    datos = {}
    for nombre, tarea in tareas.items():
        copia = dict(tarea)
        copia["ultima"] = copia["ultima"].isoformat()
        copia.setdefault("temporal", nombre not in TAREAS_PREDEFINIDAS)
        copia.setdefault("asignada_a", None)
        copia["subtareas"] = []
        for subtarea in tarea.get("subtareas", []):
            item = dict(subtarea)
            item["ultima"] = item["ultima"].isoformat() if item.get("ultima") else None
            copia["subtareas"].append(item)
        datos[nombre] = copia
    with ARCHIVO_DATOS.open("w", encoding="utf-8") as archivo:
        json.dump(datos, archivo, indent=2, ensure_ascii=False)


if SUPABASE_ACTIVO:
    try:
        tareas = cargar_datos_supabase()
        locales = cargar_datos()
        for nombre, tarea in locales.items():
            if nombre not in tareas:
                tareas[nombre] = tarea
                sincronizar_tarea(nombre)
    except (HTTPError, URLError, OSError, ValueError, KeyError):
        print("Supabase no disponible; usando almacenamiento JSON local.")
        tareas = cargar_datos()
else:
    tareas = cargar_datos()


def enviar_recordatorio(asunto, mensaje, destinatario):
    if not RESEND_API_KEY or not destinatario:
        return
    contenido = json.dumps({
        "from": EMAIL_REMITENTE,
        "to": [destinatario],
        "subject": asunto,
        "text": mensaje,
    }).encode("utf-8")
    solicitud = Request(
        "https://api.resend.com/emails",
        data=contenido,
        headers={"Authorization": "B" + "earer " + RESEND_API_KEY, "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(solicitud, timeout=15):
        pass


def revisar_recordatorios():
    while True:
        try:
            hoy = datetime.now().date()
            enviados = {}
            if RECORDATORIOS.exists():
                with RECORDATORIOS.open("r", encoding="utf-8") as archivo:
                    enviados = json.load(archivo)
            cambio = False
            for tarea in tareas.values():
                elementos = [(tarea["nombre"], tarea["ultima"], tarea["intervalo"], tarea.get("asignada_a"))]
                elementos.extend(
                    (f"{tarea['nombre']} - {sub['nombre']}", sub["ultima"] or tarea["ultima"], sub["dias"], tarea.get("asignada_a"))
                    for sub in tarea.get("subtareas", [])
                )
                for nombre, ultima, intervalo, responsable in elementos:
                    vencimiento = (ultima + timedelta(days=intervalo)).date()
                    dias = (vencimiento - hoy).days
                    if dias not in (0, 1):
                        continue
                    clave = f"{nombre}:{vencimiento.isoformat()}:{dias}"
                    if clave in enviados:
                        continue
                    cuando = "hoy" if dias == 0 else "mañana"
                    destinatario = EMAIL_POR_PERSONA.get(responsable or "Iñaki", "")
                    if not destinatario:
                        continue
                    enviar_recordatorio(
                        f"Recordatorio: {nombre} vence {cuando}",
                        f"La tarea '{nombre}' asignada a {responsable or 'la casa'} vence {cuando} ({vencimiento:%d/%m/%Y}).",
                        destinatario,
                    )
                    enviados[clave] = datetime.now().isoformat()
                    cambio = True
            if cambio:
                with RECORDATORIOS.open("w", encoding="utf-8") as archivo:
                    json.dump(enviados, archivo, indent=2, ensure_ascii=False)
        except (OSError, ValueError, TimeoutError, HTTPError, URLError) as error:
            print(f"No se pudieron revisar los recordatorios: {error}")
        time.sleep(3600)


if RESEND_API_KEY:
    threading.Thread(target=revisar_recordatorios, daemon=True).start()


def fecha_tarea(ultima, intervalo):
    proxima = ultima + timedelta(days=intervalo)
    dias = (proxima - datetime.now()).days
    return proxima, dias


def obtener_tareas():
    resultado = []
    for tarea in tareas.values():
        proxima, dias = fecha_tarea(tarea["ultima"], tarea["intervalo"])
        subtareas = []
        for subtarea in tarea.get("subtareas", []):
            subproxima, subdias = fecha_tarea(subtarea["ultima"] or tarea["ultima"], subtarea["dias"])
            subtareas.append({
                "nombre": subtarea["nombre"], "dias": subtarea["dias"],
                "proxima": subproxima.strftime("%d/%m/%Y"), "dias_restantes": subdias,
                "realizada_por": subtarea.get("realizada_por"),
            })
        resultado.append({
            "nombre": tarea["nombre"], "intervalo": tarea["intervalo"],
            "ultima": tarea["ultima"].strftime("%d/%m/%Y"), "proxima": proxima.strftime("%d/%m/%Y"),
            "dias_restantes": dias, "porcentaje": max(0, min(100, round(dias / tarea["intervalo"] * 100))),
            "estado": "pendiente" if dias < 0 else "al día", "realizada_por": tarea.get("realizada_por"),
            "temporal": tarea.get("temporal", False), "asignada_a": tarea.get("asignada_a"),
            "subtareas": subtareas,
        })
    return resultado


HTML = """<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#2563eb"><link rel="manifest" href="/manifest.json"><title>Control de limpieza</title>
<style>
:root{font-family:system-ui,-apple-system,sans-serif;color:#172033}*{box-sizing:border-box}
body{margin:0;background:#f1f5f9}header{padding:28px 16px;color:#fff;background:linear-gradient(135deg,#1d4ed8,#2563eb)}
header h1,header p,main{max-width:760px;margin-left:auto;margin-right:auto}header h1{margin-top:0;margin-bottom:5px}
main{padding:18px 14px 36px}.mensaje{min-height:24px;color:#166534;font-weight:600}.lista{display:grid;gap:14px}
.tarjeta,.formulario{padding:18px;border:1px solid #e2e8f0;border-radius:16px;background:#fff;box-shadow:0 5px 16px #0f172a0c}
.topbar{max-width:760px;margin:auto;display:flex;align-items:center;justify-content:space-between;gap:18px}.topbar select{width:auto;margin:0;background:#ffffff22;color:#fff;border:1px solid #ffffff66}.calendario-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}.calendario-grid div{padding:12px;border-radius:12px;background:#f8fafc;border:1px solid #e2e8f0}.calendario-grid small{color:#64748b}.calendario-grid button{min-height:36px;margin-top:8px;font-size:.82rem}.aviso{padding:14px;margin-top:10px;border-radius:12px;background:#fff7ed;border:1px solid #fed7aa}.aviso h3{margin:0 0 5px}.aviso p{margin:0;color:#475569}
.encabezado{display:flex;justify-content:space-between;gap:12px}.encabezado h2{margin:0;text-transform:capitalize}
.estado{padding:5px 9px;border-radius:999px;background:#dcfce7;color:#166534;font-size:.78rem;font-weight:700;white-space:nowrap}.vencida{background:#fee2e2;color:#991b1b}
.barra{height:10px;margin:15px 0 10px;overflow:hidden;border-radius:999px;background:#e2e8f0}.relleno{height:100%;background:#22c55e}.relleno.vencida{background:#ef4444}
.datos{display:grid;grid-template-columns:1fr 1fr;gap:8px;color:#475569;font-size:.9rem}.datos strong{display:block;color:#172033}
button,input{width:100%;min-height:46px;margin-top:12px;padding:10px;border-radius:10px;font:inherit}button{border:0;background:#2563eb;color:#fff;font-weight:700}
button.secundario{border:1px solid #cbd5e1;background:#fff;color:#334155}.confirmacion{padding:12px;border-radius:12px;background:#eff6ff;color:#1e3a8a}
.formulario h2{margin-top:0}.formulario label{display:block;margin-top:10px;font-weight:600}.subtarea{display:grid;grid-template-columns:1fr 100px;gap:8px}.subtarea button{grid-column:span 2;margin-top:0}
.sublista{margin:14px 0 0;padding:0;list-style:none}.sublista li{padding:8px 0;border-top:1px solid #e2e8f0;font-size:.9rem}
@media(min-width:650px){.lista{grid-template-columns:repeat(2,1fr)}.tarjeta:last-child{grid-column:span 2}}
</style></head>
<body><header><div class="topbar"><div><h1>Control de limpieza</h1><p>Organiza tus tareas del hogar.</p></div><select id="vista" aria-label="Cambiar vista"><option value="tareas">Tareas</option><option value="historial">Calendario e historial</option><option value="avisos">Avisos</option></select></div></header>
<main><div id="mensaje" class="mensaje" aria-live="polite"></div>
<section class="formulario"><h2>Añadir tarea</h2><label>Nombre<input id="nuevo-nombre" placeholder="Ej.: Lavar la ropa"></label>
<label>Cada cuántos días<input id="nuevo-intervalo" type="number" min="1" value="7"></label>
<label>¿Para quién es?<select id="nuevo-responsable"><option>Montserrat</option><option>Iñaki</option></select></label>
<div id="subtareas"></div><button type="button" class="secundario" id="agregar-subtarea">+ Añadir subtarea</button>
<button type="button" id="guardar-tarea">Guardar tarea</button></section>
<section id="lista" class="lista">Cargando...</section><section id="panel-historial" class="formulario" hidden><h2>Calendario e historial</h2><div id="calendario" class="calendario"></div><ul id="historial" class="sublista"></ul></section>
<section id="panel-avisos" class="formulario" hidden><h2>Avisos</h2><label>Título<input id="aviso-titulo" placeholder="Ej.: Comprar detergente"></label><label>Descripción<textarea id="aviso-descripcion" rows="4" placeholder="Escribe el aviso"></textarea></label><button type="button" id="guardar-aviso">Guardar aviso</button><div id="avisos"></div></section></main>
<script>
const lista=document.querySelector("#lista"),mensaje=document.querySelector("#mensaje"),subtareas=document.querySelector("#subtareas"),panel=document.querySelector("#panel-historial"),panelAvisos=document.querySelector("#panel-avisos");
const escapeHtml=v=>String(v).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"}[c]));
function agregarCampoSubtarea(){const fila=document.createElement("div");fila.className="subtarea";fila.innerHTML='<input placeholder="Ej.: Colgar la ropa" class="sub-nombre"><input type="number" min="1" value="3" class="sub-dias">';subtareas.append(fila)}
document.querySelector("#agregar-subtarea").onclick=agregarCampoSubtarea;
async function cargarTareas(){const r=await fetch("/api/tareas");if(!r.ok)throw Error("No se pudieron cargar las tareas.");const datos=await r.json();
lista.innerHTML=datos.map(t=>{const vencida=t.dias_restantes<0;return `<article class="tarjeta"><div class="encabezado"><h2>${escapeHtml(t.nombre)}</h2><span class="estado ${vencida?"vencida":""}">${t.estado}</span></div>
<div class="barra"><div class="relleno ${vencida?"vencida":""}" style="width:${t.porcentaje}%"></div></div><div class="datos"><div>Última limpieza<strong>${t.ultima}</strong></div><div>Próxima limpieza<strong>${t.proxima}</strong></div></div>
${t.asignada_a?`<small>Asignada a: ${escapeHtml(t.asignada_a)}</small>`:""}${t.realizada_por?`<small> · Última vez: ${escapeHtml(t.realizada_por)}</small>`:""}${t.subtareas.length?`<ul class="sublista">${t.subtareas.map(s=>`<li><strong>${escapeHtml(s.nombre)}</strong> · ${s.dias_restantes<0?"vencida":"faltan "+s.dias_restantes+" día(s)"}${s.realizada_por?" · "+escapeHtml(s.realizada_por):""}<button type="button" data-sub="${escapeHtml(t.nombre)}" data-subnombre="${escapeHtml(s.nombre)}">Completar subtarea</button></li>`).join("")}</ul>`:""}
<button type="button" class="activar" data-tarea="${escapeHtml(t.nombre)}">Marcar como hecha</button><div class="confirmacion" hidden><p>¿Seguro que quieres marcar la tarea como hecha?</p><label>¿Quién la hizo?<select data-persona><option>Montserrat</option><option>Iñaki</option></select></label><button type="button" data-confirmar="${escapeHtml(t.nombre)}">Marcar como hecha</button><button type="button" class="secundario cancelar">Volver</button></div></article>`}).join("");
document.querySelectorAll(".activar").forEach(b=>b.onclick=()=>{b.hidden=true;b.nextElementSibling.hidden=false});
document.querySelectorAll(".cancelar").forEach(b=>b.onclick=()=>{b.parentElement.hidden=true;b.parentElement.previousElementSibling.hidden=false});
document.querySelectorAll("[data-confirmar]").forEach(b=>b.onclick=()=>completar(b.dataset.confirmar,null,b));
document.querySelectorAll("[data-sub]").forEach(b=>b.onclick=()=>completar(b.dataset.sub,b.dataset.subnombre,b));}
async function completar(nombre,subnombre,boton){const tarjeta=boton.closest(".tarjeta"),persona=tarjeta.querySelector("[data-persona]")?.value||(subnombre?prompt("¿Quién hizo la subtarea? Escribe Montserrat o Iñaki:","Montserrat"):null);if(!["Montserrat","Iñaki"].includes(persona)){mensaje.textContent="Elige Montserrat o Iñaki.";return}boton.disabled=true;
const r=await fetch("/api/completar",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({nombre,subnombre,persona})});boton.disabled=false;
if(!r.ok){mensaje.textContent="No se pudo guardar el cambio.";return}mensaje.textContent="Cambio guardado.";await cargarTareas()}
document.querySelector("#guardar-tarea").onclick=async()=>{const nombre=document.querySelector("#nuevo-nombre").value.trim(),intervalo=Number(document.querySelector("#nuevo-intervalo").value);
const responsable=document.querySelector("#nuevo-responsable").value;
const subs=[...document.querySelectorAll(".subtarea")].map(f=>({nombre:f.querySelector(".sub-nombre").value.trim(),dias:Number(f.querySelector(".sub-dias").value)})).filter(s=>s.nombre);
if(!nombre||!intervalo){mensaje.textContent="Indica un nombre y un plazo válido.";return}const r=await fetch("/api/tareas",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({nombre,intervalo,responsable,subtareas:subs})});
if(!r.ok){mensaje.textContent=(await r.text())||"No se pudo crear la tarea.";return}document.querySelector("#nuevo-nombre").value="";subtareas.innerHTML="";mensaje.textContent="Tarea añadida.";await cargarTareas()};
async function cargarHistorial(){const r=await fetch("/api/historial");if(!r.ok)throw Error("No se pudo cargar el historial.");const datos=await r.json();document.querySelector("#calendario").innerHTML=datos.length?`<div class="calendario-grid">${datos.slice(0,30).map(e=>`<div><strong>${new Date(e.fecha).toLocaleDateString("es-ES",{day:"2-digit",month:"short"})}</strong><br>${escapeHtml(e.tarea)}${e.subtarea?" · "+escapeHtml(e.subtarea):""}<small> · ${escapeHtml(e.persona)}</small><button type="button" data-deshacer="${escapeHtml(e.id||"")}">Deshacer</button></div>`).join("")}</div>`:"<p>Aún no hay completados.</p>";document.querySelectorAll("[data-deshacer]").forEach(b=>b.onclick=()=>deshacer(b.dataset.deshacer));document.querySelector("#historial").innerHTML=datos.map(e=>`<li>${new Date(e.fecha).toLocaleString("es-ES")} · <strong>${escapeHtml(e.tarea)}</strong>${e.subtarea?" / "+escapeHtml(e.subtarea):""} · ${escapeHtml(e.persona)}</li>`).join("")}
async function deshacer(id){if(!id){mensaje.textContent="Este registro antiguo no se puede deshacer.";return}if(!confirm("¿Seguro que quieres deshacer esta actividad?"))return;const r=await fetch("/api/historial/deshacer",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({id})});if(!r.ok){mensaje.textContent="No se pudo deshacer.";return}mensaje.textContent="Actividad deshecha.";await cargarHistorial();await cargarTareas()}
async function cargarAvisos(){const r=await fetch("/api/avisos");const datos=await r.json();document.querySelector("#avisos").innerHTML=datos.length?datos.map(a=>`<article class="aviso"><h3>${escapeHtml(a.titulo)}</h3><p>${escapeHtml(a.descripcion)}</p></article>`).join(""):"<p>No hay avisos.</p>"}
document.querySelector("#guardar-aviso").onclick=async()=>{const titulo=document.querySelector("#aviso-titulo").value.trim(),descripcion=document.querySelector("#aviso-descripcion").value.trim();if(!titulo||!descripcion){mensaje.textContent="Escribe un título y una descripción.";return}const r=await fetch("/api/avisos",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({titulo,descripcion})});if(!r.ok){mensaje.textContent="No se pudo guardar el aviso.";return}document.querySelector("#aviso-titulo").value="";document.querySelector("#aviso-descripcion").value="";mensaje.textContent="Aviso guardado.";await cargarAvisos()}
document.querySelector("#vista").onchange=async e=>{const vista=e.target.value, historial=vista==="historial",avisos=vista==="avisos";lista.hidden=historial||avisos;document.querySelector(".formulario").hidden=historial||avisos;panel.hidden=!historial;panelAvisos.hidden=!avisos;if(historial)try{await cargarHistorial()}catch(error){mensaje.textContent=error.message}if(avisos)await cargarAvisos()};
cargarTareas().catch(e=>lista.textContent=e.message);
</script></body></html>"""

LOGIN_HTML = """<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Acceso</title></head>
<body style="font-family:system-ui;display:grid;place-items:center;min-height:100vh;background:#f1f5f9"><form method="post" action="/login" style="padding:24px;background:white;border-radius:16px;width:min(360px,calc(100% - 32px))"><h1>Control de limpieza</h1><label>Contraseña<input name="password" type="password" required autofocus style="display:block;width:100%;min-height:44px;margin-top:8px;box-sizing:border-box"></label><button style="width:100%;min-height:44px;margin-top:16px;background:#2563eb;color:white;border:0;border-radius:8px">Entrar</button></form></body></html>"""
MANIFEST = json.dumps({"name":"Control de limpieza","short_name":"Limpieza","start_url":"/","display":"standalone","background_color":"#f1f5f9","theme_color":"#2563eb","icons":[]})


class Solicitudes(BaseHTTPRequestHandler):
    def autenticado(self):
        cookie = self.headers.get("Cookie", "")
        return any(p.strip() == f"sesion={s}" for p in cookie.split(";") for s in SESIONES)

    def enviar(self, contenido, tipo="text/html; charset=utf-8", estado=200):
        contenido = contenido.encode("utf-8") if isinstance(contenido, str) else contenido
        self.send_response(estado); self.send_header("Content-Type", tipo); self.send_header("Content-Length", str(len(contenido))); self.end_headers(); self.wfile.write(contenido)

    def do_GET(self):
        ruta=urlparse(self.path).path
        if ruta=="/manifest.json": self.enviar(MANIFEST,"application/manifest+json; charset=utf-8"); return
        if ruta=="/health":
            estado = {"ok": True, "supabase_configurado": SUPABASE_ACTIVO}
            if SUPABASE_ACTIVO:
                try:
                    supabase_request("tareas", query="?select=id&limit=1")
                    supabase_request("avisos", query="?select=id&limit=1")
                    estado["supabase_conectado"] = True
                except (HTTPError, URLError, OSError, ValueError) as error:
                    print(f"Health check de Supabase falló: {error}")
                    estado["supabase_conectado"] = False
            self.enviar(json.dumps(estado), "application/json; charset=utf-8")
            return
        if not self.autenticado(): self.enviar(LOGIN_HTML); return
        if ruta=="/": self.enviar(HTML)
        elif ruta=="/api/tareas": self.enviar(json.dumps(obtener_tareas(),ensure_ascii=False),"application/json; charset=utf-8")
        elif ruta=="/api/historial": self.enviar(json.dumps(cargar_historial(),ensure_ascii=False),"application/json; charset=utf-8")
        elif ruta=="/api/avisos": self.enviar(json.dumps(cargar_avisos(),ensure_ascii=False),"application/json; charset=utf-8")
        else: self.enviar("No encontrado",estado=404)

    def leer_json(self):
        longitud=int(self.headers.get("Content-Length","0")); return json.loads(self.rfile.read(longitud).decode("utf-8"))

    def do_POST(self):
        ruta=urlparse(self.path).path
        if ruta=="/login":
            datos=parse_qs(self.rfile.read(int(self.headers.get("Content-Length","0"))).decode("utf-8"))
            if datos.get("password",[""])[0]!=CONTRASENA: self.enviar(LOGIN_HTML,estado=401); return
            sesion=secrets.token_urlsafe(32); SESIONES.add(sesion); self.send_response(303); self.send_header("Location","/"); self.send_header("Set-Cookie",f"sesion={sesion}; HttpOnly; SameSite=Lax"); self.end_headers(); return
        if not self.autenticado(): self.enviar("No autorizado",estado=401); return
        try: dato=self.leer_json()
        except (json.JSONDecodeError, ValueError): self.enviar("Datos inválidos",estado=400); return
        if ruta=="/api/tareas":
            nombre=str(dato.get("nombre","")).strip()
            try:
                intervalo=int(dato.get("intervalo",0))
            except (TypeError, ValueError):
                self.enviar("El intervalo debe ser un número",estado=400); return
            if not nombre or intervalo<1 or nombre in tareas: self.enviar("Nombre inválido o ya existente",estado=400); return
            responsable=dato.get("responsable")
            if responsable not in PERSONAS: self.enviar("Responsable inválido",estado=400); return
            tareas[nombre]={"nombre":nombre,"intervalo":intervalo,"ultima":datetime.now(),"realizada_por":None,"asignada_a":responsable,"temporal":True,"subtareas":[]}
            for sub in dato.get("subtareas",[]): 
                if sub.get("nombre") and int(sub.get("dias",0))>0: tareas[nombre]["subtareas"].append({"nombre":sub["nombre"],"dias":int(sub["dias"]),"ultima":None,"realizada_por":None})
            guardar_datos(tareas)
            try:
                sincronizar_tarea(nombre)
            except (HTTPError, URLError, OSError, ValueError) as error:
                del tareas[nombre]
                guardar_datos(tareas)
                print(f"No se pudo guardar la tarea en Supabase: {error}")
                self.enviar("No se pudo guardar la tarea en Supabase. Revisa las variables y tablas.", estado=502)
                return
            self.enviar(json.dumps({"ok":True}),"application/json"); return
        if ruta=="/api/completar":
            nombre=dato.get("nombre"); persona=dato.get("persona")
            if nombre not in tareas or persona not in PERSONAS: self.enviar("Datos inválidos",estado=400); return
            snapshot = None
            if tareas[nombre].get("temporal"):
                snapshot = dict(tareas[nombre])
                snapshot["ultima"] = snapshot["ultima"].isoformat()
                snapshot["subtareas"] = []
                for sub in tareas[nombre].get("subtareas", []):
                    copia = dict(sub)
                    copia["ultima"] = copia["ultima"].isoformat() if copia.get("ultima") else None
                    snapshot["subtareas"].append(copia)
            if dato.get("subnombre"):
                subtarea=next((s for s in tareas[nombre]["subtareas"] if s["nombre"]==dato["subnombre"]),None)
                if not subtarea: self.enviar("Subtarea no encontrada",estado=404); return
                ahora = datetime.now()
                subtarea["ultima"]=ahora; subtarea["realizada_por"]=persona
            else:
                ahora = datetime.now()
                if tareas[nombre].get("temporal"):
                    del tareas[nombre]
                else:
                    tareas[nombre]["ultima"]=ahora; tareas[nombre]["realizada_por"]=persona
            try:
                registrar_historial(nombre, dato.get("subnombre"), persona, ahora, snapshot)
            except (HTTPError, URLError, OSError, ValueError) as error:
                print(f"No se pudo guardar el historial: {error}")
                self.enviar("No se pudo guardar el historial en Supabase.", estado=502)
                return
            if SUPABASE_ACTIVO and nombre in tareas:
                try:
                    if dato.get("subnombre"):
                        supabase_request("subtareas", method="PATCH", payload={"ultima_realizacion": ahora.isoformat(), "realizada_por": persona},
                                         query=f"?tarea_id=eq.{tareas[nombre].get('_id')}&nombre=eq.{quote(dato['subnombre'])}")
                    else:
                        supabase_request("tareas", method="PATCH", payload={"ultima_realizacion": ahora.isoformat(), "realizada_por": persona},
                                         query=f"?nombre=eq.{quote(nombre)}")
                except (HTTPError, URLError, OSError, ValueError):
                    pass
            guardar_datos(tareas); self.enviar(json.dumps({"ok":True}),"application/json"); return
        if ruta=="/api/avisos":
            titulo=str(dato.get("titulo","")).strip()
            descripcion=str(dato.get("descripcion","")).strip()
            if not titulo or not descripcion:
                self.enviar("Título y descripción son obligatorios", estado=400); return
            avisos=cargar_avisos()
            avisos.insert(0, {"id": uuid.uuid4().hex, "titulo": titulo, "descripcion": descripcion,
                              "creado_en": datetime.now().isoformat()})
            guardar_avisos(avisos)
            try:
                sincronizar_aviso(avisos[0])
            except (HTTPError, URLError, OSError, ValueError) as error:
                avisos.pop(0)
                guardar_avisos(avisos)
                print(f"No se pudo guardar el aviso en Supabase: {error}")
                self.enviar("No se pudo guardar el aviso en Supabase. Revisa las variables y tablas.", estado=502)
                return
            self.enviar(json.dumps({"ok":True}), "application/json"); return
        if ruta=="/api/historial/deshacer":
            evento_id=dato.get("id")
            historial=cargar_historial()
            evento=next((e for e in historial if e.get("id")==evento_id), None)
            if not evento:
                self.enviar("Registro no encontrado", estado=404); return
            historial.remove(evento)
            if evento.get("snapshot"):
                restaurada = dict(evento["snapshot"])
                restaurada["ultima"] = datetime.fromisoformat(restaurada["ultima"])
                restaurada["subtareas"] = [
                    dict(sub, ultima=datetime.fromisoformat(sub["ultima"]) if sub.get("ultima") else None)
                    for sub in restaurada.get("subtareas", [])
                ]
                tareas[evento["tarea"]]=restaurada
            elif evento["tarea"] in tareas:
                tareas[evento["tarea"]]["ultima"]=datetime.now()-timedelta(days=tareas[evento["tarea"]]["intervalo"])
                tareas[evento["tarea"]]["realizada_por"]=None
            with ARCHIVO_HISTORIAL.open("w", encoding="utf-8") as archivo:
                json.dump(historial, archivo, indent=2, ensure_ascii=False)
            guardar_datos(tareas)
            if SUPABASE_ACTIVO and evento_id.isdigit():
                try:
                    supabase_request("historial_tareas", method="DELETE", query=f"?id=eq.{evento_id}")
                except (HTTPError, URLError, OSError, ValueError) as error:
                    print(f"No se pudo borrar el historial remoto: {error}")
            self.enviar(json.dumps({"ok":True}), "application/json"); return
        self.enviar("No encontrado",estado=404)

    def log_message(self, formato, *argumentos): print(f"{self.address_string()} - {formato % argumentos}")


if __name__=="__main__":
    servidor=ThreadingHTTPServer((HOST,PORT),Solicitudes); print(f"Aplicación disponible en http://localhost:{PORT}")
    try: servidor.serve_forever()
    except KeyboardInterrupt: pass
    finally: servidor.server_close()

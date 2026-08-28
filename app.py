from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
from urllib.parse import parse_qs, unquote_plus, urlparse


HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8000"))
CONTRASENA = os.environ.get("APP_PASSWORD", "cambia-esta-contrasena")
ARCHIVO_DATOS = Path(__file__).with_name("tareas_datos.json")
SESIONES = set()
PERSONAS = ("Montserrat", "Iñaki")
TAREAS_PREDEFINIDAS = {"baño": 7, "cocina": 3, "encerar piso": 14}


def cargar_datos():
    ahora = datetime.now()
    predeterminadas = {
        nombre: {
            "nombre": nombre,
            "intervalo": intervalo,
            "ultima": ahora - timedelta(days=5 if nombre == "baño" else 2 if nombre == "cocina" else 10),
            "realizada_por": None,
            "subtareas": [],
        }
        for nombre, intervalo in TAREAS_PREDEFINIDAS.items()
    }
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
        copia["subtareas"] = []
        for subtarea in tarea.get("subtareas", []):
            item = dict(subtarea)
            item["ultima"] = item["ultima"].isoformat() if item.get("ultima") else None
            copia["subtareas"].append(item)
        datos[nombre] = copia
    with ARCHIVO_DATOS.open("w", encoding="utf-8") as archivo:
        json.dump(datos, archivo, indent=2, ensure_ascii=False)


tareas = cargar_datos()


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
<body><header><h1>Control de limpieza</h1><p>Organiza tus tareas del hogar.</p></header>
<main><div id="mensaje" class="mensaje" aria-live="polite"></div>
<section class="formulario"><h2>Añadir tarea</h2><label>Nombre<input id="nuevo-nombre" placeholder="Ej.: Lavar la ropa"></label>
<label>Cada cuántos días<input id="nuevo-intervalo" type="number" min="1" value="7"></label>
<div id="subtareas"></div><button type="button" class="secundario" id="agregar-subtarea">+ Añadir subtarea</button>
<button type="button" id="guardar-tarea">Guardar tarea</button></section>
<section id="lista" class="lista">Cargando...</section></main>
<script>
const lista=document.querySelector("#lista"),mensaje=document.querySelector("#mensaje"),subtareas=document.querySelector("#subtareas");
const escapeHtml=v=>String(v).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"}[c]));
function agregarCampoSubtarea(){const fila=document.createElement("div");fila.className="subtarea";fila.innerHTML='<input placeholder="Ej.: Colgar la ropa" class="sub-nombre"><input type="number" min="1" value="3" class="sub-dias">';subtareas.append(fila)}
document.querySelector("#agregar-subtarea").onclick=agregarCampoSubtarea;
async function cargarTareas(){const r=await fetch("/api/tareas");if(!r.ok)throw Error("No se pudieron cargar las tareas.");const datos=await r.json();
lista.innerHTML=datos.map(t=>{const vencida=t.dias_restantes<0;return `<article class="tarjeta"><div class="encabezado"><h2>${escapeHtml(t.nombre)}</h2><span class="estado ${vencida?"vencida":""}">${t.estado}</span></div>
<div class="barra"><div class="relleno ${vencida?"vencida":""}" style="width:${t.porcentaje}%"></div></div><div class="datos"><div>Última limpieza<strong>${t.ultima}</strong></div><div>Próxima limpieza<strong>${t.proxima}</strong></div></div>
${t.realizada_por?`<small>Última vez: ${escapeHtml(t.realizada_por)}</small>`:""}${t.subtareas.length?`<ul class="sublista">${t.subtareas.map(s=>`<li><strong>${escapeHtml(s.nombre)}</strong> · ${s.dias_restantes<0?"vencida":"faltan "+s.dias_restantes+" día(s)"}${s.realizada_por?" · "+escapeHtml(s.realizada_por):""}<button type="button" data-sub="${escapeHtml(t.nombre)}" data-subnombre="${escapeHtml(s.nombre)}">Completar subtarea</button></li>`).join("")}</ul>`:""}
<button type="button" class="activar" data-tarea="${escapeHtml(t.nombre)}">Marcar como hecha</button><div class="confirmacion" hidden><p>¿Seguro que quieres marcar la tarea como hecha?</p><label>¿Quién la hizo?<select data-persona><option>Montserrat</option><option>Iñaki</option></select></label><button type="button" data-confirmar="${escapeHtml(t.nombre)}">Marcar como hecha</button><button type="button" class="secundario cancelar">Volver</button></div></article>`}).join("");
document.querySelectorAll(".activar").forEach(b=>b.onclick=()=>{b.hidden=true;b.nextElementSibling.hidden=false});
document.querySelectorAll(".cancelar").forEach(b=>b.onclick=()=>{b.parentElement.hidden=true;b.parentElement.previousElementSibling.hidden=false});
document.querySelectorAll("[data-confirmar]").forEach(b=>b.onclick=()=>completar(b.dataset.confirmar,null,b));
document.querySelectorAll("[data-sub]").forEach(b=>b.onclick=()=>completar(b.dataset.sub,b.dataset.subnombre,b));}
async function completar(nombre,subnombre,boton){const tarjeta=boton.closest(".tarjeta"),persona=tarjeta.querySelector("[data-persona]")?.value||(subnombre?prompt("¿Quién hizo la subtarea? Escribe Montserrat o Iñaki:","Montserrat"):null);if(!["Montserrat","Iñaki"].includes(persona)){mensaje.textContent="Elige Montserrat o Iñaki.";return}boton.disabled=true;
const r=await fetch("/api/completar",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({nombre,subnombre,persona})});boton.disabled=false;
if(!r.ok){mensaje.textContent="No se pudo guardar el cambio.";return}mensaje.textContent="Cambio guardado.";await cargarTareas()}
document.querySelector("#guardar-tarea").onclick=async()=>{const nombre=document.querySelector("#nuevo-nombre").value.trim(),intervalo=Number(document.querySelector("#nuevo-intervalo").value);
const subs=[...document.querySelectorAll(".subtarea")].map(f=>({nombre:f.querySelector(".sub-nombre").value.trim(),dias:Number(f.querySelector(".sub-dias").value)})).filter(s=>s.nombre);
if(!nombre||!intervalo){mensaje.textContent="Indica un nombre y un plazo válido.";return}const r=await fetch("/api/tareas",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({nombre,intervalo,subtareas:subs})});
if(!r.ok){mensaje.textContent=(await r.text())||"No se pudo crear la tarea.";return}document.querySelector("#nuevo-nombre").value="";subtareas.innerHTML="";mensaje.textContent="Tarea añadida.";await cargarTareas()};
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
        if not self.autenticado(): self.enviar(LOGIN_HTML); return
        if ruta=="/": self.enviar(HTML)
        elif ruta=="/api/tareas": self.enviar(json.dumps(obtener_tareas(),ensure_ascii=False),"application/json; charset=utf-8")
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
            nombre=str(dato.get("nombre","")).strip(); intervalo=int(dato.get("intervalo",0))
            if not nombre or intervalo<1 or nombre in tareas: self.enviar("Nombre inválido o ya existente",estado=400); return
            tareas[nombre]={"nombre":nombre,"intervalo":intervalo,"ultima":datetime.now(),"realizada_por":None,"subtareas":[]}
            for sub in dato.get("subtareas",[]): 
                if sub.get("nombre") and int(sub.get("dias",0))>0: tareas[nombre]["subtareas"].append({"nombre":sub["nombre"],"dias":int(sub["dias"]),"ultima":None,"realizada_por":None})
            guardar_datos(tareas); self.enviar(json.dumps({"ok":True}),"application/json"); return
        if ruta=="/api/completar":
            nombre=dato.get("nombre"); persona=dato.get("persona")
            if nombre not in tareas or persona not in PERSONAS: self.enviar("Datos inválidos",estado=400); return
            if dato.get("subnombre"):
                subtarea=next((s for s in tareas[nombre]["subtareas"] if s["nombre"]==dato["subnombre"]),None)
                if not subtarea: self.enviar("Subtarea no encontrada",estado=404); return
                subtarea["ultima"]=datetime.now(); subtarea["realizada_por"]=persona
            else: tareas[nombre]["ultima"]=datetime.now(); tareas[nombre]["realizada_por"]=persona
            guardar_datos(tareas); self.enviar(json.dumps({"ok":True}),"application/json"); return
        self.enviar("No encontrado",estado=404)

    def log_message(self, formato, *argumentos): print(f"{self.address_string()} - {formato % argumentos}")


if __name__=="__main__":
    servidor=ThreadingHTTPServer((HOST,PORT),Solicitudes); print(f"Aplicación disponible en http://localhost:{PORT}")
    try: servidor.serve_forever()
    except KeyboardInterrupt: pass
    finally: servidor.server_close()

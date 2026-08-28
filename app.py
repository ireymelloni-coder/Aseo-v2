from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
from urllib.parse import unquote_plus, urlparse


HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8000"))
CONTRASENA = os.environ.get("APP_PASSWORD", "cambia-esta-contrasena")
ARCHIVO_DATOS = Path(__file__).with_name("tareas_datos.json")
SESIONES = set()

TAREAS_PREDEFINIDAS = {
    "baño": 7,
    "cocina": 3,
    "encerar piso": 14,
}


def cargar_datos():
    if not ARCHIVO_DATOS.exists():
        ahora = datetime.now()
        return {
            "baño": ahora - timedelta(days=5),
            "cocina": ahora - timedelta(days=2),
            "encerar piso": ahora - timedelta(days=10),
        }

    with ARCHIVO_DATOS.open("r", encoding="utf-8") as archivo:
        datos = json.load(archivo)

    return {
        tarea: datetime.fromisoformat(fecha)
        for tarea, fecha in datos.items()
        if tarea in TAREAS_PREDEFINIDAS
    }


def guardar_datos(ultima_limpieza):
    datos = {tarea: fecha.isoformat() for tarea, fecha in ultima_limpieza.items()}
    with ARCHIVO_DATOS.open("w", encoding="utf-8") as archivo:
        json.dump(datos, archivo, indent=2, ensure_ascii=False)


ultima_limpieza = cargar_datos()


def obtener_tareas():
    ahora = datetime.now()
    tareas = []
    for nombre, intervalo in TAREAS_PREDEFINIDAS.items():
        ultima = ultima_limpieza.get(nombre, ahora)
        proxima = ultima + timedelta(days=intervalo)
        dias_restantes = (proxima - ahora).days
        porcentaje = max(0, min(100, (dias_restantes / intervalo) * 100))
        tareas.append(
            {
                "nombre": nombre,
                "intervalo": intervalo,
                "ultima": ultima.strftime("%d/%m/%Y"),
                "proxima": proxima.strftime("%d/%m/%Y"),
                "dias_restantes": dias_restantes,
                "porcentaje": round(porcentaje),
                "estado": "pendiente" if dias_restantes < 0 else "al día",
            }
        )
    return tareas


HTML = """<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#2563eb">
  <link rel="manifest" href="/manifest.json">
  <title>Control de limpieza</title>
  <style>
    :root { color-scheme: light; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; background: #f1f5f9; color: #172033; }
    header { padding: 32px 20px 26px; color: white; background: linear-gradient(135deg, #1d4ed8, #2563eb); }
    header h1 { max-width: 760px; margin: 0 auto 6px; font-size: clamp(1.6rem, 5vw, 2.2rem); }
    header p { max-width: 760px; margin: 0 auto; opacity: .9; }
    main { width: min(760px, 100%); margin: 0 auto; padding: 20px 14px 36px; }
    .mensaje { min-height: 24px; margin: 0 4px 12px; color: #166534; font-weight: 600; }
    .lista { display: grid; gap: 14px; }
    .tarjeta { padding: 18px; border: 1px solid #e2e8f0; border-radius: 16px; background: white; box-shadow: 0 5px 16px #0f172a0c; }
    .encabezado { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
    h2 { margin: 0; font-size: 1.25rem; text-transform: capitalize; }
    .estado { padding: 5px 9px; border-radius: 999px; background: #dcfce7; color: #166534; font-size: .78rem; font-weight: 700; white-space: nowrap; }
    .estado.vencida { background: #fee2e2; color: #991b1b; }
    .barra { height: 10px; margin: 16px 0 10px; overflow: hidden; border-radius: 999px; background: #e2e8f0; }
    .relleno { height: 100%; border-radius: inherit; background: #22c55e; transition: width .25s ease; }
    .relleno.vencida { background: #ef4444; }
    .datos { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; color: #475569; font-size: .9rem; }
    .datos strong { display: block; color: #172033; font-size: .98rem; }
    button { width: 100%; min-height: 46px; margin-top: 16px; border: 0; border-radius: 10px; background: #2563eb; color: white; font: inherit; font-weight: 700; cursor: pointer; touch-action: manipulation; }
    button:active { background: #1d4ed8; transform: translateY(1px); }
    button.secundario { margin-top: 8px; border: 1px solid #cbd5e1; background: white; color: #334155; }
    button.secundario:active { background: #f8fafc; }
    .confirmacion { margin-top: 16px; padding: 14px; border: 1px solid #bfdbfe; border-radius: 12px; background: #eff6ff; }
    .confirmacion p { margin: 0; color: #1e3a8a; font-weight: 600; }
    @media (min-width: 650px) { .lista { grid-template-columns: repeat(2, 1fr); } .tarjeta:last-child { grid-column: span 2; } }
  </style>
</head>
<body>
  <header><h1>Control de limpieza</h1><p>Organiza tus tareas del hogar desde cualquier teléfono.</p></header>
  <main><div id="mensaje" class="mensaje" aria-live="polite"></div><section id="lista" class="lista">Cargando...</section></main>
  <script>
    const lista = document.querySelector("#lista");
    const mensaje = document.querySelector("#mensaje");
    const escapeHtml = (valor) => String(valor).replace(/[&<>"']/g, (caracter) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
    }[caracter]));

    async function cargarTareas() {
      const respuesta = await fetch("/api/tareas");
      if (!respuesta.ok) throw new Error("No se pudieron cargar las tareas.");
      const tareas = await respuesta.json();
      lista.innerHTML = tareas.map((tarea) => {
        const vencida = tarea.dias_restantes < 0;
        const textoDias = vencida
          ? `Vencida hace ${Math.abs(tarea.dias_restantes)} día(s)`
          : `Faltan ${tarea.dias_restantes} día(s)`;
        return `<article class="tarjeta">
          <div class="encabezado"><h2>${escapeHtml(tarea.nombre)}</h2>
            <span class="estado ${vencida ? "vencida" : ""}">${tarea.estado}</span></div>
          <div class="barra"><div class="relleno ${vencida ? "vencida" : ""}" style="width:${tarea.porcentaje}%"></div></div>
          <div class="datos"><div>Última limpieza<strong>${tarea.ultima}</strong></div>
            <div>Próxima limpieza<strong>${tarea.proxima}</strong></div></div>
          <button type="button" class="activar-confirmacion" data-tarea="${escapeHtml(tarea.nombre)}">Marcar como hecha</button>
          <div class="confirmacion" hidden>
            <p>¿Seguro que quieres marcar esta tarea como hecha?</p>
            <button type="button" data-confirmar="${escapeHtml(tarea.nombre)}">Marcar como hecha</button>
            <button type="button" class="secundario" data-cancelar>Volver</button>
          </div>
        </article>`;
      }).join("");
      document.querySelectorAll(".activar-confirmacion").forEach((boton) => {
        boton.addEventListener("click", () => {
          boton.hidden = true;
          boton.nextElementSibling.hidden = false;
        });
      });
      document.querySelectorAll("[data-cancelar]").forEach((boton) => {
        boton.addEventListener("click", () => {
          boton.parentElement.hidden = true;
          boton.parentElement.previousElementSibling.hidden = false;
        });
      });
      document.querySelectorAll("button[data-confirmar]").forEach((boton) => {
        boton.addEventListener("click", () => marcarTarea(boton.dataset.confirmar, boton));
      });
    }

    async function marcarTarea(nombre, boton) {
      boton.disabled = true;
      const respuesta = await fetch(`/api/tareas/${encodeURIComponent(nombre)}/completar`, { method: "POST" });
      boton.disabled = false;
      if (!respuesta.ok) { mensaje.textContent = "No se pudo guardar el cambio."; return; }
      mensaje.textContent = `"${nombre}" marcada como hecha hoy.`;
      await cargarTareas();
    }

    cargarTareas().catch((error) => { lista.textContent = error.message; });
  </script>
</body>
</html>"""

LOGIN_HTML = """<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#2563eb"><title>Acceso - Control de limpieza</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f1f5f9;font-family:system-ui,-apple-system,sans-serif;color:#172033}
form{width:min(360px,calc(100% - 32px));padding:24px;border-radius:16px;background:#fff;box-shadow:0 5px 16px #0f172a18}
h1{margin-top:0;font-size:1.5rem}label{display:block;margin:18px 0 6px;font-weight:600}
input,button{width:100%;min-height:46px;padding:10px;border:1px solid #cbd5e1;border-radius:10px;box-sizing:border-box;font:inherit}
button{margin-top:18px;border:0;background:#2563eb;color:#fff;font-weight:700}</style></head>
<body><form method="post" action="/login"><h1>Control de limpieza</h1>
<label for="password">Contraseña</label><input id="password" name="password" type="password" required autofocus>
<button type="submit">Entrar</button></form></body></html>"""

MANIFEST = json.dumps({
    "name": "Control de limpieza",
    "short_name": "Limpieza",
    "start_url": "/",
    "display": "standalone",
    "background_color": "#f1f5f9",
    "theme_color": "#2563eb",
    "icons": [],
}, ensure_ascii=False)


class Solicitudes(BaseHTTPRequestHandler):
    def autenticado(self):
        cookie = self.headers.get("Cookie", "")
        return any(parte.strip() == f"sesion={sesion}" for parte in cookie.split(";") for sesion in SESIONES)

    def enviar(self, contenido, tipo="text/html; charset=utf-8", estado=200):
        contenido = contenido.encode("utf-8") if isinstance(contenido, str) else contenido
        self.send_response(estado)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(contenido)))
        self.end_headers()
        self.wfile.write(contenido)

    def do_GET(self):
        ruta = urlparse(self.path).path
        if ruta == "/manifest.json":
            self.enviar(MANIFEST, "application/manifest+json; charset=utf-8")
            return
        if not self.autenticado():
            self.enviar(LOGIN_HTML)
            return
        if ruta == "/":
            self.enviar(HTML)
        elif ruta == "/api/tareas":
            self.enviar(json.dumps(obtener_tareas(), ensure_ascii=False), "application/json; charset=utf-8")
        else:
            self.enviar("No encontrado", estado=404)

    def do_POST(self):
        ruta = urlparse(self.path).path
        if ruta == "/login":
            longitud = int(self.headers.get("Content-Length", "0"))
            datos = self.rfile.read(longitud).decode("utf-8")
            password = dict(parte.split("=", 1) for parte in datos.split("&") if "=" in parte).get("password", "")
            if unquote_plus(password) != CONTRASENA:
                self.enviar(LOGIN_HTML.replace("</form>", "<p>Contraseña incorrecta.</p></form>"), estado=401)
                return
            sesion = secrets.token_urlsafe(32)
            SESIONES.add(sesion)
            self.send_response(303)
            self.send_header("Location", "/")
            secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
            self.send_header("Set-Cookie", f"sesion={sesion}; HttpOnly; SameSite=Lax{secure}")
            self.end_headers()
            return
        if not self.autenticado():
            self.enviar("No autorizado", estado=401)
            return
        prefijo = "/api/tareas/"
        if not ruta.startswith(prefijo) or not ruta.endswith("/completar"):
            self.enviar("No encontrado", estado=404)
            return

        tarea = unquote_plus(ruta[len(prefijo):-len("/completar")])
        if tarea not in TAREAS_PREDEFINIDAS:
            self.enviar("Tarea no encontrada", estado=404)
            return

        ultima_limpieza[tarea] = datetime.now()
        guardar_datos(ultima_limpieza)
        self.enviar(json.dumps({"ok": True}), "application/json; charset=utf-8")

    def log_message(self, formato, *argumentos):
        print(f"{self.address_string()} - {formato % argumentos}")


if __name__ == "__main__":
    servidor = ThreadingHTTPServer((HOST, PORT), Solicitudes)
    print(f"Aplicación disponible en http://localhost:{PORT}")
    print("Para abrirla desde el teléfono, usa la IP local de este equipo en la misma red Wi-Fi.")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor detenido.")
    finally:
        servidor.server_close()

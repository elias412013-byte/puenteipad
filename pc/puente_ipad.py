# -*- coding: utf-8 -*-
"""
PuenteiPad - lado PC (Windows)

Usa el iPad como segundo monitor por CABLE USB, con tactil.

  * Captura un monitor con ffmpeg y manda las imagenes al iPad por un tunel USB.
  * Los toques del iPad se convierten en mouse:
        tocar = clic          arrastrar = arrastrar / mover
        mantener apretado = clic derecho
        dos dedos = scroll (vertical y horizontal)

Uso (cmd, en esta carpeta):
    py puente_ipad.py

Opciones:
    py puente_ipad.py --monitor 1        elegir que monitor mostrar (la lista sale al iniciar)
    py puente_ipad.py --ancho 1024       menos datos = menos retraso
    py puente_ipad.py --ip 192.168.1.30  conectar por Wi-Fi en vez de USB (por si el USB falla)

Requisitos (una sola vez):
    1) iTunes instalado en la PC (o la app "Dispositivos Apple"), para el tunel USB.
    2) ffmpeg:   winget install Gyan.FFmpeg   (despues abrir otra ventana cmd)
    3) Para tener un SEGUNDO monitor de verdad: un driver de monitor virtual
       (si no, el iPad muestra una copia de tu monitor principal).
"""
import argparse
import ctypes
import os
import plistlib
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time

parser = argparse.ArgumentParser(description="PuenteiPad - lado PC")
parser.add_argument("--monitor", type=int, default=None, help="numero de monitor a mostrar")
parser.add_argument("--ancho", type=int, default=1280, help="ancho del video (default 1280)")
parser.add_argument("--fps", type=int, default=60, help="cuadros por segundo (default 60)")
parser.add_argument("--calidad", type=int, default=5, help="calidad JPEG: 2 (mejor) a 31 (peor)")
parser.add_argument("--puerto", type=int, default=7777, help="puerto de la app del iPad")
parser.add_argument("--ip", default=None, help="IP del iPad para conectar por Wi-Fi (en vez de USB)")
parser.add_argument("--captura", choices=["auto", "ddagrab", "gdigrab"], default="auto",
                    help="metodo de captura: auto (prueba el rapido primero)")
args = parser.parse_args()

if os.name != "nt":
    print("Este script es para Windows.")
    sys.exit(1)

user32 = ctypes.windll.user32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


# ------------------------------------------------------------ monitores
class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]


MonitorEnumProc = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                                     ctypes.POINTER(RECT), ctypes.c_void_p)
user32.EnumDisplayMonitors.argtypes = [ctypes.c_void_p, ctypes.c_void_p, MonitorEnumProc, ctypes.c_void_p]
user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]


def listar_monitores():
    mons = []

    def cb(hmon, hdc, rect, data):
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        user32.GetMonitorInfoW(hmon, ctypes.byref(info))
        r = info.rcMonitor
        mons.append({"x": r.left, "y": r.top, "w": r.right - r.left, "h": r.bottom - r.top,
                     "primario": bool(info.dwFlags & 1)})
        return 1

    proc = MonitorEnumProc(cb)
    user32.EnumDisplayMonitors(None, None, proc, 0)
    return mons


MONS = listar_monitores()
if not MONS:
    print("No pude leer los monitores.")
    input("Apreta Enter para salir")
    sys.exit(1)

if args.monitor is not None:
    IDX = args.monitor
else:
    secundarios = [i for i, m in enumerate(MONS) if not m["primario"]]
    IDX = secundarios[-1] if secundarios else 0
if IDX < 0 or IDX >= len(MONS):
    IDX = 0
MON = MONS[IDX]


def buscar_ffmpeg():
    aqui = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ffmpeg.exe")
    if os.path.exists(aqui):
        return aqui
    return shutil.which("ffmpeg")


FFMPEG = buscar_ffmpeg()
if not FFMPEG:
    print("No encontre ffmpeg.")
    print("Instalalo con:   winget install Gyan.FFmpeg")
    print("y despues cerra esta ventana y abri otra.")
    input("Apreta Enter para salir")
    sys.exit(1)


# ------------------------------------------------------------ video
class CajaDeCuadros:
    def __init__(self):
        self.cond = threading.Condition()
        self.ultimo = None
        self.numero = 0


caja = CajaDeCuadros()
estadisticas = {"cuadros": 0, "bytes": 0}


def publicar(cuadro):
    estadisticas["cuadros"] += 1
    estadisticas["bytes"] += len(cuadro)
    with caja.cond:
        caja.ultimo = cuadro
        caja.numero += 1
        caja.cond.notify_all()


def comando_ffmpeg(modo):
    base = [FFMPEG, "-hide_banner", "-loglevel", "error"]
    if modo == "ddagrab":
        entrada = ["-f", "lavfi", "-i",
                   "ddagrab=output_idx=%d:framerate=%d:draw_mouse=1" % (IDX, args.fps)]
        filtro = "hwdownload,format=bgra,scale=%d:-2:flags=fast_bilinear" % args.ancho
    else:
        entrada = ["-fflags", "nobuffer", "-f", "gdigrab", "-framerate", str(args.fps),
                   "-offset_x", str(MON["x"]), "-offset_y", str(MON["y"]),
                   "-video_size", "%dx%d" % (MON["w"], MON["h"]),
                   "-draw_mouse", "1", "-i", "desktop"]
        filtro = "scale=%d:-2:flags=fast_bilinear" % args.ancho
    return base + entrada + ["-vf", filtro, "-c:v", "mjpeg", "-q:v", str(args.calidad),
                             "-flush_packets", "1", "-f", "image2pipe", "pipe:1"]


def hilo_ffmpeg():
    modos = ["ddagrab", "gdigrab"] if args.captura == "auto" else [args.captura]
    indice = 0
    while True:
        modo = modos[indice]
        print("Captura de pantalla: %s" % modo)
        antes = estadisticas["cuadros"]
        try:
            proc = subprocess.Popen(comando_ffmpeg(modo), stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, bufsize=0)
        except OSError as e:
            print("No pude iniciar ffmpeg:", e)
            time.sleep(3)
            continue
        buf = bytearray()
        try:
            while True:
                trozo = proc.stdout.read(65536)
                if not trozo:
                    break
                buf += trozo
                while True:
                    ini = buf.find(b"\xff\xd8")
                    if ini < 0:
                        buf.clear()
                        break
                    fin = buf.find(b"\xff\xd9", ini + 2)
                    if fin < 0:
                        if ini > 0:
                            del buf[:ini]
                        break
                    publicar(bytes(buf[ini:fin + 2]))
                    del buf[:fin + 2]
        finally:
            try:
                proc.kill()
            except Exception:
                pass
        if estadisticas["cuadros"] == antes and indice + 1 < len(modos):
            indice += 1
            print("El metodo %s no funciono. Pruebo con %s..." % (modo, modos[indice]))
        else:
            print("ffmpeg se detuvo; reinicio en 2 s...")
            time.sleep(2)


def hilo_estadisticas():
    while True:
        c0, b0 = estadisticas["cuadros"], estadisticas["bytes"]
        time.sleep(5)
        c = estadisticas["cuadros"] - c0
        b = estadisticas["bytes"] - b0
        if c > 0:
            print("Video: %.0f cuadros/s, %.0f KB por cuadro, %.1f Mbps" % (c / 5.0, b / c / 1024.0, b * 8 / 5.0 / 1e6))
        else:
            print("Video: sin cuadros (ffmpeg anda?)")


# ------------------------------------------------------------ tunel USB (usbmuxd de Apple)
USBMUXD = ("127.0.0.1", 27015)


class MuxNoDisponible(Exception):
    pass


class AppCerrada(Exception):
    pass


def _leer(s, n):
    datos = b""
    while len(datos) < n:
        trozo = s.recv(n - len(datos))
        if not trozo:
            raise ConnectionError("conexion cerrada")
        datos += trozo
    return datos


def _mux_enviar(s, tag, mensaje):
    cuerpo = plistlib.dumps(mensaje)
    s.sendall(struct.pack("<IIII", 16 + len(cuerpo), 1, 8, tag) + cuerpo)


def _mux_recibir(s):
    largo, version, tipo, tag = struct.unpack("<IIII", _leer(s, 16))
    return plistlib.loads(_leer(s, largo - 16))


def mux_dispositivos():
    try:
        s = socket.create_connection(USBMUXD, timeout=3)
    except OSError:
        raise MuxNoDisponible()
    try:
        _mux_enviar(s, 1, {"MessageType": "ListDevices", "ClientVersionString": "puente_ipad",
                           "ProgName": "puente_ipad"})
        resp = _mux_recibir(s)
    finally:
        s.close()
    return resp.get("DeviceList", [])


def mux_conectar(device_id, puerto):
    try:
        s = socket.create_connection(USBMUXD, timeout=3)
    except OSError:
        raise MuxNoDisponible()
    _mux_enviar(s, 2, {"MessageType": "Connect", "ClientVersionString": "puente_ipad",
                       "ProgName": "puente_ipad", "DeviceID": device_id,
                       "PortNumber": ((puerto & 0xFF) << 8) | (puerto >> 8)})
    resp = _mux_recibir(s)
    if resp.get("Number", -1) != 0:
        s.close()
        raise AppCerrada()
    s.settimeout(None)
    return s


# ------------------------------------------------------------ toques -> mouse
L = threading.RLock()
toques = {}
scroll = {"activo": False, "acc_x": 0.0, "acc_y": 0.0}
PASO_SCROLL = 30.0    # pixeles de movimiento por cada "click" de rueda


def izq(abajo):
    user32.mouse_event(0x0002 if abajo else 0x0004, 0, 0, 0, 0)


def clic_derecho():
    user32.mouse_event(0x0008, 0, 0, 0, 0)
    user32.mouse_event(0x0010, 0, 0, 0, 0)


def rueda(v):
    user32.mouse_event(0x0800, 0, 0, int(v), 0)


def rueda_h(v):
    user32.mouse_event(0x1000, 0, 0, int(v), 0)


def a_pixel(nx, ny):
    nx = min(1.0, max(0.0, nx))
    ny = min(1.0, max(0.0, ny))
    return int(MON["x"] + nx * (MON["w"] - 1)), int(MON["y"] + ny * (MON["h"] - 1))


def cancelar_timers(t):
    for k in ("timer_p", "timer_l"):
        tm = t.get(k)
        if tm:
            tm.cancel()


def disparar_presion(tid):
    with L:
        t = toques.get(tid)
        if not t or t["presionado"] or t["consumido"] or scroll["activo"] or len(toques) != 1:
            return
        izq(True)
        t["presionado"] = True


def disparar_largo(tid):
    with L:
        t = toques.get(tid)
        if not t or t["consumido"] or scroll["activo"] or len(toques) != 1:
            return
        if t["presionado"]:
            izq(False)
            t["presionado"] = False
        clic_derecho()
        t["consumido"] = True


def soltar_todo():
    with L:
        for t in toques.values():
            cancelar_timers(t)
            if t["presionado"]:
                izq(False)
        toques.clear()
        scroll["activo"] = False


def procesar_toque(linea):
    p = linea.strip().split(",")
    if len(p) < 5 or p[0] != "T":
        return
    try:
        fase = p[1]
        tid = int(p[2])
        nx = float(p[3])
        ny = float(p[4])
    except ValueError:
        return
    px, py = a_pixel(nx, ny)
    with L:
        if fase == "D":
            t = {"x": px, "y": py, "x0": px, "y0": py, "presionado": False, "consumido": False}
            toques[tid] = t
            if len(toques) == 1:
                user32.SetCursorPos(px, py)
                t["timer_p"] = threading.Timer(0.05, disparar_presion, args=(tid,))
                t["timer_l"] = threading.Timer(0.6, disparar_largo, args=(tid,))
                t["timer_p"].start()
                t["timer_l"].start()
            else:
                # segundo dedo: pasa a modo scroll (sin clics accidentales)
                for o in toques.values():
                    cancelar_timers(o)
                    if o["presionado"]:
                        izq(False)
                        o["presionado"] = False
                    o["consumido"] = True
                scroll["activo"] = True
                scroll["acc_x"] = 0.0
                scroll["acc_y"] = 0.0
        elif fase == "M":
            t = toques.get(tid)
            if not t:
                return
            dx = px - t["x"]
            dy = py - t["y"]
            t["x"], t["y"] = px, py
            if scroll["activo"]:
                scroll["acc_x"] += dx / 2.0
                scroll["acc_y"] += dy / 2.0
                pasos_y = int(scroll["acc_y"] / PASO_SCROLL)
                if pasos_y:
                    rueda(pasos_y * 120)
                    scroll["acc_y"] -= pasos_y * PASO_SCROLL
                pasos_x = int(scroll["acc_x"] / PASO_SCROLL)
                if pasos_x:
                    rueda_h(-pasos_x * 120)
                    scroll["acc_x"] -= pasos_x * PASO_SCROLL
            elif not t["consumido"]:
                if abs(px - t["x0"]) > 6 or abs(py - t["y0"]) > 6:
                    cancelar_timers(t)
                    if not t["presionado"]:
                        izq(True)
                        t["presionado"] = True
                user32.SetCursorPos(px, py)
        elif fase == "U":
            t = toques.pop(tid, None)
            if not t:
                return
            cancelar_timers(t)
            if scroll["activo"]:
                if not toques:
                    scroll["activo"] = False
                return
            if t["presionado"]:
                izq(False)
            elif not t["consumido"]:
                user32.SetCursorPos(px, py)      # toque corto = clic
                izq(True)
                izq(False)


def recibir_toques(s):
    buf = b""
    while True:
        try:
            datos = s.recv(4096)
        except OSError:
            break
        if not datos:
            break
        buf += datos
        while b"\n" in buf:
            linea, buf = buf.split(b"\n", 1)
            try:
                procesar_toque(linea.decode("ascii", "ignore"))
            except Exception:
                pass
    try:
        s.close()
    except OSError:
        pass


# ------------------------------------------------------------ conexion con el iPad
def enviar_cuadros(s):
    ultimo = -1
    while True:
        with caja.cond:
            while caja.numero == ultimo:
                caja.cond.wait(timeout=1)
            cuadro = caja.ultimo
            ultimo = caja.numero
        s.sendall(struct.pack(">I", len(cuadro)) + cuadro)


_ultimo_aviso = [""]


def aviso(texto):
    if texto != _ultimo_aviso[0]:
        _ultimo_aviso[0] = texto
        print(texto)


def hilo_conexion():
    while True:
        try:
            if args.ip:
                s = socket.create_connection((args.ip, args.puerto), timeout=3)
            else:
                devs = mux_dispositivos()
                usb = [d for d in devs if d.get("Properties", {}).get("ConnectionType", "USB") == "USB"]
                if not usb:
                    aviso("No veo el iPad por USB. Conectalo con el cable, desbloquealo y toca 'Confiar'.")
                    time.sleep(2)
                    continue
                s = mux_conectar(usb[0]["DeviceID"], args.puerto)
        except MuxNoDisponible:
            aviso("No encuentro el servicio USB de Apple. Instala iTunes (o 'Dispositivos Apple') en la PC.")
            time.sleep(3)
            continue
        except AppCerrada:
            aviso("iPad conectado. Abri la app PuenteiPad en el iPad.")
            time.sleep(1)
            continue
        except OSError:
            aviso("Esperando al iPad...")
            time.sleep(2)
            continue

        try:
            s.settimeout(None)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        print("Conectado al iPad!")
        _ultimo_aviso[0] = ""
        threading.Thread(target=recibir_toques, args=(s,), daemon=True).start()
        try:
            enviar_cuadros(s)
        except (OSError, ConnectionError):
            pass
        try:
            s.close()
        except OSError:
            pass
        soltar_todo()
        print("Se perdio la conexion con el iPad. Reintento...")
        time.sleep(1)


def main():
    print("PuenteiPad - lado PC")
    print("Monitores detectados:")
    for i, m in enumerate(MONS):
        marca = "  <- el que se muestra en el iPad" if i == IDX else ""
        print("  %d: %dx%d en (%d,%d)%s%s" % (i, m["w"], m["h"], m["x"], m["y"],
                                              " (principal)" if m["primario"] else "", marca))
    if len(MONS) == 1:
        print("Solo hay un monitor: el iPad va a mostrar una COPIA de la pantalla principal.")
        print("Para un segundo monitor de verdad, instala un driver de monitor virtual.")
    print("Si el monitor equivocado se ve en el iPad, proba:  py puente_ipad.py --monitor N")
    print("Cerra esta ventana (o Ctrl+C) para salir.\n")
    threading.Thread(target=hilo_ffmpeg, daemon=True).start()
    threading.Thread(target=hilo_estadisticas, daemon=True).start()
    threading.Thread(target=hilo_conexion, daemon=True).start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        soltar_todo()
        print("Chau.")


if __name__ == "__main__":
    main()

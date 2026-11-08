# PuenteiPad

Usar un iPad (probado en la idea para iPad mini 2) como segundo monitor por cable USB, con tactil.

## Partes
- `PuenteiPad/` y `project.yml`: la app del iPad (se compila con GitHub Actions).
- `pc/puente_ipad.py`: el puente que corre en la PC con Windows.

## En la PC (una sola vez)
1. iTunes instalado (o la app "Dispositivos Apple" de la Microsoft Store). Lo necesita el tunel USB.
2. `winget install Gyan.FFmpeg` y abrir otra ventana cmd.
3. Para un SEGUNDO monitor de verdad: instalar un driver de monitor virtual y, en Configuracion de pantalla de Windows, elegir "Extender estas pantallas". Sin eso, el iPad muestra una copia de tu monitor principal.

## Cada vez
1. Conectar el iPad con el cable, desbloquearlo y tocar "Confiar".
2. Abrir la app PuenteiPad en el iPad.
3. En la PC, en la carpeta `pc`: `py puente_ipad.py`

## Tactil
- tocar = clic, arrastrar = arrastrar, mantener = clic derecho, dos dedos = scroll.

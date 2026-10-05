@echo off
:: SistemAlf -- Que mirar antes y despues de desplegar el modulo de accesos
::
:: SOLO LECTURA. No escribe nada, ni en la base ni en los equipos.
::
:: Se corre dos veces:
::   ANTES de actualizar  -> para saber si algo va a salir mal
::   DESPUES de reiniciar -> para confirmar que no salio
::
:: Lo que revisa, en orden de lo que mas duele si esta mal:
::   1. La IP del lector de asistencia. Es el unico punto donde este modulo
::      puede romper algo que hoy funciona.
::   2. Que ese lector no este marcado como puerta.
::   3. Que los fichajes sigan llegando.
::   4. Que el modulo nuevo lo vea solo el rol sistema.
::   5. Si las huellas ya estan respaldadas.
::
:: Uso:
::    scripts\verificar_despliegue.bat
::    scripts\verificar_despliegue.bat --base C:\otra\ruta\fichajes.db

setlocal
cd /d "%~dp0.."

python scripts\verificar_despliegue.py %*

echo.
pause

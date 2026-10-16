@echo off
:: SistemAlf -- Cambiar el nivel de administracion de alguien en un lector
::
:: ESCRIBE EN EL EQUIPO. El nivel es quien puede administrar el lector parado
:: frente a el: entrar al menu, dar de alta gente, tomarle la huella. No tiene
:: nada que ver con abrir puertas.
::
:: Niveles: 0 usuario comun, 2 enrolador, 6 administrador, 14 super admin.
::
:: Avisa si el cambio deja al equipo sin ningun administrador --ahi el menu
:: queda abierto para cualquiera-- y verifica releyendo.
::
:: Uso:
::    scripts\cambiar_nivel_lector.bat 8 0
::    scripts\cambiar_nivel_lector.bat 8 0 --equipo 192.168.1.209

setlocal
cd /d "%~dp0.."

:: Encontrar el interprete: en una de las maquinas `python` no esta en el
:: PATH y Windows lo resuelve al alias del Microsoft Store.
call "%~dp0_python.bat"
if not defined PY exit /b 1

if "%~2"=="" goto :uso

%PY% scripts\cambiar_nivel_lector.py %*

echo.
pause
exit /b

:uso
echo.
echo   Faltan datos: el numero de la persona y el nivel.
echo.
echo       scripts\cambiar_nivel_lector.bat 8 0
echo.
echo   Niveles:
echo       0   usuario comun, no administra
echo       2   enrolador: puede dar de alta y tomar huellas
echo       6   administrador del lector
echo       14  super admin
echo.
pause
exit /b 1

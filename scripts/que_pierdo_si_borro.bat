@echo off
:: SistemAlf -- Que hay en esta carpeta que NO este en GitHub
::
:: Para correr ANTES de borrar una copia del repo. Todo lo que esta versionado
:: se puede volver a bajar; lo que figura aca existe solo en este disco y se
:: pierde para siempre.
::
:: Lo tipico que aparece y NO importa: __pycache__, .vscode, .venv
:: Lo que SI importa: bases de datos, relevamiento\, borrados\, fotos, .env
::
:: SOLO LECTURA. No borra nada.
::
:: Uso: paralo en la carpeta que vas a borrar y corre
::    scripts\que_pierdo_si_borro.bat

setlocal
cd /d "%~dp0.."

echo.
echo   Carpeta: %CD%
echo.
echo   ARCHIVOS SIN GUARDAR (modificados y no commiteados)
echo   --------------------------------------------------
git status --short
echo.
echo   COMMITS QUE NO ESTAN EN GITHUB
echo   --------------------------------------------------
git log --branches --not --remotes --oneline
echo.
echo   CARPETAS Y ARCHIVOS QUE EXISTEN SOLO ACA
echo   --------------------------------------------------
git status --ignored --short | findstr "^!!"
echo.
echo   De esa lista, __pycache__, .vscode y .venv no importan.
echo   Las bases de datos, relevamiento\, borrados\ y las fotos SI.
echo.
pause

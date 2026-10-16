@echo off
:: SistemAlf -- Encontrar el interprete de Python. No se corre solo.
::
:: Lo llaman los demas .bat con:   call "%~dp0_python.bat"
:: y despues usan   %PY%   en vez de   python
::
:: Por que hace falta. Los scripts asumian que `python` estaba en el PATH, y en
:: una de las dos maquinas de produccion no esta: Windows lo resuelve al alias
:: del Microsoft Store y contesta "no se encontro Python; ejecutar sin
:: argumentos para instalar". El sistema igual corre, porque el servicio usa su
:: propio interprete; el que no lo encontraba era el PATH de la consola.
::
:: Orden de busqueda, del mas especifico al mas general:
::   1. SISTEMALF_PYTHON, si alguien lo definio a mano
::   2. el entorno virtual al lado del sistema (.venv o venv)
::   3. `python`, salvo que sea el alias del Store
::   4. el lanzador `py -3`, que instala el instalador estandar para todos

setlocal enabledelayedexpansion
set "ENCONTRADO="

if defined SISTEMALF_PYTHON if exist "%SISTEMALF_PYTHON%" set "ENCONTRADO=%SISTEMALF_PYTHON%"

if not defined ENCONTRADO if exist "%~dp0..\.venv\Scripts\python.exe" set "ENCONTRADO=%~dp0..\.venv\Scripts\python.exe"
if not defined ENCONTRADO if exist "%~dp0..\venv\Scripts\python.exe"  set "ENCONTRADO=%~dp0..\venv\Scripts\python.exe"

:: `where` lista todos los que hay; el alias del Store vive en WindowsApps y no
:: es un interprete: contesta un cartel y se va.
if not defined ENCONTRADO (
  for /f "delims=" %%P in ('where python 2^>nul') do (
    if not defined ENCONTRADO (
      set "RUTA=%%P"
      rem Sin `find` ni `findstr`: en una consola con Git Bash en el PATH esos
      rem nombres resuelven a otro programa que no entiende estos argumentos.
      if "!RUTA:WindowsApps=!"=="!RUTA!" set "ENCONTRADO=!RUTA!"
    )
  )
)

if not defined ENCONTRADO (
  py -3 -c "pass" >nul 2>&1 && set "ENCONTRADO=py -3"
)

endlocal & set "PY=%ENCONTRADO%"

if not defined PY (
  echo.
  echo   No encuentro Python en esta maquina.
  echo.
  echo   El sistema igual corre: el servicio usa su propio interprete. Lo que
  echo   falta es que la consola lo encuentre. Proba:
  echo.
  echo       where python
  echo       py -0p
  echo.
  echo   Y cuando sepas donde esta, decile a los scripts una sola vez:
  echo.
  echo       setx SISTEMALF_PYTHON "C:\ruta\a\python.exe"
  echo.
  echo   Abri una consola nueva despues de eso.
  echo.
)

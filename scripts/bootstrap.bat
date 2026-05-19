@echo off
:: SistemAlf — Instalador
:: Descargar este archivo, clic derecho → "Ejecutar como administrador"

echo.
echo ╔══════════════════════════════════════════╗
echo ║       SistemAlf — Instalacion           ║
echo ╚══════════════════════════════════════════╝
echo.

:: Verificar que corre como Admin
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo ERROR: Este instalador requiere permisos de Administrador.
    echo.
    echo Clic derecho en el archivo y elegir "Ejecutar como administrador".
    echo.
    pause
    exit /b 1
)

echo Descargando instalador...
powershell -ExecutionPolicy Bypass -Command "Invoke-WebRequest 'https://raw.githubusercontent.com/gardinerbuenosaires/sistemalf-asistencia/main/scripts/install.ps1' -OutFile '%TEMP%\sl_install.ps1'"

if %errorLevel% neq 0 (
    echo ERROR: No se pudo descargar el instalador. Verificar conexion a internet.
    pause
    exit /b 1
)

echo Ejecutando instalador...
echo.
powershell -ExecutionPolicy Bypass -File "%TEMP%\sl_install.ps1" %*

del "%TEMP%\sl_install.ps1" >nul 2>&1

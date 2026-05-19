@echo off
:: SistemAlf — Actualizador
:: Descarga los cambios nuevos del repositorio y reinicia el servicio.
:: Ejecutar como Administrador.

echo.
echo ╔══════════════════════════════════════════╗
echo ║       SistemAlf — Actualizacion         ║
echo ╚══════════════════════════════════════════╝
echo.

:: Verificar que corre como Admin
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo ERROR: Ejecutar como Administrador.
    pause
    exit /b 1
)

echo Paso 1/2 — Descargando cambios del repositorio...
cd C:\SistemAlf
git pull
if %errorLevel% neq 0 (
    echo ERROR: No se pudo actualizar. Verificar conexion a internet.
    pause
    exit /b 1
)

echo.
echo Paso 2/2 — Reiniciando servicio (el sistema queda inaccesible ~5 segundos)...
C:\SistemAlf\tools\nssm.exe restart SistemAlf

echo.
echo Actualizacion completada. El sistema ya esta disponible.
echo.
pause

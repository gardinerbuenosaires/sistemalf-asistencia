@echo off
:: SistemaLF — Actualizador
:: Descarga los cambios nuevos del repositorio y reinicia el servicio.
:: Ejecutar como Administrador.

echo.
echo ╔══════════════════════════════════════════╗
echo ║       SistemaLF — Actualizacion         ║
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
cd C:\SistemaLF
git pull
if %errorLevel% neq 0 (
    echo ERROR: No se pudo actualizar. Verificar conexion a internet.
    pause
    exit /b 1
)

echo.
echo Paso 2/2 — Reiniciando servicio (el sistema queda inaccesible ~5 segundos)...
C:\SistemaLF\tools\nssm.exe restart SistemaLF

echo.
echo Actualizacion completada. El sistema ya esta disponible.
echo.
pause

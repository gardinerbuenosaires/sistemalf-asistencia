@echo off
net session >nul 2>&1
if %errorLevel% neq 0 ( echo ERROR: Ejecutar como Administrador. & pause & exit /b 1 )
echo Deteniendo SistemAlf...
C:\SistemAlf\tools\nssm.exe stop SistemAlf
echo Servicio detenido. La base de datos puede copiarse con seguridad.
pause

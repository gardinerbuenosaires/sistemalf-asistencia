@echo off
net session >nul 2>&1
if %errorLevel% neq 0 ( echo ERROR: Ejecutar como Administrador. & pause & exit /b 1 )
echo Iniciando SistemAlf...
C:\SistemAlf\tools\nssm.exe start SistemAlf
echo Servicio iniciado. Sistema disponible en http://localhost:8000
pause

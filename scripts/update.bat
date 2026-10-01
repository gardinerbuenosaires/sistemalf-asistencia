@echo off
:: SistemAlf -- Actualizador
:: Descarga los cambios nuevos del repositorio y reinicia el servicio.
:: Ejecutar como Administrador.

echo.
echo ==========================================
echo       SistemAlf -- Actualizacion
echo ==========================================
echo.

:: Verificar que corre como Admin
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Ejecutar como Administrador.
    pause
    exit /b 1
)

:: Verificar que existe el directorio
if not exist "C:\SistemAlf\.git" (
    echo ERROR: No se encontro C:\SistemAlf. El sistema no esta instalado.
    pause
    exit /b 1
)

:: Verificar que existe NSSM
if not exist "C:\SistemAlf\tools\nssm.exe" (
    echo ERROR: No se encontro nssm.exe en C:\SistemAlf\tools\
    pause
    exit /b 1
)

echo Paso 1/3 -- Descargando cambios del repositorio...
cd /d "C:\SistemAlf"
git pull
if %errorlevel% neq 0 (
    echo ERROR: No se pudo actualizar. Verificar conexion a internet.
    pause
    exit /b 1
)

:: Si el cambio trajo una libreria nueva, sin esto el servicio no arranca.
:: Si no hay nada nuevo, no hace nada. Mismo python que uso install.ps1.
echo.
echo Paso 2/3 -- Instalando librerias nuevas, si las hay...
python -m pip install -r requirements.txt --quiet --disable-pip-version-check
if %errorlevel% neq 0 (
    echo ERROR: No se pudieron instalar las librerias.
    echo El servicio NO se reinicio: sigue funcionando la version anterior.
    pause
    exit /b 1
)

echo.
echo Paso 3/3 -- Reiniciando servicio...
"C:\SistemAlf\tools\nssm.exe" restart SistemAlf
if %errorlevel% neq 0 (
    echo ADVERTENCIA: El servicio no se pudo reiniciar automaticamente.
    echo Reinicialo manualmente con: scripts\start.bat
)

echo.
echo ==========================================
echo   Actualizacion completada con exito.
echo   El sistema ya esta disponible.
echo ==========================================
echo.
pause

# install.ps1 — SistemaLF: instalación en PC de producción
# Ejecutar como Administrador en PowerShell
#
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#   .\install.ps1

$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"

# ── Configuración ────────────────────────────────────────────────────────────
$REPO_URL   = "https://github.com/gardinerbuenosaires/sistemalf-asistencia.git"
$CODE_DIR   = "C:\SistemaLF"
$DATA_DIR   = "C:\ProgramData\SistemaLF"
$DB_FILE    = "$DATA_DIR\fichajes.db"
$SERVICE    = "SistemaLF"
$NSSM_URL   = "https://nssm.cc/release/nssm-2.24.zip"
$NSSM_EXE   = "$CODE_DIR\nssm.exe"
$LOG_FILE   = "$DATA_DIR\service.log"

function Step($n, $msg) { Write-Host "`n[$n] $msg" -ForegroundColor Cyan }
function OK($msg)        { Write-Host "    OK: $msg" -ForegroundColor Green }
function Warn($msg)      { Write-Host "    AVISO: $msg" -ForegroundColor Yellow }
function Fail($msg)      { Write-Host "    ERROR: $msg" -ForegroundColor Red; exit 1 }

# ── 1. Verificar Python ──────────────────────────────────────────────────────
Step 1 "Verificando Python 3.11+..."
try {
    $ver = python --version 2>&1
    if ($ver -notmatch "Python 3\.(1[1-9]|[2-9]\d)") { Fail "Se requiere Python 3.11+. Instalado: $ver" }
    OK $ver
} catch { Fail "Python no encontrado. Instalarlo desde python.org y reintentar." }

# ── 2. Verificar Git ─────────────────────────────────────────────────────────
Step 2 "Verificando Git..."
try { $g = git --version 2>&1; OK $g }
catch { Fail "Git no encontrado. Instalarlo desde git-scm.com y reintentar." }

# ── 3. Clonar repositorio ────────────────────────────────────────────────────
Step 3 "Repositorio de codigo en $CODE_DIR..."
if (Test-Path "$CODE_DIR\.git") {
    Warn "Repo ya existe. Actualizando..."
    git -C $CODE_DIR pull
} else {
    git clone $REPO_URL $CODE_DIR
}
OK "Codigo listo en $CODE_DIR"

# ── 4. Instalar dependencias Python ─────────────────────────────────────────
Step 4 "Instalando dependencias Python..."
python -m pip install -r "$CODE_DIR\requirements.txt" --upgrade -q
OK "Dependencias instaladas"

# ── 5. Directorio de datos ───────────────────────────────────────────────────
Step 5 "Directorio de datos: $DATA_DIR..."
New-Item -ItemType Directory -Force -Path $DATA_DIR        | Out-Null
New-Item -ItemType Directory -Force -Path "$DATA_DIR\fotos" | Out-Null
New-Item -ItemType Directory -Force -Path "$DATA_DIR\fotos_pendientes" | Out-Null
OK "Directorios creados"

if (-not (Test-Path $DB_FILE)) {
    Write-Host ""
    Write-Host "    No se encontro fichajes.db en $DATA_DIR" -ForegroundColor Yellow
    Write-Host "    Copia el archivo fichajes.db desde la PC de desarrollo a:" -ForegroundColor Yellow
    Write-Host "    $DATA_DIR\" -ForegroundColor White
    Write-Host "    (tambien copia la carpeta 'fotos' si ya tenes fotos cargadas)" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "    Presiona Enter cuando el archivo este listo..." -ForegroundColor Yellow
    Read-Host
    if (-not (Test-Path $DB_FILE)) { Fail "fichajes.db no encontrado en $DATA_DIR. Abortando." }
}
OK "Base de datos: $DB_FILE"

# ── 6. Descargar NSSM ────────────────────────────────────────────────────────
Step 6 "NSSM (servicio Windows)..."
if (-not (Test-Path $NSSM_EXE)) {
    Write-Host "    Descargando NSSM..."
    $zip = "$env:TEMP\nssm.zip"
    Invoke-WebRequest -Uri $NSSM_URL -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath "$env:TEMP\nssm_ext" -Force
    Copy-Item "$env:TEMP\nssm_ext\nssm-2.24\win64\nssm.exe" $NSSM_EXE
    Remove-Item $zip, "$env:TEMP\nssm_ext" -Recurse -Force
}
OK "NSSM listo"

# ── 7. Registrar servicio Windows ────────────────────────────────────────────
Step 7 "Registrando servicio Windows '$SERVICE'..."
$python = (Get-Command python -ErrorAction Stop).Source

$svc = Get-Service -Name $SERVICE -ErrorAction SilentlyContinue
if ($svc) {
    Write-Host "    Servicio existente detectado. Reemplazando..."
    & $NSSM_EXE stop    $SERVICE 2>$null
    & $NSSM_EXE remove  $SERVICE confirm 2>$null
    Start-Sleep -Seconds 2
}

& $NSSM_EXE install $SERVICE $python "`"$CODE_DIR\main.py`""
& $NSSM_EXE set $SERVICE AppDirectory        $CODE_DIR
& $NSSM_EXE set $SERVICE AppEnvironmentExtra "DB_PATH=$DB_FILE"
& $NSSM_EXE set $SERVICE DisplayName         "SistemaLF Asistencia"
& $NSSM_EXE set $SERVICE Description         "Sistema de fichaje y asistencia - Gardiner BA"
& $NSSM_EXE set $SERVICE Start               SERVICE_AUTO_START
& $NSSM_EXE set $SERVICE AppStdout           $LOG_FILE
& $NSSM_EXE set $SERVICE AppStderr           $LOG_FILE
& $NSSM_EXE set $SERVICE AppRotateFiles      1
& $NSSM_EXE set $SERVICE AppRotateBytes      5000000
& $NSSM_EXE set $SERVICE AppRestartDelay     3000

Write-Host "    Iniciando servicio..."
& $NSSM_EXE start $SERVICE
Start-Sleep -Seconds 4

$svc2 = Get-Service -Name $SERVICE -ErrorAction SilentlyContinue
if ($svc2 -and $svc2.Status -eq "Running") {
    OK "Servicio corriendo"
} else {
    Warn "El servicio no arranco. Revisar logs en $LOG_FILE"
}

# ── Resumen ───────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host " INSTALACION COMPLETA" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host " Sistema:   http://localhost:8000"
Write-Host " Datos:     $DATA_DIR"
Write-Host " Logs:      $LOG_FILE"
Write-Host " Actualizar: C:\SistemaLF\update.bat"
Write-Host "========================================"

<#
.SYNOPSIS
    Instala SistemAlf en una PC nueva (Windows 10/11).

.DESCRIPTION
    Instala Python y Git si no están presentes, clona el repositorio,
    instala dependencias, configura el servicio Windows via NSSM y lo arranca.

.PARAMETER Port
    Puerto HTTP del servidor (default: 8000).

.EXAMPLE
    # Ejecutar como Administrador:
    Set-ExecutionPolicy Bypass -Scope Process -Force
    .\scripts\install.ps1

    # Con puerto personalizado:
    .\scripts\install.ps1 -Port 8001

.NOTES
    Tiempo estimado: 5-10 minutos en una PC nueva.
    Al finalizar el sistema queda disponible en http://localhost:PORT
    Usuario inicial: admin@sistema.local  Clave: admin1234
#>

param(
    [string]$Port        = "8000",
    [string]$RepoUrl     = "https://github.com/gardinerbuenosaires/sistemalf-asistencia.git",
    [string]$InstallDir  = "C:\SistemAlf",
    [string]$DataDir     = "C:\ProgramData\SistemAlf",
    [string]$ServiceName = "SistemAlf"
)

$ErrorActionPreference = "Stop"

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-OK($msg)   { Write-Host "    OK: $msg" -ForegroundColor Green }
function Write-Info($msg) { Write-Host "    $msg" -ForegroundColor Gray }

# ── Verificar que corre como Admin ───────────────────────────────────────────
Write-Step "Verificando permisos"
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "ERROR: Ejecutar este script como Administrador." -ForegroundColor Red
    exit 1
}
Write-OK "Ejecutando como Administrador"

# ── Python ────────────────────────────────────────────────────────────────────
Write-Step "Verificando Python"
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Info "Instalando Python 3.12 via winget..."
    winget install --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path","User")
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        Write-Host "ERROR: Python no encontrado. Instalarlo manualmente desde https://python.org" -ForegroundColor Red
        exit 1
    }
}
Write-OK (python --version)

# ── Git ───────────────────────────────────────────────────────────────────────
Write-Step "Verificando Git"
$git = Get-Command git -ErrorAction SilentlyContinue
if (-not $git) {
    Write-Info "Instalando Git via winget..."
    winget install --id Git.Git --silent --accept-package-agreements --accept-source-agreements
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path","User")
}
Write-OK (git --version)

# ── Clonar o actualizar repositorio ───────────────────────────────────────────
Write-Step "Instalando codigo"
if (Test-Path "$InstallDir\.git") {
    Write-Info "Repositorio ya existe, actualizando..."
    git -C $InstallDir pull
} else {
    Write-Info "Clonando en $InstallDir ..."
    git clone $RepoUrl $InstallDir
}
Write-OK "Codigo en $InstallDir"

# ── Directorio de datos (DB, logs, fotos) ─────────────────────────────────────
Write-Step "Creando directorio de datos"
New-Item -ItemType Directory -Path $DataDir                   -Force | Out-Null
New-Item -ItemType Directory -Path "$DataDir\fotos"           -Force | Out-Null
New-Item -ItemType Directory -Path "$DataDir\fotos_pendientes" -Force | Out-Null
Write-OK "Datos en $DataDir"

# ── Dependencias Python ───────────────────────────────────────────────────────
Write-Step "Instalando dependencias Python"
Write-Info "Esto puede tardar 1-2 minutos..."
python -m pip install --upgrade pip --quiet
python -m pip install -r "$InstallDir\requirements.txt"
Write-OK "Dependencias instaladas"

# ── NSSM (servicio Windows) ───────────────────────────────────────────────────
Write-Step "Configurando servicio Windows"
$toolsDir = "$InstallDir\tools"
$nssmExe  = "$toolsDir\nssm.exe"

if (-not (Test-Path $nssmExe)) {
    Write-Info "Descargando NSSM..."
    New-Item -ItemType Directory -Path $toolsDir -Force | Out-Null
    $zip = "$env:TEMP\nssm.zip"
    $tmp = "$env:TEMP\nssm_tmp"
    Invoke-WebRequest "https://nssm.cc/release/nssm-2.24.zip" -OutFile $zip
    Expand-Archive $zip $tmp -Force
    Copy-Item "$tmp\nssm-2.24\win64\nssm.exe" $nssmExe
    Remove-Item $zip, $tmp -Recurse -Force
}
Write-OK "NSSM disponible"

# Eliminar servicio anterior si existe
$svcStatus = $null
try { $svcStatus = & $nssmExe status $ServiceName 2>&1 } catch {}
if ($svcStatus -and $svcStatus -notmatch "Can't open service") {
    Write-Info "Eliminando servicio anterior..."
    try { & $nssmExe stop   $ServiceName 2>&1 | Out-Null } catch {}
    try { & $nssmExe remove $ServiceName confirm 2>&1 | Out-Null } catch {}
    Start-Sleep -Seconds 2
}

# Registrar servicio
$pythonExe = (python -c "import sys; print(sys.executable)").Trim()
Write-Info "Registrando servicio $ServiceName en puerto $Port..."

& $nssmExe install $ServiceName $pythonExe "`"$InstallDir\main.py`""
& $nssmExe set $ServiceName AppDirectory        $InstallDir
& $nssmExe set $ServiceName DisplayName         "SistemAlf - Asistencia"
& $nssmExe set $ServiceName Description         "Sistema de fichaje y asistencia SistemAlf"
& $nssmExe set $ServiceName Start               SERVICE_AUTO_START
& $nssmExe set $ServiceName AppEnvironmentExtra "DB_PATH=$DataDir\fichajes.db" "API_PORT=$Port"
& $nssmExe set $ServiceName AppStdout           "$DataDir\service.log"
& $nssmExe set $ServiceName AppStderr           "$DataDir\service.log"
& $nssmExe set $ServiceName AppRotateFiles      1
& $nssmExe set $ServiceName AppRotateBytes      5242880
& $nssmExe set $ServiceName AppRestartDelay     3000

# ── Pausa para restaurar DB existente ────────────────────────────────────────
$dbDest = "$DataDir\fichajes.db"
if (-not (Test-Path $dbDest)) {
    Write-Host ""
    Write-Host "──────────────────────────────────────────────────" -ForegroundColor Yellow
    Write-Host "  MIGRACION DE DATOS (opcional)" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  Si tenes un respaldo de la base de datos," -ForegroundColor Yellow
    Write-Host "  copialo ahora a:" -ForegroundColor Yellow
    Write-Host "    $dbDest" -ForegroundColor White
    Write-Host ""
    Write-Host "  Si es una instalacion nueva, dejalo en blanco." -ForegroundColor Yellow
    Write-Host "──────────────────────────────────────────────────" -ForegroundColor Yellow
    Write-Host ""
    Read-Host "  Presiona Enter cuando este listo para continuar"
}

# Iniciar servicio
Write-Step "Iniciando servicio"
& $nssmExe start $ServiceName
Start-Sleep -Seconds 4

$finalStatus = & $nssmExe status $ServiceName 2>$null
Write-OK "Estado: $finalStatus"

# ── Tarea programada: backup nocturno a OneDrive ──────────────────────────────
Write-Step "Configurando backup nocturno (3:00 AM → OneDrive)..."
$backupScript = "$InstallDir\scripts\backup.ps1"
$taskName     = "SistemAlf-Backup"
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
$action  = New-ScheduledTaskAction -Execute "powershell.exe" `
               -Argument "-NonInteractive -ExecutionPolicy Bypass -File `"$backupScript`""
$trigger = New-ScheduledTaskTrigger -Daily -At "03:00"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable:$false
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -RunLevel Highest -Force | Out-Null
Write-OK "Tarea '$taskName' registrada — corre diariamente a las 3:00 AM"

# ── Resumen ───────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "===========================================" -ForegroundColor Green
Write-Host "        INSTALACION COMPLETADA             " -ForegroundColor Green
Write-Host "===========================================" -ForegroundColor Green
Write-Host "  URL:      http://localhost:$Port"          -ForegroundColor Green
Write-Host "  Usuario:  admin@sistema.local"             -ForegroundColor Green
Write-Host "  Clave:    admin1234"                       -ForegroundColor Green
Write-Host "  Datos:    $DataDir"                        -ForegroundColor Green
Write-Host "  Logs:     $DataDir\service.log"            -ForegroundColor Green
Write-Host "===========================================" -ForegroundColor Green
Write-Host ""
Write-Host "IMPORTANTE: Cambia la clave del admin al primer ingreso." -ForegroundColor Yellow
Write-Host ""

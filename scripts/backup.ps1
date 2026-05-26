# backup.ps1 — SistemAlf: backup nocturno de la base de datos
# Corre via Tarea Programada. No requiere Administrador.

$DB_SOURCE   = "C:\ProgramData\SistemAlf\fichajes.db"
$RETENER_DIAS = 30

# Carpeta de destino dentro de OneDrive
$oneDrive = $env:OneDrive
if (-not $oneDrive -or -not (Test-Path $oneDrive)) {
    Write-Error "OneDrive no encontrado. Verificar que este instalado y con sesion iniciada."
    exit 1
}
$BACKUP_DIR = "$oneDrive\SistemAlf\Backups"
New-Item -ItemType Directory -Force -Path $BACKUP_DIR | Out-Null

# Nombre del archivo con fecha
$fecha   = Get-Date -Format "yyyy-MM-dd"
$destino = "$BACKUP_DIR\fichajes_$fecha.db"

# Copiar (SQLite con WAL: solo copiar .db es seguro si el servicio usa checkpoints)
try {
    Copy-Item -Path $DB_SOURCE -Destination $destino -Force
    $size = [math]::Round((Get-Item $destino).Length / 1MB, 2)
    Write-Host "$(Get-Date -Format 'HH:mm:ss') Backup OK: $destino ($size MB)"
} catch {
    Write-Error "Error al copiar DB: $_"
    exit 1
}

# Limpiar backups mas antiguos que $RETENER_DIAS dias
$viejos = Get-ChildItem -Path $BACKUP_DIR -Filter "fichajes_*.db" |
          Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$RETENER_DIAS) }
foreach ($v in $viejos) {
    Remove-Item $v.FullName -Force
    Write-Host "$(Get-Date -Format 'HH:mm:ss') Eliminado backup viejo: $($v.Name)"
}

Write-Host "$(Get-Date -Format 'HH:mm:ss') Backups disponibles: $((Get-ChildItem $BACKUP_DIR -Filter '*.db').Count)"

<#
.SYNOPSIS
    Stop every window started by scripts\start_all.ps1 (process trees from var\processes.json),
    then any Python process still listening on ports 8000-8003 or 8501.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\stop_all.ps1
#>
$root = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $root "var\processes.json"
if (Test-Path $pidFile) {
    # Assign first: in Windows PowerShell 5.1 a piped JSON array arrives as one object.
    $entries = Get-Content -Raw $pidFile | ConvertFrom-Json
    foreach ($p in $entries) {
        if (Get-Process -Id $p.pid -ErrorAction SilentlyContinue) {
            & taskkill.exe /PID $p.pid /T /F | Out-Null
            Write-Host "Stopped $($p.name) (pid $($p.pid))"
        }
    }
    Remove-Item $pidFile
} else {
    Write-Host "No var\processes.json: checking the service ports only."
}
foreach ($port in 8000, 8001, 8002, 8003, 8501) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        $proc = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
        if ($proc -and $proc.ProcessName -like "python*") {
            Stop-Process -Id $proc.Id -Force
            Write-Host "Stopped python pid $($proc.Id) still listening on port $port"
        }
    }
}
Write-Host "All services stopped."

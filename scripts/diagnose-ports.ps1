# =============================================================================
# NurseLearn AI — Diagnostic des ports (API / Web) sous Windows
# Usage :  powershell -ExecutionPolicy Bypass -File .\scripts\diagnose-ports.ps1
# Lecture seule : n'arrête, ne supprime et ne modifie rien. Durée : quelques secondes.
# =============================================================================
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ports = @(3000, 8000, 18000, 28000, 55432)

Write-Host "=== 1. Programmes à l'écoute sur $($ports -join ', ') ==="
foreach ($port in $ports) {
    $conns = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
    if (-not $conns) { Write-Host ("{0,-6} libre" -f $port); continue }
    foreach ($c in $conns) {
        $p = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
        $path = try { $p.Path } catch { "" }
        Write-Host ("{0,-6} {1,-18} PID {2,-7} {3,-25} {4}" -f $port, $c.LocalAddress, $c.OwningProcess, $p.ProcessName, $path)
    }
}

Write-Host ""; Write-Host "=== 2. Conteneurs Docker (tous projets) et ports publiés ==="
docker ps -a --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

Write-Host ""; Write-Host "=== 3. Ce qui répond sur chaque port (en-têtes seulement) ==="
foreach ($port in 8000, 18000, 28000, 3000) {
    Write-Host "--- http://127.0.0.1:$port/health"
    curl.exe -s -i -m 3 "http://127.0.0.1:$port/health" | Select-Object -First 12
}

Write-Host ""; Write-Host "=== 4. Plages de ports réservées par Windows (Hyper-V / WinNAT) ==="
netsh interface ipv4 show excludedportrange protocol=tcp

Write-Host ""; Write-Host "=== 5. État et derniers journaux des services api et web ==="
docker compose ps -a
docker compose logs --no-color --tail 30 api web

Write-Host ""; Write-Host "=== 6. Distributions WSL en cours (un serveur dans WSL peut occuper un port Windows) ==="
wsl.exe -l -v

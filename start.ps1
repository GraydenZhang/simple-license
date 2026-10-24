$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Run .\setup.ps1 first.' }
New-Item -ItemType Directory -Path (Join-Path $PSScriptRoot 'data') -Force | Out-Null
foreach ($service in @('approval', 'verify')) {
    $port = if ($service -eq 'approval') { 15001 } else { 15002 }
    $stateFile = Join-Path $PSScriptRoot "data\$service.process.json"
    $existing = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -ne $existing) {
        if (Test-Path -LiteralPath $stateFile) {
            $saved = Get-Content -Raw -LiteralPath $stateFile | ConvertFrom-Json
            if ($saved.server_pid -eq $existing.OwningProcess) { Write-Output "$service is already running on $port"; continue }
        }
        throw "Port $port is occupied. The existing process was left unchanged."
    }
    $launcher = Start-Process -FilePath $pythonPath -ArgumentList @('-m', 'scripts.run', $service) -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $PSScriptRoot "data\$service.stdout.log") -RedirectStandardError (Join-Path $PSScriptRoot "data\$service.stderr.log") -PassThru
    $listener = $null
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 300
        $listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($null -ne $listener) { break }
    }
    if ($null -eq $listener) { throw "$service failed to start. Check data\$service.stderr.log" }
    $server = Get-Process -Id $listener.OwningProcess
    @{service=$service; server_pid=$server.Id; launcher_pid=$launcher.Id; started_at=$server.StartTime.ToUniversalTime().ToString('o'); workspace=$PSScriptRoot} | ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding UTF8
    Write-Output "$service started on http://127.0.0.1:$port"
}
Write-Output 'Open http://127.0.0.1:15001/staff/login. Both root paths intentionally return 404.'

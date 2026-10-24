$ErrorActionPreference = 'Stop'
foreach ($service in @('approval', 'verify')) {
    $stateFile = Join-Path $PSScriptRoot "data\$service.process.json"
    if (-not (Test-Path -LiteralPath $stateFile)) { continue }
    $saved = Get-Content -Raw -LiteralPath $stateFile | ConvertFrom-Json
    if ($saved.workspace -ne $PSScriptRoot) { throw 'Process record belongs to another workspace.' }
    $server = Get-Process -Id $saved.server_pid -ErrorAction SilentlyContinue
    if ($null -ne $server) {
        # PowerShell 7 may deserialize ISO dates as DateTime; normalize both sides.
        $savedStart = [DateTime]$saved.started_at
        if ($server.StartTime.ToUniversalTime().Ticks -ne $savedStart.ToUniversalTime().Ticks) { throw 'PID has been reused. No process was stopped.' }
        Stop-Process -Id $server.Id
    }
    Remove-Item -LiteralPath $stateFile
    Write-Output "$service stopped. Business data was preserved."
}

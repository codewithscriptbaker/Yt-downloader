# Allow phone access to MediaPort on the same Wi‑Fi.
# Right-click PowerShell → Run as administrator, then:
#   cd "d:\Repo Projects\Yt\Yt-downloader"
#   powershell -ExecutionPolicy Bypass -File .\scripts\allow_lan_firewall.ps1

$ErrorActionPreference = "Stop"

foreach ($rule in @(
    @{ Name = "MediaPort API 8009"; Port = 8009 },
    @{ Name = "MediaPort Web 3005"; Port = 3005 }
)) {
    netsh advfirewall firewall delete rule name=$rule.Name 2>$null | Out-Null
    netsh advfirewall firewall add rule name=$rule.Name dir=in action=allow protocol=TCP localport=$rule.Port | Out-Null
    Write-Host "Allowed inbound TCP $($rule.Port) ($($rule.Name))"
}

Write-Host ""
Write-Host "Done. Restart the app, then on your phone open:"
Write-Host "  http://$((Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -like '192.168.*' -or $_.IPAddress -like '10.*' } | Select-Object -First 1).IPAddress):3005"

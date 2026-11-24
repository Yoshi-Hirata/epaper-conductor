# Keeps the show PC's Wi-Fi adapter on the units' network (R2.net).
#
# The R2.net profile is "connect manually" on purpose, so when the router drops
# every client (seen several times at Kunitachi, 2026-11-22) the units come back
# by themselves in 26-45 s but the PC stays off until somebody reconnects it -
# and the Conductor shows every unit offline meanwhile. This reconnects the
# adapter when it has been disconnected for two checks in a row, then waits
# before trying again: the router's radio needs that long to come back, and a
# connect sent to an adapter that is already connecting only restarts it.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\keep_r2net.ps1
#   powershell ... -File tools\keep_r2net.ps1 -Adapter "Wi-Fi 2" -Profile "R2.net"
#
# It changes no setting: it only asks Windows to connect a profile that is
# already saved. Stop it with Ctrl+C. Do not connect R2.net from the taskbar
# while it runs - that picks the built-in adapter, and Windows drops both when
# two adapters sit on the same network.
param(
    [string]$Adapter = "Wi-Fi 2",
    [string]$Profile = "R2.net",
    [string]$Log = (Join-Path $env:TEMP "keep_r2net.log")
)

function Say($message) {
    $line = "{0:HH:mm:ss} {1}" -f (Get-Date), $message
    $line
    Add-Content -Path $Log -Value $line -Encoding utf8
}

Say "watching $Adapter ($Profile)"
$down = 0
$wasDown = $false
while ($true) {
    $state = (Get-NetAdapter -Name $Adapter -ErrorAction SilentlyContinue).MediaConnectionState
    if ($state -eq 'Connected') {
        if ($wasDown) { Say "$Adapter is back on $Profile"; $wasDown = $false }
        $down = 0
        Start-Sleep -Seconds 5
        continue
    }
    $down++
    if ($down -ge 2) {
        if (-not $wasDown) { Say "$Adapter lost $Profile (state: $state) - reconnecting"; $wasDown = $true }
        netsh wlan connect name="$Profile" interface="$Adapter" | Out-Null
        Start-Sleep -Seconds 12
    } else {
        Start-Sleep -Seconds 5
    }
}

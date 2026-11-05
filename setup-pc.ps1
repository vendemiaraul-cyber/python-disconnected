# Roblox Studio -> iPhone setup (run on your Windows PC)
# Installs Roblox Studio + Chrome Remote Desktop, keeps the PC awake,
# then opens the one-time Google sign-in page so your iPhone can connect.

$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'   # makes downloads much faster
$dl = Join-Path $env:TEMP 'studio-iphone-setup'
New-Item -ItemType Directory -Force -Path $dl | Out-Null

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

# 1. Roblox Studio (installs per-user, so this part runs WITHOUT admin)
Step 'Roblox Studio'
$studio = Get-ChildItem "$env:LOCALAPPDATA\Roblox\Versions\*\RobloxStudioBeta.exe" -ErrorAction SilentlyContinue
if ($studio) {
    Write-Host 'Already installed - skipping.'
} else {
    $exe = Join-Path $dl 'RobloxStudioInstaller.exe'
    Write-Host 'Downloading official installer from roblox.com...'
    Invoke-WebRequest 'https://www.roblox.com/download/studio' -OutFile $exe -UseBasicParsing
    Write-Host 'Installing (a Roblox window will appear; let it finish)...'
    Start-Process $exe -Wait
}

# 2. Claude Sync plugin (lets Claude's code appear in Studio with one tap)
Step 'Claude Sync plugin for Roblox Studio'
$pluginDir = Join-Path $env:LOCALAPPDATA 'Roblox\Plugins'
New-Item -ItemType Directory -Force -Path $pluginDir | Out-Null
$src = Join-Path $PSScriptRoot 'plugin\ClaudeSync.lua'
if (Test-Path $src) {
    Copy-Item $src $pluginDir -Force
} else {
    Invoke-WebRequest 'https://raw.githubusercontent.com/vendemiaraul-cyber/python-disconnected/studio/plugin/ClaudeSync.lua' -OutFile (Join-Path $pluginDir 'ClaudeSync.lua') -UseBasicParsing
}
Write-Host 'Installed. Restart Roblox Studio if it is open; look for the "Claude" toolbar in the Plugins tab.'

# 3. Chrome Remote Desktop host (needs admin -> one UAC prompt)
Step 'Chrome Remote Desktop host'
$crd = @("${env:ProgramFiles(x86)}\Google\Chrome Remote Desktop",
         "$env:ProgramFiles\Google\Chrome Remote Desktop") | Where-Object { Test-Path $_ }
if ($crd) {
    Write-Host 'Already installed - skipping.'
} else {
    $msi = Join-Path $dl 'chromeremotedesktophost.msi'
    Write-Host 'Downloading from Google...'
    Invoke-WebRequest 'https://dl.google.com/edgedl/chrome-remote-desktop/chromeremotedesktophost.msi' -OutFile $msi -UseBasicParsing
    Write-Host 'Installing (click YES on the Windows permission prompt)...'
    $p = Start-Process msiexec.exe -ArgumentList "/i `"$msi`" /qn /norestart" -Verb RunAs -Wait -PassThru
    if ($p.ExitCode -ne 0) { throw "Chrome Remote Desktop install failed (code $($p.ExitCode))." }
}

# 4. Keep the PC reachable: never sleep while plugged in
Step 'Power settings (no sleep while plugged in)'
try {
    powercfg /change standby-timeout-ac 0
    powercfg /change hibernate-timeout-ac 0
    Write-Host 'Done.'
} catch { Write-Host 'Could not change power settings - set Sleep to "Never" manually.' -ForegroundColor Yellow }

# 5. Finish: one-time sign-in that links this PC to your Google account
Step 'Last step - in the browser page that just opened:'
Write-Host '  1. Sign in with a Google account (use the same one on your iPhone).'
Write-Host '  2. Under "Set up remote access", click "Turn on", name the PC, pick a 6+ digit PIN.'
Write-Host '  3. On your iPhone, install "Chrome Remote Desktop" from the App Store, sign in, tap this PC.'
Start-Process 'https://remotedesktop.google.com/access'

Write-Host "`nAll set. Open Roblox Studio from your iPhone through Chrome Remote Desktop." -ForegroundColor Green

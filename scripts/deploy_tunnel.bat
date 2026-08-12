@echo off
REM Cloudflare quick tunnel to local POONG API
cd /d "%~dp0.."
if not exist "tools\cloudflared.exe" (
  echo Missing tools\cloudflared.exe — download from https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/installation/
  exit /b 1
)
echo Starting tunnel to http://127.0.0.1:8000
echo Open the printed URL with /?token=YOUR_ACCESS_TOKEN
tools\cloudflared.exe tunnel --url http://127.0.0.1:8000 --no-autoupdate

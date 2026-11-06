# YouTube History Songs

Builds a Mac app and a Windows folder with an `.exe`. The `.exe` is not one loose file: share the whole `dist/YTHistoryMusic` folder.

## Mac app, on a Mac

```bash
cd "/Users/mohammed.farhank/Desktop/YT downloader"
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt pyinstaller
curl -L "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-macosarm64-gpl.zip" -o ffmpeg.zip
unzip -j ffmpeg.zip "*/bin/ffmpeg" -d .
chmod +x ffmpeg
curl -L "https://github.com/denoland/deno/releases/latest/download/deno-aarch64-apple-darwin.zip" -o deno.zip
unzip -o deno.zip
chmod +x deno
.venv/bin/pyinstaller --noconfirm YTHistoryMusic.spec
open dist/YTHistoryMusic.app
```

That ffmpeg download is for Apple Silicon. On an Intel Mac, use `ffmpeg-master-latest-macos64-gpl.zip` instead.

The first time the app reads Chrome, macOS asks for the login keychain password. Allow it once. Later runs reuse the saved login and do not ask again. To sign in again, delete:

```bash
rm "$HOME/Library/Application Support/YTHistoryMusic/cookies-chrome.txt"
```

On another Mac, the app is not notarized. Right-click it, choose Open, then Open again.

## Mac app, without a Mac

1. Create a GitHub repository and push this project:

```bash
cd "/Users/mohammed.farhank/Desktop/YT downloader"
git init
git add .
git commit -m "Add YouTube history song downloader"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/yt-music-downloader.git
git push -u origin main
```

2. Open the repository on GitHub and click **Actions**.
3. Open **Build Mac app and Windows exe**. If it is not already running, click **Run workflow**.
4. When the run is green, download the **YTHistoryMusic-macOS** artifact.

The GitHub Mac build is Apple Silicon.

## Windows exe, on Windows

In PowerShell, from the project folder:

```powershell
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt pyinstaller
Invoke-WebRequest -Uri "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip" -OutFile "ffmpeg.zip"
Expand-Archive -Path "ffmpeg.zip" -DestinationPath "ffmpeg_extracted"
Get-ChildItem -Path "ffmpeg_extracted" -Filter "ffmpeg.exe" -Recurse | Copy-Item -Destination "ffmpeg.exe"
Invoke-WebRequest -Uri "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip" -OutFile "deno.zip"
Expand-Archive -Path "deno.zip" -DestinationPath "."
.venv\Scripts\pyinstaller --noconfirm YTHistoryMusic.spec
```

The app is `dist\YTHistoryMusic\YTHistoryMusic.exe`. Zip the whole `dist\YTHistoryMusic` folder before you send it. The `.exe` alone will not start.

## Windows exe, without Windows

Use the same GitHub Actions run as the Mac build. Download the **YTHistoryMusic-Windows** artifact. Unzip it and run `YTHistoryMusic.exe`.

# YouTube History Songs

You already listen to songs on YouTube. This app looks through that watch history, keeps the songs, and saves them as music files on your computer.

It works for songs in any language. A Tamil film song, a Hindi track, a Korean release, and an English song are all treated as music. Cooking videos, tutorials, news, reviews, and short clips are left out.

## How to use it

1. Open the app.
2. Choose the browser you use for YouTube. You need to be signed in there.
3. Choose how far back to look, or leave it at the last 100 videos.
4. Press **Start**.

While it runs you see the song being saved right now, a few songs waiting after it, and the songs already finished. You can pause, skip the current song, or stop after the current song.

Finished songs go to your Downloads folder, in a folder named `Downloaded_Songs`. Click a saved song in the app to show it in Finder on a Mac, or in File Explorer on Windows. If a song was saved before, it shows as already saved and is not downloaded again.

The first time the app reads Chrome on a Mac, the computer asks for your login password. That lets it see which videos you watched. Allow it once. The next time you press Start, it does not ask again.

## Building the app

The sections below are for making the Mac app and the Windows program from this source code. You can skip them if you already have the app.

### On a Mac

```bash
cd "/Users/mohammed.farhank/Desktop/YT downloader"
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt pyinstaller
curl -L "https://github.com/eugeneware/ffmpeg-static/releases/latest/download/ffmpeg-darwin-arm64.gz" -o ffmpeg.gz
gunzip -c ffmpeg.gz > ffmpeg
chmod +x ffmpeg
curl -L "https://github.com/denoland/deno/releases/latest/download/deno-aarch64-apple-darwin.zip" -o deno.zip
unzip -o deno.zip
chmod +x deno
.venv/bin/pyinstaller --noconfirm YTHistoryMusic.spec
open dist/YTHistoryMusic.app
```

That ffmpeg download is for Apple Silicon. On an Intel Mac, download `ffmpeg-darwin-x64.gz` from the same place instead.

The finished app is `dist/YTHistoryMusic.app`. On another Mac, right-click the app, choose Open, then Open again the first time.

To make the app ask for the Chrome login again, delete the saved login:

```bash
rm "$HOME/Library/Application Support/YTHistoryMusic/cookies-chrome.txt"
```

### On Windows

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

The program is the single file `dist\YTHistoryMusic.exe`. Send that file. The first open is slower because Windows unpacks it.

### Without a Mac or without Windows

GitHub can build both for you.

1. Open [the Actions page](https://github.com/sret-farhan2021/YToffed/actions).
2. Open **Build Mac app and Windows exe**.
3. When the run is green, download **YTHistoryMusic-macOS** or **YTHistoryMusic-Windows**.
4. Unzip the download. On a Mac, open the app. On Windows, run `YTHistoryMusic.exe`. GitHub wraps that file in a zip, so unzip it first and then open the exe. Opening the exe from inside the zip makes Windows show “Failed to start embedded python interpreter.”

The Mac file from GitHub is for Apple Silicon.

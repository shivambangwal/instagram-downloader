/Users/shivambangwal/.zprofile:2: no such file or directory: /usr/local/bin/brew
# Instagram Downloader

A local app for saving Instagram Reels and video posts you own or have permission to save. It downloads the best available video and audio through yt-dlp, uses captions for filenames when available, shows progress, and can stop a running download.

The app runs on macOS and Windows. Its HTML interface is in [`web/index.html`](web/index.html); it needs the local Python server for previewing and downloading, so opening the HTML file directly or hosting it as a static GitHub Pages site will not run the downloader.

## Requirements

- Python 3.11 or newer
- Internet access on first launch so the app can install its private yt-dlp and FFmpeg runtime in `.runtime/`
- A modern browser

The app binds only to `127.0.0.1` and saves files under `Downloads/Instagram` in your home folder.

## macOS

1. Double-click [`Start Instagram Downloader.command`](Start%20Instagram%20Downloader.command).
2. If macOS asks whether to open the script, choose **Open**.
3. The app opens at <http://127.0.0.1:8766/>. Keep the Terminal window open while using it.

You can also run `python3 app.py` from this folder.

## Windows

1. Install Python 3.11 or newer and enable the Python launcher (`py`) during installation.
2. Double-click [`Start Instagram Downloader.bat`](Start%20Instagram%20Downloader.bat), or run [`Start Instagram Downloader.ps1`](Start%20Instagram%20Downloader.ps1) from PowerShell.
3. The app opens at <http://127.0.0.1:8766/>. Keep the console window open while using it.

If PowerShell blocks the script, use the `.bat` launcher instead.

## Browser helper for profile Reels

yt-dlp's Instagram profile listing can be unavailable. The optional Chrome helper scans Reel links visible on a profile tab you choose and imports up to 300 links into the app's selection list. Stories are skipped. The helper does not ask for a password or read browser cookies.

1. Start the local app and keep it running.
2. In Chrome, open `chrome://extensions`, turn on **Developer mode**, choose **Load unpacked**, and select the `chrome-extension` folder.
3. Sign in to Instagram directly in Chrome and open the profile.
4. Click **Instagram Downloader Local Helper** and choose **Scan and send Reels**.
5. Select specific Reels or download all scanned links in the local app.

Chrome grants temporary access to the active tab when you use the helper. Its persistent site permission is limited to the local app at `127.0.0.1:8766`.

## HTML interface

The interface lives in [`web/index.html`](web/index.html). Open the app through one of the launchers so the page can connect to the local API. A static HTML copy by itself cannot retrieve Instagram media or save files.

## Notes and limits

- Downloads depend on Instagram making each media URL available to yt-dlp. Finding a Reel in a signed-in browser does not guarantee yt-dlp can download it.
- Profile scanning is limited to 300 Reels per scan. Stories are excluded.
- Instagram may change its pages or limit access; some links may fail.
- Use the app only for your own content or media you have permission to save.
- No Instagram password or cookie file is requested or stored.

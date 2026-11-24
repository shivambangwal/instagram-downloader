#!/usr/bin/env python3
"""Local Instagram media downloader for content the user may save."""

from __future__ import annotations

import importlib
import importlib.util
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit


BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
RUNTIME_DIR = BASE_DIR / ".runtime"
DOWNLOADS_DIR = Path(os.environ.get("IG_DOWNLOADER_DOWNLOAD_DIR", str(Path.home() / "Downloads" / "Instagram"))).expanduser().resolve()
HOST = "127.0.0.1"
PORT = int(os.environ.get("IG_DOWNLOADER_PORT", "8766"))
CHROME_EXTENSION_ID = "lngcjapofbnoiedhbjkcpdeeedljnfbp"

sys.path.insert(0, str(RUNTIME_DIR))

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.RLock()
PREVIEWS: dict[str, list[str]] = {}
EXTENSION_IMPORT: dict | None = None


def ffmpeg_executable() -> str | None:
    try:
        import imageio_ffmpeg
        path = imageio_ffmpeg.get_ffmpeg_exe()
        return path if Path(path).is_file() else None
    except (ImportError, OSError, RuntimeError):
        return shutil.which("ffmpeg")


def tools_ready() -> dict[str, bool]:
    return {
        "yt_dlp": importlib.util.find_spec("yt_dlp") is not None,
        "ffmpeg": ffmpeg_executable() is not None,
    }


def yt_dlp_version() -> str:
    try:
        from yt_dlp.version import __version__
        return str(__version__)
    except (ImportError, AttributeError):
        return "installed version"


def ensure_tools() -> None:
    if sys.version_info < (3, 11):
        raise RuntimeError("Python 3.11 or newer is required to set up the downloader.")
    status = tools_ready()
    if all(status.values()):
        return
    print("Setting up the downloader's private yt-dlp and FFmpeg tools (first launch only)…")
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([
        sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--upgrade",
        "--target", str(RUNTIME_DIR), "yt-dlp[default]", "imageio-ffmpeg",
    ])
    importlib.invalidate_caches()
    status = tools_ready()
    if result.returncode != 0 or not all(status.values()):
        missing = ", ".join(name for name, ready in status.items() if not ready)
        raise RuntimeError(f"Could not install the app-local tools ({missing or 'pip failed'}). Check your internet connection and launch again.")


def classify_instagram_url(raw_url: str) -> tuple[str, str]:
    value = raw_url.strip()
    if not value:
        raise ValueError("Paste an Instagram profile, post, or Reel link first.")
    candidate = value if "://" in value else "https://" + value
    parsed = urlsplit(candidate)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or host not in {"instagram.com", "www.instagram.com", "instagr.am", "www.instagr.am"}:
        raise ValueError("Use a link from instagram.com or instagr.am.")
    if parsed.username or parsed.password or parsed.port not in {None, 80, 443}:
        raise ValueError("That Instagram link is not valid.")
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        raise ValueError("Paste a link to an Instagram profile, post, or Reel.")
    if parts[0].lower() == "stories":
        raise ValueError("Stories are not supported. Paste a profile, post, or Reel link.")
    if parts[0].lower() in {"p", "reel", "reels", "tv"}:
        if len(parts) < 2:
            raise ValueError("That Instagram media link is incomplete.")
        media_url = f"https://www.instagram.com/{parts[0]}/{parts[1]}/"
        return media_url, "media"
    reserved = {"accounts", "about", "developer", "direct", "explore", "legal", "stories", "web"}
    if len(parts) == 1 or (len(parts) == 2 and parts[1].lower() == "reels"):
        if parts[0].lower() in reserved:
            raise ValueError("Paste a link to an Instagram profile, post, or Reel.")
        return f"https://www.instagram.com/{parts[0]}/", "profile"
    raise ValueError("Paste a profile link or one Instagram post/Reel link.")


def video_url_from_entry(entry: dict, index: int) -> str | None:
    candidate = entry.get("webpage_url") or entry.get("original_url") or entry.get("url")
    if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
        try:
            url, kind = classify_instagram_url(candidate)
            return url if kind == "media" else None
        except ValueError:
            pass
    shortcode = entry.get("id")
    if isinstance(shortcode, str) and shortcode:
        return f"https://www.instagram.com/p/{shortcode}/"
    return None


def manual_link_preview(raw_urls: object) -> dict:
    if not isinstance(raw_urls, list) or not raw_urls:
        raise ValueError("Paste one or more Reel/post links, one per line.")
    if len(raw_urls) > 300:
        raise ValueError("Paste up to 300 Reel/post links at a time.")
    urls = []
    videos = []
    for line_number, raw in enumerate(raw_urls, start=1):
        try:
            url, kind = classify_instagram_url(str(raw))
        except ValueError as error:
            raise ValueError(f"Link {line_number}: {error}") from error
        if kind != "media":
            raise ValueError(f"Link {line_number}: paste a direct Reel or video-post link, not a profile or Stories link.")
        if url in urls:
            continue
        parts = [part for part in urlsplit(url).path.split("/") if part]
        shortcode = parts[-1] if parts else f"{len(videos) + 1}"
        videos.append({
            "key": str(len(urls)), "title": f"Reel/post {len(videos) + 1} · {shortcode}",
            "thumbnail": "", "duration": None,
        })
        urls.append(url)
    if not urls:
        raise ValueError("No valid Reel/post links were found.")
    preview_id = str(uuid.uuid4())
    PREVIEWS[preview_id] = urls
    if len(PREVIEWS) > 40:
        PREVIEWS.pop(next(iter(PREVIEWS)))
    return {"preview_id": preview_id, "videos": videos, "manual": True}


def extract_preview(url: str, is_profile: bool) -> dict:
    command = [sys.executable, "-m", "yt_dlp", "--no-warnings", "--skip-download", "--dump-single-json"]
    if is_profile:
        command.extend(["--flat-playlist", "--playlist-end", "300"])
    command.append(url)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(RUNTIME_DIR) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    ffmpeg_path = ffmpeg_executable()
    if ffmpeg_path:
        env["PATH"] = str(Path(ffmpeg_path).parent) + os.pathsep + env.get("PATH", "")
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=150)
    if result.returncode != 0:
        output = f"{result.stderr}\n{result.stdout}"
        if is_profile and "unable to extract data" in output.lower():
            raise ValueError(
                f"Instagram profile listing is currently broken in yt-dlp {yt_dlp_version()}. "
                "Use individual public Reel links; Choose videos can load a pasted list of links. Stories are not included."
            )
        detail = output.strip().splitlines()
        hint = detail[-1] if detail else "Instagram did not return a video list."
        raise ValueError(f"Could not read this link: {hint}")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ValueError("Instagram returned an unreadable media list. Try again later.") from error

    entries = data.get("entries") if isinstance(data, dict) else None
    if entries is None:
        entries = data.get("entries") if isinstance(data, dict) and data.get("_type") == "playlist" else [data]
    videos = []
    urls = []
    for index, entry in enumerate(entries or []):
        if not isinstance(entry, dict):
            continue
        formats = entry.get("formats") or []
        has_video = bool(entry.get("duration") and entry.get("duration") > 0) or any(
            item.get("vcodec") not in {None, "none"} for item in formats if isinstance(item, dict)
        )
        # The flat profile extractor emits URL entries only for GraphVideo nodes.
        if is_profile and entry.get("_type") == "url":
            has_video = True
        if not has_video:
            continue
        media_url = video_url_from_entry(entry, index)
        if not media_url or media_url in urls:
            continue
        urls.append(media_url)
        thumbnail = entry.get("thumbnail")
        if not thumbnail and entry.get("thumbnails"):
            thumbnail = entry["thumbnails"][-1].get("url")
        title = (entry.get("title") or entry.get("description") or f"Video {len(videos) + 1}").strip()
        videos.append({
            "key": str(len(videos)), "title": title[:160], "thumbnail": thumbnail or "",
            "duration": entry.get("duration"),
        })
    if not videos:
        raise ValueError("No videos were found. Instagram may be limiting this profile, or the link may contain photos only.")
    preview_id = str(uuid.uuid4())
    PREVIEWS[preview_id] = urls
    # Keep memory bounded when the app is used for many profiles.
    if len(PREVIEWS) > 40:
        PREVIEWS.pop(next(iter(PREVIEWS)))
    return {"preview_id": preview_id, "videos": videos}


def json_response(handler: BaseHTTPRequestHandler, payload: dict, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    if (handler.headers.get("Origin") == f"chrome-extension://{CHROME_EXTENSION_ID}"
            and urlsplit(handler.path).path == "/api/extension/import"):
        handler.send_header("Access-Control-Allow-Origin", f"chrome-extension://{CHROME_EXTENSION_ID}")
        handler.send_header("Vary", "Origin")
    handler.end_headers()
    handler.wfile.write(body)


def safe_download_path(path_value: str) -> Path | None:
    try:
        path = Path(path_value).expanduser().resolve()
        path.relative_to(DOWNLOADS_DIR)
        return path if path.is_file() else None
    except (OSError, ValueError):
        return None


def files_for(job: dict) -> list[dict]:
    files = []
    for path_value in job.get("files", []):
        path = safe_download_path(path_value)
        if path:
            files.append({"name": path.name, "size": path.stat().st_size, "url": f"/api/jobs/{job['id']}/files/{len(files)}"})
    return files


def public_job(job: dict) -> dict:
    return {
        "id": job["id"], "status": job["status"], "progress": job.get("progress", 0),
        "current": job.get("current", ""), "message": job.get("message", ""),
        "logs": job.get("logs", [])[-30:], "files": files_for(job),
    }


def add_log(job_id: str, line: str) -> None:
    line = line.strip()
    if not line:
        return
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        job.setdefault("logs", []).append(line)
        job["logs"] = job["logs"][-80:]
        match = re.search(r"\[download\]\s+(\d+(?:\.\d+)?)%", line)
        if match:
            job["progress"] = min(100, float(match.group(1)))
            job["message"] = "Downloading at the highest available quality…"
        if "Destination:" in line:
            job["current"] = line.split("Destination:", 1)[1].strip()
        elif "Merging formats into" in line or "Remuxing video from" in line:
            job["message"] = "Finishing the highest-quality video…"
        elif line.startswith("IG_FILE:"):
            path = line.removeprefix("IG_FILE:").strip()
            if path and path not in job.setdefault("files", []):
                job["files"].append(path)


def run_download(job_id: str, urls: list[str]) -> None:
    output_template = "%(uploader,channel|Instagram)s/%(description,title|Instagram video).190B [%(id)s].%(ext)s"
    command = [
        sys.executable, "-m", "yt_dlp", "--newline", "--progress", "--continue", "--no-overwrites",
        "--no-warnings", "--no-restrict-filenames", "--trim-filenames", "240",
        "--replace-in-metadata", "description", r"[\r\n\t]+", " ",
        "-f", "bv*+ba/b", "--merge-output-format", "mp4",
        "-P", str(DOWNLOADS_DIR), "-o", output_template,
        "--print", "after_move:IG_FILE:%(filepath)s",
    ]
    command.extend(urls)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(RUNTIME_DIR) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    ffmpeg_path = ffmpeg_executable()
    if ffmpeg_path:
        env["PATH"] = str(Path(ffmpeg_path).parent) + os.pathsep + env.get("PATH", "")
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                   encoding="utf-8", errors="replace", env=env, bufsize=1)
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if not job:
                process.terminate()
                return
            job["process"] = process
            if job.get("cancel_requested"):
                process.terminate()
        assert process.stdout is not None
        for line in process.stdout:
            add_log(job_id, line)
        return_code = process.wait()
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if not job:
                return
            job.pop("process", None)
            if job.get("cancel_requested"):
                job["status"] = "cancelled"
                job["message"] = "Stopped. Any completed files are kept in Downloads/Instagram."
            elif return_code == 0:
                job["status"] = "complete"
                job["progress"] = 100
                job["message"] = "Finished. Your files are in Downloads/Instagram."
            else:
                job["status"] = "error"
                recent = " ".join(job.get("logs", [])[-4:])
                if job.get("profile") and "unable to extract data" in recent.lower():
                    job["message"] = (
                        f"Instagram profile listing is currently broken in yt-dlp {yt_dlp_version()}. "
                        "Use individual public Reel links or Choose videos to load a pasted list. Stories are not included."
                    )
                else:
                    job["message"] = "Instagram did not provide this media. The link may be private, unavailable, or temporarily unsupported." if not recent else "Download stopped with an error. Check the details below."
    except Exception as error:
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job:
                job.pop("process", None)
                job["status"] = "error"
                job["message"] = str(error)


class Handler(BaseHTTPRequestHandler):
    server_version = "IGDownloader/1.0"

    def log_message(self, format: str, *args) -> None:
        print("[web] " + format % args)

    def allowed_request(self, request_path: str) -> bool:
        host = self.headers.get("Host", "").split(":", 1)[0].strip("[]").lower()
        if host not in {"127.0.0.1", "localhost"}:
            self.send_error(403, "Local app only")
            return False
        origin = self.headers.get("Origin")
        if origin:
            parsed = urlsplit(origin)
            local_origin = parsed.scheme in {"http", "https"} and parsed.hostname in {"127.0.0.1", "localhost"}
            extension_origin = (
                origin == f"chrome-extension://{CHROME_EXTENSION_ID}"
                and request_path == "/api/extension/import"
            )
            if not local_origin and not extension_origin:
                self.send_error(403, "Local app only")
                return False
        return True

    def read_json(self) -> dict:
        try:
            length = min(int(self.headers.get("Content-Length", "0")), 100_000)
            return json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            return {}

    def do_GET(self) -> None:
        path = unquote(urlsplit(self.path).path)
        if not self.allowed_request(path):
            return
        if path == "/" or path == "/index.html":
            self.send_file(WEB_DIR / "index.html", "text/html; charset=utf-8")
            return
        if path == "/api/status":
            json_response(self, {**tools_ready(), "download_dir": str(DOWNLOADS_DIR)})
            return
        if path == "/api/extension/latest":
            global EXTENSION_IMPORT
            with JOBS_LOCK:
                imported = EXTENSION_IMPORT
                EXTENSION_IMPORT = None
            json_response(self, {"import": imported})
            return
        match = re.fullmatch(r"/api/jobs/([a-f0-9-]+)(?:/files(?:/(\d+))?)?", path)
        if match:
            job_id, file_index = match.groups()
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if not job:
                    json_response(self, {"error": "Download job not found."}, 404)
                    return
                if file_index is None:
                    if path.endswith("/files"):
                        json_response(self, {"files": files_for(job)})
                    else:
                        json_response(self, public_job(job))
                    return
                files = files_for(job)
                index = int(file_index)
                path_obj = safe_download_path(job.get("files", [])[index]) if index < len(job.get("files", [])) else None
            if not path_obj:
                json_response(self, {"error": "File is not available."}, 404)
                return
            self.send_file(path_obj, mimetypes.guess_type(path_obj.name)[0] or "application/octet-stream", attachment=True)
            return
        self.send_error(404)

    def do_POST(self) -> None:
        path = unquote(urlsplit(self.path).path)
        if not self.allowed_request(path):
            return
        body = self.read_json()
        if path == "/api/extension/import":
            global EXTENSION_IMPORT
            if self.headers.get("Origin") != f"chrome-extension://{CHROME_EXTENSION_ID}":
                json_response(self, {"error": "Import requests must come from the installed Instagram Downloader helper."}, 403)
                return
            try:
                profile_url, kind = classify_instagram_url(str(body.get("profile_url", "")))
                if kind != "profile":
                    raise ValueError("Open an Instagram profile page before scanning Reels.")
                raw_urls = body.get("urls")
                if not isinstance(raw_urls, list) or not raw_urls or len(raw_urls) > 300:
                    raise ValueError("The helper must send between 1 and 300 Reel links.")
                urls = []
                for raw in raw_urls:
                    media_url, media_kind = classify_instagram_url(str(raw))
                    if media_kind != "media" or "/reel/" not in urlsplit(media_url).path.lower():
                        continue
                    if media_url not in urls:
                        urls.append(media_url)
                if not urls:
                    raise ValueError("No Reel links were found on that profile page.")
                username = str(body.get("username", "")).strip()[:80]
                with JOBS_LOCK:
                    EXTENSION_IMPORT = {"username": username, "profile_url": profile_url, "urls": urls}
                json_response(self, {"ok": True, "count": len(urls)})
            except ValueError as error:
                json_response(self, {"error": str(error)}, 400)
            return
        if path == "/api/preview":
            try:
                if "urls" in body:
                    result = manual_link_preview(body.get("urls"))
                else:
                    url, kind = classify_instagram_url(str(body.get("url", "")))
                    result = extract_preview(url, kind == "profile")
                json_response(self, result)
            except (ValueError, subprocess.TimeoutExpired) as error:
                message = str(error) if isinstance(error, ValueError) else "Instagram took too long to return the video list. Try again."
                json_response(self, {"error": message}, 400)
            return
        if path == "/api/download":
            try:
                profile_job = False
                if body.get("preview_id"):
                    preview_id = str(body.get("preview_id"))
                    selected = body.get("selected")
                    if not isinstance(selected, list) or not selected:
                        raise ValueError("Select at least one video first.")
                    preview_urls = PREVIEWS.get(preview_id)
                    if not preview_urls:
                        raise ValueError("That video list expired. Load the profile again.")
                    urls = []
                    for key in selected:
                        try:
                            index = int(key)
                        except (TypeError, ValueError) as error:
                            raise ValueError("The selected video list is invalid. Load the profile again.") from error
                        if index < 0 or index >= len(preview_urls):
                            raise ValueError("The selected video list is invalid. Load the profile again.")
                        if preview_urls[index] not in urls:
                            urls.append(preview_urls[index])
                else:
                    url, kind = classify_instagram_url(str(body.get("url", "")))
                    if kind == "profile":
                        if not body.get("all_profile"):
                            raise ValueError("Choose Download all videos or load the profile to select specific videos.")
                        profile_job = True
                    urls = [url]
                job_id = str(uuid.uuid4())
                with JOBS_LOCK:
                    JOBS[job_id] = {
                        "id": job_id, "status": "running", "progress": 0, "current": "",
                        "message": "Starting the download…", "logs": [], "files": [],
                        "started_at": datetime.now().isoformat(), "cancel_requested": False,
                        "profile": profile_job,
                    }
                threading.Thread(target=run_download, args=(job_id, urls), daemon=True).start()
                json_response(self, {"id": job_id})
            except ValueError as error:
                json_response(self, {"error": str(error)}, 400)
            return
        match = re.fullmatch(r"/api/jobs/([a-f0-9-]+)/cancel", path)
        if match:
            job_id = match.group(1)
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if not job:
                    json_response(self, {"error": "Download job not found."}, 404)
                    return
                process = job.get("process")
                job["cancel_requested"] = True
                job["message"] = "Stopping the download…"
                if process and process.poll() is None:
                    process.terminate()
            json_response(self, {"ok": True})
            return
        self.send_error(404)

    def do_OPTIONS(self) -> None:
        path = unquote(urlsplit(self.path).path)
        if not self.allowed_request(path) or path != "/api/extension/import":
            return
        if self.headers.get("Origin") != f"chrome-extension://{CHROME_EXTENSION_ID}":
            self.send_error(403, "Local app only")
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", f"chrome-extension://{CHROME_EXTENSION_ID}")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        if self.headers.get("Access-Control-Request-Private-Network") == "true":
            self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def send_file(self, path: Path, content_type: str, attachment: bool = False) -> None:
        try:
            size = path.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-store")
            if attachment:
                self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(path.name)}")
            self.end_headers()
            with path.open("rb") as file:
                shutil.copyfileobj(file, self.wfile)
        except OSError:
            self.send_error(404)


def main() -> None:
    try:
        ensure_tools()
    except RuntimeError as error:
        print(f"Setup error: {error}", file=sys.stderr)
        raise SystemExit(1)
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}/"
    print(f"Instagram Downloader is ready at {url}")
    threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nInstagram Downloader stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

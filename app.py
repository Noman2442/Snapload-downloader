from flask import Flask, request, render_template, jsonify, Response, stream_with_context
import yt_dlp
import requests
from concurrent.futures import ThreadPoolExecutor
import threading

app = Flask(__name__)

# Thread pool for concurrent streaming (speeds up proxy downloads)
executor = ThreadPoolExecutor(max_workers=8)

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/get-video', methods=['POST'])
def get_video():
    url = request.form.get('url')
    if not url:
        return jsonify({"error": "Please paste a valid video link!"})

    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'format': 'bestvideo+bestaudio/best',   # ← better quality selection
        'merge_output_format': 'mp4',
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

            title = info.get('title', 'Social Media Video')
            thumbnail = info.get('thumbnail', '')
            duration_secs = info.get('duration', 0)

            if duration_secs:
                mins = duration_secs // 60
                secs = duration_secs % 60
                duration_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"
            else:
                duration_str = "Unknown"

            formats_available = []
            seen_qualities = set()

            for f in info.get('formats', []):
                if f.get('url'):
                    height = f.get('height')
                    if height and f.get('vcodec') != 'none':
                        if height >= 1080 and "1080p Full HD" not in seen_qualities:
                            formats_available.append({"quality": "1080p Full HD", "url": f['url']})
                            seen_qualities.add("1080p Full HD")
                        elif height >= 720 and "720p HD" not in seen_qualities:
                            formats_available.append({"quality": "720p HD", "url": f['url']})
                            seen_qualities.add("720p HD")
                        elif height >= 480 and "480p Medium" not in seen_qualities:
                            formats_available.append({"quality": "480p Medium", "url": f['url']})
                            seen_qualities.add("480p Medium")
                        elif height >= 360 and "360p Low" not in seen_qualities:
                            formats_available.append({"quality": "360p Low", "url": f['url']})
                            seen_qualities.add("360p Low")
                    elif f.get('acodec') != 'none' and f.get('vcodec') == 'none':
                        if "Audio Only (MP3/M4A)" not in seen_qualities:
                            formats_available.append({"quality": "Audio Only (MP3/M4A)", "url": f['url']})
                            seen_qualities.add("Audio Only (MP3/M4A)")

            best_url = info.get('url', '')
            if best_url:
                if not any("HD" in q['quality'] for q in formats_available):
                    formats_available.insert(0, {"quality": "720p HD (Standard)", "url": best_url})
                if not any("1080p" in q['quality'] for q in formats_available) and info.get('height', 0) >= 1080:
                    formats_available.insert(0, {"quality": "1080p Full HD", "url": best_url})
                if not any("Audio" in q['quality'] for q in formats_available):
                    formats_available.append({"quality": "Audio Only (MP3/M4A)", "url": best_url})

            return jsonify({
                "title": title,
                "thumbnail": thumbnail,
                "duration": duration_str,
                "formats": formats_available
            })

    except Exception as e:
        return jsonify({"error": "The link is invalid or this platform is currently not supported."})


# ⚡ HIGH-SPEED STREAMING PROXY
# Uses large chunk size (1MB) + passes Content-Length so browser shows real progress
@app.route('/direct-download')
def direct_download():
    video_url = request.args.get('url')
    filename = request.args.get('filename', 'video.mp4')

    if not video_url:
        return "URL missing", 400

    HEADERS = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/124.0.0.0 Safari/537.36'
        ),
        'Accept': '*/*',
        'Accept-Encoding': 'identity',   # ← prevent gzip so Content-Length is accurate
        'Connection': 'keep-alive',
        'Referer': 'https://www.youtube.com/',
    }

    try:
        req = requests.get(
            video_url,
            stream=True,
            headers=HEADERS,
            timeout=60,
            verify=True
        )
        req.raise_for_status()

        # 1 MB chunks → much faster than 256 KB
        CHUNK_SIZE = 1024 * 1024  # 1 MB

        def generate():
            try:
                for chunk in req.iter_content(chunk_size=CHUNK_SIZE):
                    if chunk:
                        yield chunk
            except Exception:
                pass  # client disconnected – just stop

        is_audio = filename.lower().endswith('.mp3')
        content_type = 'audio/mpeg' if is_audio else req.headers.get('Content-Type', 'video/mp4')

        response_headers = {
            'Content-Disposition': f'attachment; filename="{filename}"',
            'Content-Type': content_type,
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',    # ← disables nginx buffering if behind nginx
        }

        # Pass Content-Length if available so frontend can show real progress %
        content_length = req.headers.get('Content-Length')
        if content_length:
            response_headers['Content-Length'] = content_length

        # Pass Accept-Ranges so browser can resume if interrupted
        if req.headers.get('Accept-Ranges'):
            response_headers['Accept-Ranges'] = req.headers.get('Accept-Ranges')

        return Response(
            stream_with_context(generate()),
            headers=response_headers,
            status=req.status_code
        )

    except requests.exceptions.Timeout:
        return "Request timed out. The video server took too long.", 504
    except requests.exceptions.ConnectionError:
        return "Could not connect to video server.", 502
    except Exception as e:
        return f"Download error: {str(e)}", 500


if __name__ == '__main__':
    # threaded=True is important for concurrent downloads
    app.run(debug=True, threaded=True)
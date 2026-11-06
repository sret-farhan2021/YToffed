import yt_dlp

from app import get_binary_path, prepare_cookie_file, video_url
from music_filter import classify_flat, classify_full

BROWSER = "chrome"
LIMIT = 50


def main():
    deno_bin = get_binary_path("deno")
    cookies = prepare_cookie_file(BROWSER)
    opts = {
        "cookiefile": cookies,
        "extract_flat": True,
        "quiet": True,
        "no_warnings": True,
        "playlistend": LIMIT,
        "js_runtimes": {"deno": {"path": deno_bin}},
    }
    meta_opts = {
        "cookiefile": cookies,
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "js_runtimes": {"deno": {"path": deno_bin}},
    }
    print(f"Auditing last {LIMIT} history entries from {BROWSER.capitalize()}...\n")
    matched = 0
    skipped = 0
    with yt_dlp.YoutubeDL(opts) as scanner, yt_dlp.YoutubeDL(meta_opts) as meta:
        feed = scanner.extract_info("https://www.youtube.com/feed/history", download=False)
        for idx, entry in enumerate(feed.get("entries") or [], 1):
            if not entry:
                continue
            title = entry.get("title") or "Unknown"
            decision, reason = classify_flat(entry)
            if decision == "maybe":
                try:
                    full = meta.extract_info(video_url(entry), download=False)
                except Exception as exc:
                    decision, reason = "junk", f"Could not check ({exc})"
                else:
                    keep, reason = classify_full(full or {})
                    decision = "music" if keep else "junk"
            if decision == "music":
                matched += 1
                print(f"[{idx}] [KEEP] {title[:70]}")
                print(f"      -> {reason}")
            else:
                skipped += 1
                print(f"[{idx}] [SKIP] {title[:70]}")
                print(f"      -> {reason}")
    print(f"\nAudit complete: {matched} songs kept, {skipped} skipped.")


if __name__ == "__main__":
    main()

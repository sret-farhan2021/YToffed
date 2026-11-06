"""Decide whether a YouTube entry is a song, in any language."""

import os
import re

MIN_SECONDS = 75
MAX_SECONDS = 12 * 60

# Strong markers beat a junk word. "How to Say Sorry (feat. …)" stays.
_STRONG_PHRASES = (
    "lyric",
    "official audio",
    "official video",
    "official music",
    "music video",
    "visualizer",
    "soundtrack",
    "bass boosted",
    "video song",
    "audio song",
    "title track",
    "slowed",
    "sped up",
    "sped-up",
    "video oficial",
    "audio oficial",
    "clip officiel",
    "பாடல்",
    "பாட்டு",
    "गीत",
    "गाना",
    "노래",
    "뮤직비디오",
    "가사",
    "歌詞",
    "主題歌",
    "أغنية",
)
_STRONG_RES = tuple(
    re.compile(pat, re.IGNORECASE)
    for pat in (
        r"\bremix\b",
        r"\bmashups?\b",
        r"\bbgm\b",
        r"\bost\b",
        r"\bmv\b",
        r"\b(?:feat|ft)\.",
        r"\bfeat\b",
    )
)

# Weaker hints. A junk title still loses, so "tutorial video" is not a song.
_WEAK_RES = tuple(
    re.compile(pat, re.IGNORECASE)
    for pat in (
        r"\bvideos?\b",
        r"\baudios?\b",
        r"\bsongs?\b",
        r"\banthems?\b",
        r"\bcovers?\b",
        r"\bcovered\b",
        r"\bkaraoke\b",
    )
)

_JUNK_RES = tuple(
    re.compile(pat, re.IGNORECASE)
    for pat in (
        r"\bhow\s*to\b",
        r"\bhowto\b",
        r"\btutorials?\b",
        r"\brecipes?\b",
        r"\breviews?\b",
        r"\bunboxings?\b",
        r"\bgameplay\b",
        r"\bwalkthroughs?\b",
        r"\bpodcasts?\b",
        r"\bvlogs?\b",
        r"\bexplained\b",
        r"\bcosplay\b",
        r"\bcostumes?\b",
        r"\bdiy\b",
        r"\bvoicemails?\b",
        r"\bwordpress\b",
        r"\belementor\b",
        r"\bgutenberg\b",
        r"\bwidgets?\b",
        r"\bplugins?\b",
        r"\bminecraft\b",
        r"#shorts\b",
        r"\bshorts\b",
        r"\btrailers?\b",
        r"\bteasers?\b",
        r"\binterviews?\b",
        r"\bhighlights\b",
        r"\breactions?\b",
        r"\bepisodes?\b",
        r"\blectures?\b",
        r"\bcoding\b",
        r"\binstall\b",
        r"\bsetup\b",
        r"\bcooking\b",
        r"\bguides?\b",
        r"\bseason\s+\d",
        r"\bpost[\s-]?credits\b",
        r"\bwanted poster\b",
    )
)

_LABELS = (
    "records",
    "recordings",
    "saregama",
    "sony music",
    "t-series",
    "t series",
    "think music",
    "lahari",
    "aditya music",
    "tips official",
    "zee music",
    "zeemusic",
)


def _title(info: dict) -> str:
    return str(info.get("title") or "")


def _uploader(info: dict) -> str:
    return str(info.get("uploader") or info.get("channel") or "").lower()


def is_music_channel(uploader: str) -> bool:
    name = (uploader or "").lower()
    if name.endswith(" - topic") or " - topic" in name or "vevo" in name:
        return True
    return any(label in name for label in _LABELS)


def has_strong_marker(title: str) -> bool:
    folded = title.lower()
    if any(phrase in folded for phrase in _STRONG_PHRASES):
        return True
    return any(pattern.search(title) for pattern in _STRONG_RES)


def has_weak_marker(title: str) -> bool:
    return any(pattern.search(title) for pattern in _WEAK_RES)


def has_junk(title: str) -> bool:
    return any(pattern.search(title) for pattern in _JUNK_RES)


def _duration(info: dict):
    duration = info.get("duration")
    if isinstance(duration, (int, float)) and duration > 0:
        return duration
    return None


def classify_flat(info: dict) -> tuple[str, str]:
    """Return ('music'|'junk'|'maybe', reason).

    'maybe' needs a full video lookup. Junk is a title that is clearly not a
    song, or a clip that is far outside a normal song length.
    """
    title = _title(info)
    if has_strong_marker(title) or is_music_channel(_uploader(info)):
        return "music", "Music marker"

    if has_junk(title):
        return "junk", "Not a song"

    duration = _duration(info)
    if duration is not None and duration < MIN_SECONDS:
        return "junk", "Too short"
    if duration is not None and duration > MAX_SECONDS:
        return "junk", "Too long"

    if has_weak_marker(title):
        return "music", "Looks like a song"

    return "maybe", "Needs a closer look"


def classify_full(info: dict) -> tuple[bool, str]:
    """Decide from a full yt-dlp info dict. Language does not matter."""
    decision, reason = classify_flat(info)
    if decision == "music":
        return True, reason
    if decision == "junk":
        return False, reason

    categories = [str(c).lower() for c in (info.get("categories") or [])]
    if "music" in categories:
        return True, "Category: Music"
    if info.get("track") or info.get("artist") or info.get("album"):
        return True, "Music metadata"
    if is_music_channel(_uploader(info)):
        return True, "Music channel"
    return False, "No music metadata"


def load_archive_ids(path: str) -> set[str]:
    found = set()
    if not path or not os.path.isfile(path):
        return found
    with open(path, "r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            parts = line.split()
            if len(parts) >= 2:
                found.add(parts[-1])
    return found


def remove_archive_id(path: str, video_id: str) -> None:
    if not path or not video_id or not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8", errors="ignore") as handle:
        lines = handle.readlines()
    kept = [line for line in lines if video_id not in line.split()]
    if len(kept) == len(lines):
        return
    with open(path, "w", encoding="utf-8") as handle:
        handle.writelines(kept)


def obvious_non_song_filename(name: str) -> bool:
    stem = os.path.splitext(os.path.basename(name))[0]
    decision, _reason = classify_flat({"title": stem})
    return decision == "junk"

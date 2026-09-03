"""Pulling a video id out of the many shapes a YouTube link takes."""

import re
from urllib.parse import parse_qs, urlsplit

# 11-character ids, the only length YouTube issues.
_ID_RE = re.compile(r'^[A-Za-z0-9_-]{11}$')

# Paths that carry the id directly: /embed/<id>, /v/<id>, /shorts/<id>, /live/<id>
_PATH_RE = re.compile(r'^/(?:embed|v|shorts|live)/([A-Za-z0-9_-]{11})')


def extract_youtube_id(value):
    """
    Return the video id from a URL, or None.

    Accepts watch links, youtu.be short links, embeds, shorts, live URLs and a
    bare id, so an editor can paste whatever the browser gave them.
    """
    value = (value or '').strip()
    if not value:
        return None
    if _ID_RE.match(value):
        return value

    if '//' not in value:
        value = 'https://' + value
    parts = urlsplit(value)
    host = parts.netloc.lower().removeprefix('www.').removeprefix('m.')

    if host in ('youtu.be',):
        candidate = parts.path.lstrip('/').split('/')[0]
        return candidate if _ID_RE.match(candidate) else None

    if host not in ('youtube.com', 'youtube-nocookie.com', 'music.youtube.com'):
        return None

    match = _PATH_RE.match(parts.path)
    if match:
        return match.group(1)

    candidates = parse_qs(parts.query).get('v', [])
    if candidates and _ID_RE.match(candidates[0]):
        return candidates[0]
    return None

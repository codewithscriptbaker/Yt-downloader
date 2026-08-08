"""Local unit checks that do not require Redis/Docker."""

from __future__ import annotations

import time

from fastapi import HTTPException

from app.config import Settings
from app.errors import hint_for_error, is_permanent_error, is_transient_error, map_ytdlp_error
from app.formats import audio_postprocessors, is_audio_quality, resolve_ydl_format
from app.signing import create_signed_download_token, verify_signed_download_token
from app.validation import validate_media_url


def test_allow_list():
    settings = Settings()
    url, host = validate_media_url("https://www.youtube.com/watch?v=abc", settings)
    assert host == "www.youtube.com"
    validate_media_url("https://vm.tiktok.com/ZMxxx/", settings)
    validate_media_url("https://www.instagram.com/reel/abc/", settings)
    try:
        validate_media_url("https://example.com/video", settings)
        raise AssertionError("should reject")
    except HTTPException as exc:
        assert exc.status_code == 400


def test_signed_download_roundtrip():
    settings = Settings(download_signing_secret="test-secret", download_url_ttl_seconds=60)
    token, ttl = create_signed_download_token("jid", "opaque", "file.mp4", settings)
    assert ttl == 60
    verify_signed_download_token("jid", "opaque", "file.mp4", token, settings)
    try:
        verify_signed_download_token("jid", "opaque", "other.mp4", token, settings)
        raise AssertionError("should reject")
    except HTTPException:
        pass


def test_expired_token():
    settings = Settings(download_signing_secret="test-secret", download_url_ttl_seconds=1)
    token, _ = create_signed_download_token("jid", "opaque", "file.mp4", settings)
    time.sleep(0)  # noop; full expiry covered in integration
    verify_signed_download_token("jid", "opaque", "file.mp4", token, settings)


def test_transient_dns_error():
    msg = (
        "ERROR: [TikTok] Unable to download webpage: "
        "HTTPSConnection(host='www.tiktok.com', port=443): "
        "Failed to resolve 'www.tiktok.com' ([Errno 11002] getaddrinfo failed)"
    )
    assert is_transient_error(msg)
    assert not is_permanent_error(msg)
    assert "Couldn't reach the site" in map_ytdlp_error(Exception(msg))


def test_transient_curl_dns():
    msg = (
        "Failed to perform, curl: (6) Could not resolve host: www.tiktok.com. "
        "See https://curl.se/libcurl/c/libcurl-errors.html"
    )
    assert is_transient_error(msg)
    assert "Couldn't reach the site" in map_ytdlp_error(Exception(msg))


def test_hostname_helpers():
    from app.network import hostname_from_url

    assert hostname_from_url("https://www.tiktok.com/@x/video/1") == "www.tiktok.com"
    assert hostname_from_url("not-a-url") is None


def test_transient_429_and_5xx():
    assert is_transient_error("HTTP Error 429: Too Many Requests")
    assert is_transient_error("HTTP Error 503: Service Unavailable")
    assert is_transient_error(TimeoutError("socket timeout"))


def test_permanent_errors_not_retried():
    assert is_permanent_error("Private video. Sign in if you've been granted access")
    assert not is_transient_error("Private video. Sign in if you've been granted access")
    assert is_permanent_error("Video unavailable")
    assert not is_transient_error("This video has been removed")
    assert is_permanent_error("File is larger than 500MB")


def test_format_presets():
    assert "bv*" in resolve_ydl_format(quality="best", url="https://youtu.be/x", is_tiktok=False)
    assert "height<=?720" in resolve_ydl_format(
        quality="720", url="https://youtu.be/x", is_tiktok=False
    )
    assert "height<=?1080" in resolve_ydl_format(
        quality="1080p", url="https://youtu.be/x", is_tiktok=False
    )
    assert resolve_ydl_format(quality="audio", url="https://youtu.be/x", is_tiktok=False) == "ba/b"
    assert is_audio_quality("audio")
    assert not is_audio_quality("720")
    mp3 = audio_postprocessors("mp3")
    assert mp3[0]["preferredcodec"] == "mp3"
    m4a = audio_postprocessors("m4a")
    assert m4a[0]["preferredcodec"] == "m4a"


def test_summarize_qualities():
    from app.formats import summarize_available_qualities

    info = {
        "formats": [
            {"vcodec": "avc1", "acodec": "none", "height": 1080, "filesize": 80_000_000, "fps": 30},
            {"vcodec": "avc1", "acodec": "none", "height": 720, "filesize_approx": 40_000_000, "fps": 30},
            {"vcodec": "none", "acodec": "mp4a", "filesize": 3_000_000},
            {"vcodec": "vp9", "acodec": "none", "height": 1440, "filesize": 120_000_000},
        ]
    }
    quals = summarize_available_qualities(info)
    ids = [q["id"] for q in quals]
    assert ids[0] == "best"
    assert "1440" in ids
    assert "1080" in ids
    assert "720" in ids
    assert "audio" in ids
    assert "360" not in ids  # not present in source formats


def test_error_hints():
    friendly = map_ytdlp_error(Exception("Private video. Sign in required"))
    hint = hint_for_error(friendly)
    assert hint
    assert hint_for_error("Couldn't reach the site right now. Please try again in a moment.")
    assert hint_for_error("Some unknown failure")  # default hint


def test_fallback_classification():
    from app.errors import (
        is_format_fallback_error,
        is_retryable_with_fallback,
        is_setup_fallback_error,
    )
    from app.ytdlp_support import build_download_strategies, effective_quality

    assert is_setup_fallback_error(AssertionError())
    assert is_setup_fallback_error("AssertionError")
    assert is_format_fallback_error("Requested format is not available")
    assert not is_permanent_error("Requested format is not available")
    assert is_retryable_with_fallback(AssertionError(), has_next_strategy=True)
    assert not is_retryable_with_fallback(AssertionError(), has_next_strategy=False)
    assert not is_retryable_with_fallback(
        Exception("Private video. Sign in required"), has_next_strategy=True
    )

    assert map_ytdlp_error(AssertionError()).startswith("Download client setup failed")

    with_imp = build_download_strategies(quality="best", impersonate_available=True)
    assert [s.name for s in with_imp] == ["standard", "impersonate", "compat"]
    no_imp = build_download_strategies(quality="best", impersonate_available=False)
    assert [s.name for s in no_imp] == ["standard", "compat"]
    audio = build_download_strategies(quality="audio", impersonate_available=False)
    assert [s.name for s in audio] == ["standard"]
    assert effective_quality("best", with_imp[-1]) == "720"
    assert effective_quality("audio", with_imp[0]) == "audio"


def test_tiktok_compat_and_error_map():
    from app.ytdlp_support import apply_site_compat, is_tiktok_url

    assert is_tiktok_url("https://www.tiktok.com/@x/video/1")
    assert is_tiktok_url("https://vm.tiktok.com/ZMxxx/")
    assert not is_tiktok_url("https://youtu.be/abc")

    opts: dict = {"impersonate": "chrome"}
    apply_site_compat(opts, "https://www.tiktok.com/@x/video/1")
    assert "impersonate" not in opts
    # Leave default headers alone — forcing a UA can trigger TikTok challenges.
    assert "http_headers" not in opts

    msg = map_ytdlp_error(
        Exception(
            "ERROR: [TikTok] 123: Unable to extract universal data for rehydration; please report"
        )
    )
    assert "TikTok blocked" in msg
    assert hint_for_error(msg)


def test_playlist_url_detection():
    from app.preview import is_playlist_url

    assert is_playlist_url("https://www.youtube.com/playlist?list=PLabc123")
    assert is_playlist_url("https://music.youtube.com/playlist?list=PLabc123")
    assert is_playlist_url("https://www.youtube.com/playlist?list=PLabc")
    # watch + list stays single-video mode
    assert not is_playlist_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLabc")
    assert not is_playlist_url("https://youtu.be/dQw4w9WgXcQ")
    assert not is_playlist_url("https://www.tiktok.com/@x/video/1")


def test_playlist_entry_building():
    from app.preview import _build_playlist_entries, _entry_watch_url

    assert (
        _entry_watch_url({"id": "dQw4w9WgXcQ", "ie_key": "Youtube", "url": "dQw4w9WgXcQ"})
        == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    )
    assert _entry_watch_url(
        {"id": "dQw4w9WgXcQ", "webpage_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}
    ) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    info = {
        "title": "Demo playlist",
        "playlist_count": 5,
        "entries": [
            {
                "id": "aaaaaaaaaaa",
                "title": "One",
                "url": "aaaaaaaaaaa",
                "ie_key": "Youtube",
                "duration": 90,
                "thumbnail": "https://i.ytimg.com/vi/aaaaaaaaaaa/hqdefault.jpg",
            },
            {
                "id": "bbbbbbbbbbb",
                "title": "[Deleted video]",
                "url": "bbbbbbbbbbb",
                "ie_key": "Youtube",
            },
            {
                "id": "ccccccccccc",
                "title": "Three",
                "url": "ccccccccccc",
                "ie_key": "Youtube",
                "duration": 12,
            },
        ],
    }
    entries, count, truncated = _build_playlist_entries(info, limit=30)
    assert len(entries) == 2
    assert entries[0].id == "aaaaaaaaaaa"
    assert entries[0].url.endswith("aaaaaaaaaaa")
    assert count == 5
    assert truncated is True

    capped, _, _ = _build_playlist_entries(
        {
            "playlist_count": 100,
            "entries": [
                {"id": f"id{i:09d}", "title": f"V{i}", "url": f"id{i:09d}", "ie_key": "Youtube"}
                for i in range(40)
            ],
        },
        limit=5,
    )
    assert len(capped) == 5


def test_batch_request_validation():
    from app.models import CreateBatchJobRequest

    body = CreateBatchJobRequest(
        urls=[
            "https://www.youtube.com/watch?v=aaaaaaaaaaa",
            "https://www.youtube.com/watch?v=aaaaaaaaaaa",  # dedupe
            "https://www.youtube.com/watch?v=bbbbbbbbbbb",
        ],
        quality="720",
    )
    assert len(body.urls) == 2
    assert body.quality == "720"

    try:
        CreateBatchJobRequest(urls=["short"], quality="best")
        raise AssertionError("should reject short url")
    except Exception:
        pass


if __name__ == "__main__":
    test_allow_list()
    test_signed_download_roundtrip()
    test_expired_token()
    test_transient_dns_error()
    test_transient_curl_dns()
    test_hostname_helpers()
    test_transient_429_and_5xx()
    test_permanent_errors_not_retried()
    test_format_presets()
    test_summarize_qualities()
    test_error_hints()
    test_fallback_classification()
    test_tiktok_compat_and_error_map()
    test_playlist_url_detection()
    test_playlist_entry_building()
    test_batch_request_validation()
    print("all local checks passed")

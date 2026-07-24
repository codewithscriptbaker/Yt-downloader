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
    assert resolve_ydl_format(quality="audio", url="https://youtu.be/x", is_tiktok=False) == "ba/b"
    assert is_audio_quality("audio")
    assert not is_audio_quality("720")
    mp3 = audio_postprocessors("mp3")
    assert mp3[0]["preferredcodec"] == "mp3"
    m4a = audio_postprocessors("m4a")
    assert m4a[0]["preferredcodec"] == "m4a"


def test_error_hints():
    friendly = map_ytdlp_error(Exception("Private video. Sign in required"))
    hint = hint_for_error(friendly)
    assert hint
    assert hint_for_error("Couldn't reach the site right now. Please try again in a moment.")
    assert hint_for_error("Some unknown failure")  # default hint


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
    test_error_hints()
    print("all local checks passed")

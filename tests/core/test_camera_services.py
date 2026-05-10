from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.api.endpoints import cameras as cameras_endpoint
from api.models.camera import Camera
from api.services.camera_crypto import decrypt_secret, encrypt_secret
from api.services.camera_service import camera_to_dict
from api.services.mediamtx_service import (
    MediaMTXClient,
    build_stream_urls,
    create_stream_token,
    mediamtx_api_url,
    verify_stream_token,
)
from api.services.onvif_ptz import PTZVelocity, velocity_for_action


def test_camera_secret_encryption_round_trips_without_plaintext():
    encrypted = encrypt_secret("rtsp://admin:secret@192.168.1.179:554/stream1")

    assert "secret" not in encrypted
    assert decrypt_secret(encrypted) == "rtsp://admin:secret@192.168.1.179:554/stream1"


def test_camera_dict_can_include_plain_configuration_for_lan_admin_ui():
    camera = Camera(
        id=1,
        name="front",
        enabled=True,
        mediamtx_path="front_door",
        rtsp_url_encrypted=encrypt_secret("rtsp://admin:secret@192.168.1.179/stream1"),
        onvif_enabled=True,
        onvif_host="192.168.1.179",
        onvif_port=80,
        onvif_username="admin",
        onvif_password_encrypted=encrypt_secret("secret"),
    )

    public_data = camera_to_dict(camera)
    sensitive_data = camera_to_dict(camera, include_sensitive=True)

    assert "rtsp_url" not in public_data
    assert sensitive_data["rtsp_url"] == "rtsp://admin:secret@192.168.1.179/stream1"
    assert sensitive_data["onvif_password"] == "secret"


def test_camera_stream_token_validates_path():
    token = create_stream_token(camera_id=7, path="front_door", user_id=42)["token"]

    payload = verify_stream_token(token, path="front_door")

    assert payload["camera_id"] == 7
    assert payload["user_id"] == 42
    with pytest.raises(HTTPException) as exc:
        verify_stream_token(token, path="garage")
    assert exc.value.status_code == 403


def test_mediamtx_service_reads_generated_env_file(monkeypatch, tmp_path):
    env_file = tmp_path / "ikaros-mediamtx.env"
    env_file.write_text(
        "\n".join(
            [
                "MEDIAMTX_API_URL=http://127.0.0.1:19997",
                "MEDIAMTX_HLS_PORT=18888",
                "MEDIAMTX_WEBRTC_PORT=18889",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MEDIAMTX_ENV_FILE", str(env_file))
    for name in (
        "MEDIAMTX_API_URL",
        "MEDIAMTX_HLS_BASE_URL",
        "MEDIAMTX_HLS_PORT",
        "MEDIAMTX_WEBRTC_BASE_URL",
        "MEDIAMTX_WEBRTC_PORT",
    ):
        monkeypatch.delenv(name, raising=False)

    request = SimpleNamespace(
        headers={},
        url=SimpleNamespace(scheme="http", hostname="ikaros.local"),
    )

    urls = build_stream_urls(request, "front_door", "stream-token")

    assert mediamtx_api_url() == "http://127.0.0.1:19997"
    assert MediaMTXClient()._base_url() == "http://127.0.0.1:19997"
    assert urls["webrtc_url"].startswith("http://ikaros.local:18889/front_door?")
    assert urls["hls_url"].startswith("http://ikaros.local:18888/front_door/")


def test_mediamtx_service_uses_same_origin_proxy_base(monkeypatch, tmp_path):
    env_file = tmp_path / "ikaros-mediamtx.env"
    env_file.write_text(
        "\n".join(
            [
                "MEDIAMTX_API_URL=http://127.0.0.1:19997",
                "MEDIAMTX_HLS_BASE_URL=/_mediamtx/hls",
                "MEDIAMTX_WEBRTC_BASE_URL=/_mediamtx/webrtc",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MEDIAMTX_ENV_FILE", str(env_file))
    for name in (
        "MEDIAMTX_HLS_BASE_URL",
        "MEDIAMTX_HLS_PORT",
        "MEDIAMTX_WEBRTC_BASE_URL",
        "MEDIAMTX_WEBRTC_PORT",
    ):
        monkeypatch.delenv(name, raising=False)

    request = SimpleNamespace(
        headers={"x-forwarded-proto": "https"},
        url=SimpleNamespace(scheme="http", hostname="internal.local"),
    )

    urls = build_stream_urls(request, "front_door", "stream-token")

    assert urls["webrtc_url"].startswith("/_mediamtx/webrtc/front_door?")
    assert urls["hls_url"].startswith("/_mediamtx/hls/front_door/")


@pytest.mark.asyncio
async def test_mediamtx_auth_accepts_query_token():
    token = create_stream_token(camera_id=7, path="front_door", user_id=42)["token"]

    result = await cameras_endpoint.mediamtx_auth(
        cameras_endpoint.MediaMTXAuthRequest(
            action="read",
            path="front_door",
            protocol="webrtc",
            query=f"token={token}",
        )
    )

    assert result == {"success": True}


@pytest.mark.asyncio
async def test_mediamtx_client_adds_missing_rtsp_path():
    calls = []

    class FakeClient(MediaMTXClient):
        async def _request(self, method, path, **kwargs):
            calls.append((method, path, kwargs.get("json")))
            if method == "GET":
                return SimpleNamespace(status_code=404)
            return SimpleNamespace(status_code=200)

    await FakeClient("http://mediamtx").ensure_rtsp_path(
        path="front_door",
        rtsp_url="rtsp://camera/stream1",
    )

    assert calls == [
        ("GET", "/v3/config/paths/get/front_door", None),
        (
            "POST",
            "/v3/config/paths/add/front_door",
            {
                "source": "rtsp://camera/stream1",
                "sourceOnDemand": True,
                "rtspTransport": "tcp",
            },
        ),
    ]


def test_ptz_velocity_mapping_clamps_speed():
    assert velocity_for_action("up_left", 2.0) == PTZVelocity(pan=-1.0, tilt=1.0)
    assert velocity_for_action("zoom_out", 0.01) == PTZVelocity(zoom=-0.05)
    with pytest.raises(ValueError):
        velocity_for_action("spin", 0.4)

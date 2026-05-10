from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(frozen=True)
class PTZVelocity:
    pan: float = 0.0
    tilt: float = 0.0
    zoom: float = 0.0


def _clamp_speed(speed: float | int | None) -> float:
    try:
        value = float(speed if speed is not None else 0.4)
    except Exception:
        value = 0.4
    return min(1.0, max(0.05, value))


def velocity_for_action(action: str, speed: float | int | None = None) -> PTZVelocity:
    value = _clamp_speed(speed)
    key = str(action or "").strip().lower().replace("-", "_")
    mapping = {
        "left": PTZVelocity(pan=-value),
        "right": PTZVelocity(pan=value),
        "up": PTZVelocity(tilt=value),
        "down": PTZVelocity(tilt=-value),
        "up_left": PTZVelocity(pan=-value, tilt=value),
        "up_right": PTZVelocity(pan=value, tilt=value),
        "down_left": PTZVelocity(pan=-value, tilt=-value),
        "down_right": PTZVelocity(pan=value, tilt=-value),
        "zoom_in": PTZVelocity(zoom=value),
        "zoom_out": PTZVelocity(zoom=-value),
    }
    if key not in mapping:
        raise ValueError(f"Unsupported PTZ action: {action}")
    return mapping[key]


def _profile_token(media_service) -> str:
    profiles = media_service.GetProfiles()
    if not profiles:
        raise RuntimeError("ONVIF camera did not return any media profiles")
    return str(getattr(profiles[0], "token", "") or profiles[0]["token"])


def _run_onvif_command(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    action: str,
    speed: float,
) -> None:
    try:
        from onvif import ONVIFCamera  # type: ignore[reportMissingImports]
    except Exception as exc:
        raise RuntimeError(
            "ONVIF dependency is not installed. Install the API dependencies again."
        ) from exc

    camera = ONVIFCamera(host, int(port), username, password)
    media_service = camera.create_media_service()
    ptz_service = camera.create_ptz_service()
    token = _profile_token(media_service)

    normalized = str(action or "").strip().lower().replace("-", "_")
    if normalized == "stop":
        request = ptz_service.create_type("Stop")
        request.ProfileToken = token
        request.PanTilt = True
        request.Zoom = True
        ptz_service.Stop(request)
        return

    velocity = velocity_for_action(normalized, speed)
    request = ptz_service.create_type("ContinuousMove")
    request.ProfileToken = token
    request.Velocity = {
        "PanTilt": {"x": velocity.pan, "y": velocity.tilt},
        "Zoom": {"x": velocity.zoom},
    }
    ptz_service.ContinuousMove(request)


async def send_ptz_command(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    action: str,
    speed: float = 0.4,
) -> None:
    if not str(host or "").strip():
        raise RuntimeError("ONVIF host is required")
    await asyncio.to_thread(
        _run_onvif_command,
        host=str(host).strip(),
        port=int(port or 80),
        username=str(username or "").strip(),
        password=str(password or ""),
        action=str(action or ""),
        speed=float(speed),
    )

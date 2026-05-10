from api import main as api_main


def test_cameras_path_uses_camera_pwa_manifest():
    override = api_main._pwa_override_for_path("modules/cameras")

    assert override is not None
    assert override["manifest"] == "/cameras-manifest.webmanifest"
    assert override["title"] == "实时监控"


def test_default_paths_keep_default_manifest():
    assert api_main._pwa_override_for_path("home") is None

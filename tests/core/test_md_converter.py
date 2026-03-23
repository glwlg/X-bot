from services.md_converter import adapt_md_file_for_platform


def test_adapt_md_file_for_platform_keeps_md_for_weixin():
    payload = b"# Demo\n\nhello"

    adapted_bytes, adapted_name = adapt_md_file_for_platform(
        file_bytes=payload,
        filename="README.md",
        platform="weixin",
    )

    assert adapted_bytes == payload
    assert adapted_name == "README.md"


def test_adapt_md_file_for_platform_converts_md_for_telegram():
    payload = b"# Demo\n\nhello"

    adapted_bytes, adapted_name = adapt_md_file_for_platform(
        file_bytes=payload,
        filename="README.md",
        platform="telegram",
    )

    assert adapted_name == "README.html"
    assert b"<!DOCTYPE html>" in adapted_bytes

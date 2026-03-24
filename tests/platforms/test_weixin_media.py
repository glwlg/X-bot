import base64

from extension.channels.weixin.media import (
    UploadedWeixinMedia,
    aes_ecb_padded_size,
    build_file_message_item,
    build_cdn_download_url,
    build_image_message_item,
    build_video_message_item,
    classify_media_kind,
    extension_from_content_type_or_url,
    guess_mime_type,
    parse_aes_key_base64,
)


def test_aes_ecb_padded_size_matches_pkcs7_block_behavior():
    assert aes_ecb_padded_size(0) == 16
    assert aes_ecb_padded_size(1) == 16
    assert aes_ecb_padded_size(16) == 32
    assert aes_ecb_padded_size(17) == 32


def test_mime_guessing_and_kind_classification_cover_common_media():
    assert guess_mime_type("demo.png") == "image/png"
    assert guess_mime_type("clip.mp4") == "video/mp4"
    assert classify_media_kind("image/png") == "image"
    assert classify_media_kind("video/mp4") == "video"
    assert classify_media_kind("application/pdf") == "file"


def test_extension_from_content_type_or_url_prefers_content_type_then_url():
    assert (
        extension_from_content_type_or_url("image/webp", "https://example.com/a.bin")
        == ".webp"
    )
    assert (
        extension_from_content_type_or_url("", "https://example.com/files/report.pdf")
        == ".pdf"
    )
    assert (
        extension_from_content_type_or_url(None, "https://example.com/no-ext") == ".bin"
    )


def test_media_message_item_builders_match_expected_payload_shape():
    uploaded = UploadedWeixinMedia(
        filekey="fk-1",
        download_encrypted_query_param="enc-1",
        aes_key_hex="00112233445566778899aabbccddeeff",
        plaintext_size=123,
        ciphertext_size=128,
    )
    expected_aes_b64 = base64.b64encode(uploaded.aes_key_hex.encode("ascii")).decode(
        "ascii"
    )

    image_item = build_image_message_item(uploaded)
    assert image_item["type"] == 2
    assert image_item["image_item"]["media"]["aes_key"] == expected_aes_b64
    assert image_item["image_item"]["mid_size"] == 128

    video_item = build_video_message_item(uploaded)
    assert video_item["type"] == 5
    assert video_item["video_item"]["media"]["encrypt_query_param"] == "enc-1"
    assert video_item["video_item"]["video_size"] == 128

    file_item = build_file_message_item(uploaded, "../report.pdf")
    assert file_item["type"] == 4
    assert file_item["file_item"]["file_name"] == "report.pdf"
    assert file_item["file_item"]["len"] == "123"


def test_parse_aes_key_base64_accepts_raw_16_byte_encoding():
    raw_key = bytes.fromhex("00112233445566778899aabbccddeeff")
    encoded = base64.b64encode(raw_key).decode("ascii")
    assert parse_aes_key_base64(encoded) == raw_key


def test_parse_aes_key_base64_accepts_base64_of_hex_text():
    hex_text = "00112233445566778899aabbccddeeff"
    encoded = base64.b64encode(hex_text.encode("ascii")).decode("ascii")
    assert parse_aes_key_base64(encoded) == bytes.fromhex(hex_text)


def test_build_cdn_download_url_quotes_query_param():
    assert (
        build_cdn_download_url("https://novac2c.cdn.weixin.qq.com/c2c/", "enc=1+2")
        == "https://novac2c.cdn.weixin.qq.com/c2c/download?encrypted_query_param=enc%3D1%2B2"
    )

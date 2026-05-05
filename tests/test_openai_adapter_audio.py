from services.openai_adapter import build_messages


def _extract_file_block(messages):
    content = messages[0]["content"]
    assert isinstance(content, list)
    return next(block for block in content if block.get("type") == "file")


def _extract_block(messages, block_type):
    content = messages[0]["content"]
    assert isinstance(content, list)
    return next(block for block in content if block.get("type") == block_type)


def test_build_messages_maps_image_inline_data_to_image_url_data_url() -> None:
    messages = build_messages(
        contents=[
            {
                "role": "user",
                "parts": [
                    {"text": "看图回答"},
                    {
                        "inline_data": {
                            "mime_type": "image/png",
                            "data": "ZmFrZS1pbWFnZS1kYXRh",
                        }
                    },
                ],
            }
        ]
    )

    image_block = _extract_block(messages, "image_url")
    assert image_block["image_url"]["url"] == (
        "data:image/png;base64,ZmFrZS1pbWFnZS1kYXRh"
    )


def test_build_messages_maps_ogg_to_file() -> None:
    messages = build_messages(
        contents=[
            {
                "role": "user",
                "parts": [
                    {"text": "transcribe"},
                    {
                        "inline_data": {
                            "mime_type": "audio/ogg",
                            "data": "ZmFrZS1hdWRpby1kYXRh",
                        }
                    },
                ],
            }
        ]
    )

    file_block = _extract_file_block(messages)
    assert file_block["file"]["filename"] == "audio.ogg"
    assert file_block["file"]["file_data"] == "ZmFrZS1hdWRpby1kYXRh"


def test_build_messages_maps_ogg_with_codec_suffix() -> None:
    messages = build_messages(
        contents=[
            {
                "role": "user",
                "parts": [
                    {
                        "inline_data": {
                            "mime_type": "audio/ogg; codecs=opus",
                            "data": "ZmFrZS1hdWRpby1kYXRh",
                        }
                    }
                ],
            }
        ]
    )

    file_block = _extract_file_block(messages)
    assert file_block["file"]["filename"] == "audio.ogg"


def test_build_messages_maps_webm_to_file() -> None:
    messages = build_messages(
        contents=[
            {
                "role": "user",
                "parts": [
                    {
                        "inline_data": {
                            "mime_type": "audio/webm",
                            "data": "ZmFrZS1hdWRpby1kYXRh",
                        }
                    }
                ],
            }
        ]
    )

    file_block = _extract_file_block(messages)
    assert file_block["file"]["filename"] == "audio.webm"


def test_build_messages_supports_input_audio_override() -> None:
    messages = build_messages(
        contents=[
            {
                "role": "user",
                "parts": [
                    {
                        "inline_data": {
                            "mime_type": "audio/wav",
                            "data": "ZmFrZS1hdWRpby1kYXRh",
                        }
                    }
                ],
            }
        ],
        config={"audio_part_style": "input_audio"},
    )

    audio_block = _extract_block(messages, "input_audio")
    assert audio_block["input_audio"]["format"] == "wav"
    assert audio_block["input_audio"]["data"] == "ZmFrZS1hdWRpby1kYXRh"


def test_build_messages_supports_input_audio_data_uri_override() -> None:
    messages = build_messages(
        contents=[
            {
                "role": "user",
                "parts": [
                    {
                        "inline_data": {
                            "mime_type": "audio/mpeg",
                            "data": "ZmFrZS1hdWRpby1kYXRh",
                        }
                    }
                ],
            }
        ],
        config={"audio_part_style": "input_audio_data_uri"},
    )

    audio_block = _extract_block(messages, "input_audio")
    assert (
        audio_block["input_audio"]["data"]
        == "data:audio/mpeg;base64,ZmFrZS1hdWRpby1kYXRh"
    )


def test_build_messages_maps_video_to_video_url() -> None:
    messages = build_messages(
        contents=[
            {
                "role": "user",
                "parts": [
                    {"text": "transcribe"},
                    {
                        "inline_data": {
                            "mime_type": "video/mp4",
                            "data": "ZmFrZS12aWRlby1kYXRh",
                        }
                    },
                ],
            }
        ],
        config={"video_part_style": "video_url"},
    )

    video_block = _extract_block(messages, "video_url")
    assert video_block["video_url"]["url"] == "data:video/mp4;base64,ZmFrZS12aWRlby1kYXRh"

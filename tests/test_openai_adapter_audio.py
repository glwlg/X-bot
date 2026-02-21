from services.openai_adapter import build_messages


def _extract_file_block(messages):
    content = messages[0]["content"]
    assert isinstance(content, list)
    return next(block for block in content if block.get("type") == "file")


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

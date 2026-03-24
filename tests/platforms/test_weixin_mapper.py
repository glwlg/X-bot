from core.platform.models import MessageType
from extension.channels.weixin.formatter import markdown_to_weixin_text
from extension.channels.weixin.mapper import map_weixin_message


def test_markdown_to_weixin_text_downgrades_links_and_emphasis():
    rendered = markdown_to_weixin_text(
        "## 标题\n**加粗** [OpenAI](https://openai.com)\n`code`"
    )
    assert "标题" in rendered
    assert "加粗" in rendered
    assert "OpenAI: https://openai.com" in rendered
    assert "`" not in rendered


def test_map_weixin_message_extracts_plain_text_items():
    message = map_weixin_message(
        {
            "from_user_id": "wx-user-1",
            "from_user_name": "Alice",
            "client_id": "msg-1",
            "create_time_ms": 1710000000000,
            "item_list": [
                {"type": 1, "text_item": {"text": "hello"}},
                {"type": 1, "text_item": {"text": "world"}},
            ],
            "context_token": "ctx-1",
        }
    )

    assert message.platform == "weixin"
    assert message.user.id == "wx-user-1"
    assert message.chat.id == "wx-user-1"
    assert message.type == MessageType.TEXT
    assert message.text == "hello\nworld"


def test_map_weixin_message_promotes_image_to_media_message():
    message = map_weixin_message(
        {
            "from_user_id": "wx-user-1",
            "from_user_name": "Alice",
            "client_id": "msg-2",
            "create_time_ms": 1710000000000,
            "item_list": [
                {"type": 1, "text_item": {"text": "帮我看看这个商品"}},
                {
                    "type": 2,
                    "image_item": {
                        "media": {
                            "encrypt_query_param": "enc-image-1",
                            "aes_key": "MDAxMTIyMzM0NDU1NjY3Nzg4OTlhYWJiY2NkZGVlZmY=",
                        },
                        "mid_size": 24576,
                    },
                },
            ],
            "context_token": "ctx-2",
        }
    )

    assert message.type == MessageType.IMAGE
    assert message.text is None
    assert message.caption == "帮我看看这个商品"
    assert message.file_id == "enc-image-1"
    assert message.file_size == 24576
    assert message.mime_type == "image/jpeg"


def test_map_weixin_message_maps_file_metadata():
    message = map_weixin_message(
        {
            "from_user_id": "wx-user-2",
            "client_id": "msg-3",
            "item_list": [
                {
                    "type": 4,
                    "file_item": {
                        "media": {"encrypt_query_param": "enc-file-1"},
                        "file_name": "report.pdf",
                        "len": "12345",
                    },
                }
            ],
        }
    )

    assert message.type == MessageType.DOCUMENT
    assert message.file_id == "enc-file-1"
    assert message.file_name == "report.pdf"
    assert message.file_size == 12345
    assert message.mime_type == "application/octet-stream"


def test_map_weixin_message_maps_voice_metadata_from_encode_type():
    message = map_weixin_message(
        {
            "from_user_id": "wx-user-3",
            "client_id": "msg-4",
            "item_list": [
                {
                    "type": 3,
                    "voice_item": {
                        "media": {"encrypt_query_param": "enc-voice-1"},
                        "encode_type": 8,
                        "playtime": 2600,
                        "text": "帮我总结一下",
                    },
                }
            ],
        }
    )

    assert message.type == MessageType.VOICE
    assert message.file_id == "enc-voice-1"
    assert message.duration == 2600
    assert message.mime_type == "audio/ogg"


def test_map_weixin_message_extracts_link_card_as_text():
    message = map_weixin_message(
        {
            "from_user_id": "wx-user-4",
            "from_user_name": "Alice",
            "client_id": "msg-5",
            "create_time_ms": 1710000000000,
            "item_list": [
                {
                    "type": 6,
                    "link_item": {
                        "title": "GitHub",
                        "description": "A collective list of free APIs",
                        "url": "https://github.com/public-apis/public-apis",
                    },
                }
            ],
        }
    )

    assert message.type == MessageType.TEXT
    assert "GitHub" in str(message.text)
    assert "free APIs" in str(message.text)
    assert "https://github.com/public-apis/public-apis" in str(message.text)

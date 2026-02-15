from datetime import datetime
from types import SimpleNamespace

import pytest

from core.platform.exceptions import MediaDownloadUnavailableError
from core.platform.models import MessageType
from platforms.dingtalk.adapter import DingTalkAdapter
from platforms.dingtalk.mapper import map_chatbot_message, map_dingtalk_message
from platforms.discord.adapter import DiscordAdapter
from platforms.telegram.mapper import map_telegram_message


class _FakeTelegramObject:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def to_dict(self):
        data = {}
        for key, value in self.__dict__.items():
            if isinstance(value, list):
                data[key] = [
                    item.to_dict() if hasattr(item, "to_dict") else item for item in value
                ]
            elif hasattr(value, "to_dict"):
                data[key] = value.to_dict()
            else:
                data[key] = value
        return data


def _build_telegram_message(media_type: MessageType):
    user = _FakeTelegramObject(
        id=1001,
        username="u",
        first_name="F",
        last_name="L",
        language_code="zh",
        is_bot=False,
    )
    chat = _FakeTelegramObject(id=2001, type="private", title=None, username=None)

    base = {
        "message_id": 1,
        "from_user": user,
        "chat": chat,
        "date": datetime.now(),
        "text": None,
        "caption": None,
        "photo": None,
        "video": None,
        "voice": None,
        "audio": None,
        "document": None,
        "sticker": None,
        "location": None,
        "contact": None,
        "reply_to_message": None,
    }

    if media_type == MessageType.TEXT:
        base["text"] = "hello"
    elif media_type == MessageType.IMAGE:
        base["photo"] = [
            _FakeTelegramObject(file_id="p_small", file_size=1, width=10, height=10),
            _FakeTelegramObject(file_id="p_big", file_size=2, width=20, height=20),
        ]
        base["caption"] = "img"
    elif media_type == MessageType.VIDEO:
        base["video"] = _FakeTelegramObject(
            file_id="v1",
            file_size=10,
            mime_type="video/mp4",
            file_name="demo.mp4",
            width=1920,
            height=1080,
            duration=12,
        )
    elif media_type == MessageType.VOICE:
        base["voice"] = _FakeTelegramObject(
            file_id="voice1",
            file_size=3,
            mime_type="audio/ogg",
            duration=9,
        )
    elif media_type == MessageType.AUDIO:
        base["audio"] = _FakeTelegramObject(
            file_id="audio1",
            file_size=4,
            mime_type="audio/mpeg",
            file_name="a.mp3",
            duration=30,
        )
    elif media_type == MessageType.DOCUMENT:
        base["document"] = _FakeTelegramObject(
            file_id="doc1",
            file_size=5,
            mime_type="application/pdf",
            file_name="a.pdf",
        )

    return _FakeTelegramObject(**base)


@pytest.mark.parametrize(
    "media_type, expected_file_id",
    [
        (MessageType.TEXT, None),
        (MessageType.IMAGE, "p_big"),
        (MessageType.VIDEO, "v1"),
        (MessageType.VOICE, "voice1"),
        (MessageType.AUDIO, "audio1"),
        (MessageType.DOCUMENT, "doc1"),
    ],
)
def test_telegram_mapper_supports_all_main_message_types(media_type, expected_file_id):
    mapped = map_telegram_message(_build_telegram_message(media_type))
    assert mapped.type == media_type
    assert mapped.file_id == expected_file_id


def test_dingtalk_payload_mapper_maps_media_fields():
    mapped = map_dingtalk_message(
        msg_data={
            "msgtype": "file",
            "msgId": "m001",
            "content": {
                "downloadCode": "download_code_1",
                "fileName": "report.pdf",
                "mimeType": "application/pdf",
            },
        },
        conversation_id="c1",
        sender_id="u1",
        sender_nick="tester",
        conversation_type="1",
    )

    assert mapped.type == MessageType.DOCUMENT
    assert mapped.file_id == "download_code_1"
    assert mapped.file_name == "report.pdf"
    assert mapped.mime_type == "application/pdf"


def test_dingtalk_chatbot_mapper_maps_media_fields():
    incoming = SimpleNamespace(
        sender_staff_id="u1",
        sender_nick="tester",
        conversation_id="c1",
        conversation_type="2",
        msg_id="m002",
        message_type="video",
        content={"downloadCode": "dc2", "mimeType": "video/mp4"},
        text=None,
    )

    mapped = map_chatbot_message(incoming)

    assert mapped.type == MessageType.VIDEO
    assert mapped.file_id == "dc2"
    assert mapped.mime_type == "video/mp4"
    assert mapped.chat.type == "group"


@pytest.mark.asyncio
async def test_dingtalk_adapter_download_file_returns_diagnostic_error(monkeypatch):
    adapter = DingTalkAdapter("app_key", "app_secret")
    context = SimpleNamespace(
        message=SimpleNamespace(file_url=None),
    )

    async def _no_token():
        return None

    monkeypatch.setattr(adapter, "_fetch_openapi_access_token", _no_token)

    with pytest.raises(MediaDownloadUnavailableError) as exc:
        await adapter.download_file(context, "download_code_1")

    assert exc.value.error_code == "media_download_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content_type, expected_type",
    [
        ("image/png", MessageType.IMAGE),
        ("video/mp4", MessageType.VIDEO),
        ("audio/mpeg", MessageType.AUDIO),
        ("application/pdf", MessageType.DOCUMENT),
    ],
)
async def test_discord_mapper_supports_attachment_media_types(content_type, expected_type):
    adapter = DiscordAdapter("token")
    attachment = SimpleNamespace(
        id=99,
        size=321,
        filename="file.bin",
        content_type=content_type,
        url="https://example.com/file.bin",
        width=100,
        height=50,
        duration=None,
    )
    message = SimpleNamespace(
        id=123,
        attachments=[attachment],
        content="",
        created_at=datetime.now(),
        channel=SimpleNamespace(id=11, name="chan"),
        author=SimpleNamespace(id=22, name="name", display_name="display", bot=False),
        reference=None,
    )

    mapped = await adapter._map_message(message)

    assert mapped.type == expected_type
    assert mapped.file_id == "99"
    assert mapped.file_size == 321

    await adapter.stop()

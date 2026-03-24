from types import SimpleNamespace

import pytest

import core.agent_input as agent_input_module
from handlers import message_utils
from core.platform.models import MessageType


class _DummyContext:
    def __init__(self, *, platform: str = "telegram"):
        self.message = SimpleNamespace(
            id="msg-1",
            platform=platform,
            reply_to_message=None,
        )
        self.documents: list[dict[str, object]] = []
        self.replies: list[object] = []
        self.actions: list[tuple[str, dict]] = []

    async def reply(self, payload, **kwargs):
        _ = kwargs
        self.replies.append(payload)
        return SimpleNamespace(delete=self._delete_status)

    async def _delete_status(self):
        return True

    async def send_chat_action(self, action, **kwargs):
        self.actions.append((action, dict(kwargs)))
        return True

    async def download_file(self, file_id, **kwargs):
        _ = (file_id, kwargs)
        return b"downloaded"

    async def reply_document(self, document, filename=None, caption=None, **kwargs):
        self.documents.append(
            {
                "document": document,
                "filename": filename,
                "caption": caption,
                "kwargs": dict(kwargs),
            }
        )
        return True


@pytest.mark.asyncio
async def test_process_and_send_code_files_keeps_20_line_block_inline(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    ctx = _DummyContext()
    code = "\n".join(f"<div>{idx}</div>" for idx in range(20))
    text = f"前文\n```html\n{code}\n```\n后文"

    rendered = await message_utils.process_and_send_code_files(ctx, text)

    assert rendered == text
    assert ctx.documents == []


@pytest.mark.asyncio
async def test_process_and_send_code_files_converts_21_line_block_to_file(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    ctx = _DummyContext()
    code = "\n".join(f"<div>{idx}</div>" for idx in range(21))
    text = f"前文\n```html\n{code}\n```\n后文"

    rendered = await message_utils.process_and_send_code_files(ctx, text)

    assert "code_snippet_1.html" in rendered
    assert "内容已保存为文件" in rendered
    assert len(ctx.documents) == 1
    assert ctx.documents[0]["filename"] == "code_snippet_1.html"
    assert ctx.documents[0]["caption"] == "📝 HTML 代码片段"


@pytest.mark.asyncio
async def test_process_and_send_code_files_keeps_html_snippet_as_md_for_weixin(
    monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    ctx = _DummyContext(platform="weixin")
    code = "\n".join(f"<div>{idx}</div>" for idx in range(21))
    text = f"前文\n```html\n{code}\n```\n后文"

    rendered = await message_utils.process_and_send_code_files(ctx, text)

    assert "code_snippet_1.md" in rendered
    assert "内容已保存为文件" in rendered
    assert len(ctx.documents) == 1
    assert ctx.documents[0]["filename"] == "code_snippet_1.md"
    assert ctx.documents[0]["caption"] == "📝 Markdown 文本片段（原始语言: html）"


@pytest.mark.asyncio
async def test_resolve_inline_inputs_from_text_reads_local_image_path(tmp_path):
    image_path = (tmp_path / "camera.png").resolve()
    image_path.write_bytes(b"\x89PNG\r\n\x1a\npayload")

    resolution = await message_utils.resolve_inline_inputs_from_text(
        f"看看这张图 {image_path}"
    )

    assert resolution.detected_refs == [str(image_path)]
    assert len(resolution.inputs) == 1
    assert resolution.inputs[0].source_kind == "local_path"
    assert resolution.inputs[0].mime_type == "image/png"


@pytest.mark.asyncio
async def test_resolve_inline_inputs_from_text_fetches_url_images(monkeypatch):
    async def _fake_fetch(url: str, *, max_bytes=0):
        _ = max_bytes
        return b"jpeg-bytes", "image/jpeg"

    monkeypatch.setattr(agent_input_module, "fetch_image_from_url", _fake_fetch)
    url = "https://example.com/cam.jpg"

    resolution = await message_utils.resolve_inline_inputs_from_text(f"看这个 {url}")

    assert resolution.detected_refs == [url]
    assert resolution.errors == []
    assert len(resolution.inputs) == 1
    assert resolution.inputs[0].source_kind == "url"
    assert resolution.inputs[0].mime_type == "image/jpeg"


@pytest.mark.asyncio
async def test_resolve_inline_inputs_from_text_ignores_non_image_web_url():
    url = "https://github.com/public-apis/public-apis"

    resolution = await message_utils.resolve_inline_inputs_from_text(f"看这个 {url}")

    assert resolution.detected_refs == []
    assert resolution.errors == []
    assert resolution.inputs == []


@pytest.mark.asyncio
async def test_process_reply_message_prefers_inline_image_inputs_over_web_content(
    monkeypatch,
):
    url = "https://example.com/cam.jpg"

    async def _fake_resolve_inline_inputs_from_text(_text: str, *, limit=5):
        _ = limit
        return message_utils.InlineInputResolution(
            inputs=[
                message_utils.ResolvedInlineInput(
                    mime_type="image/jpeg",
                    content=b"reply-image",
                    source_kind="url",
                    source_ref=url,
                )
            ],
            detected_refs=[url],
            errors=[],
        )

    async def _forbidden_fetch_webpage_content(_url: str):
        raise AssertionError("reply image URL should not fall back to webpage fetch")

    monkeypatch.setattr(
        agent_input_module,
        "resolve_inline_inputs_from_text",
        _fake_resolve_inline_inputs_from_text,
    )
    monkeypatch.setattr(
        agent_input_module,
        "fetch_webpage_content",
        _forbidden_fetch_webpage_content,
    )

    ctx = _DummyContext()
    ctx.message.reply_to_message = SimpleNamespace(
        id="reply-1",
        text=url,
        caption="",
        type=MessageType.TEXT,
        entities=[],
        caption_entities=[],
    )

    result = await message_utils.process_reply_message(ctx)

    assert len(result.inputs) == 1
    assert result.extra_context == ""
    assert result.inputs[0].source_ref == url


@pytest.mark.asyncio
async def test_process_reply_message_fetches_web_content_for_non_image_url(monkeypatch):
    url = "https://github.com/public-apis/public-apis"

    async def _fake_fetch_webpage_content(_url: str):
        assert _url == url
        return "网页正文"

    monkeypatch.setattr(
        agent_input_module,
        "fetch_webpage_content",
        _fake_fetch_webpage_content,
    )

    ctx = _DummyContext()
    ctx.message.reply_to_message = SimpleNamespace(
        id="reply-2",
        text=url,
        caption="",
        type=MessageType.TEXT,
        entities=[],
        caption_entities=[],
    )

    result = await message_utils.process_reply_message(ctx)

    assert result.inputs == []
    assert result.detected_refs == []
    assert result.errors == []
    assert "网页正文" in result.extra_context

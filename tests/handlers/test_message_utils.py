from types import SimpleNamespace

import pytest

from handlers import message_utils


class _DummyContext:
    def __init__(self):
        self.message = SimpleNamespace(id="msg-1", platform="telegram")
        self.documents: list[dict[str, object]] = []

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


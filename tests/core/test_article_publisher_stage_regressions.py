import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_execute_module():
    path = (
        Path(__file__).resolve().parents[2]
        / "extension"
        / "skills"
        / "learned"
        / "article_publisher"
        / "scripts"
        / "execute.py"
    )
    spec = importlib.util.spec_from_file_location(
        "article_publisher_execute_regression_test",
        path,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_write_module():
    path = (
        Path(__file__).resolve().parents[2]
        / "extension"
        / "skills"
        / "learned"
        / "article_publisher"
        / "scripts"
        / "stages"
        / "write.py"
    )
    spec = importlib.util.spec_from_file_location(
        "article_publisher_write_regression_test",
        path,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_write_stage_uses_material_context_from_research_json(
    monkeypatch,
    tmp_path: Path,
):
    module = _load_write_module()
    research_path = tmp_path / "research.json"
    research_path.write_text(
        json.dumps(
            {
                "topic": "素材整理",
                "material_context": "这是从本地素材整理出来的写作上下文。",
                "sources": [{"type": "local_material", "paths": ["/tmp/demo.md"]}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    captured: dict[str, str] = {}

    async def fake_generate_article_json(topic: str, context: str, ctx=None):
        _ = ctx
        captured["topic"] = topic
        captured["context"] = context
        return {
            "title": "测试标题",
            "author": "Ikaros",
            "digest": "测试摘要",
            "cover_prompt": None,
            "sections": [
                {
                    "content": "<p>" + ("正文" * 120) + "</p>",
                    "image_prompt": None,
                }
            ],
        }

    monkeypatch.setattr(module, "_generate_article_json", fake_generate_article_json)

    result = await module.write_stage(str(research_path), output_dir=str(tmp_path / "out"))

    assert result.ok is True
    assert captured["topic"] == "素材整理"
    assert "本地素材整理出来的写作上下文" in captured["context"]


@pytest.mark.asyncio
async def test_write_stage_rehydrates_local_material_sources_without_embedded_content(
    monkeypatch,
    tmp_path: Path,
):
    module = _load_write_module()
    material_path = tmp_path / "material.md"
    material_path.write_text(
        "# 视频转写\n\n这里是本地素材正文，用于回填 write stage 上下文。",
        encoding="utf-8",
    )
    research_path = tmp_path / "research.json"
    research_path.write_text(
        json.dumps(
            {
                "topic": "回填测试",
                "sources": [{"type": "local_material", "paths": [str(material_path)]}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    captured: dict[str, str] = {}

    async def fake_generate_article_json(topic: str, context: str, ctx=None):
        _ = ctx
        captured["topic"] = topic
        captured["context"] = context
        return {
            "title": "测试标题",
            "author": "Ikaros",
            "digest": "测试摘要",
            "cover_prompt": None,
            "sections": [
                {
                    "content": "<p>" + ("正文" * 120) + "</p>",
                    "image_prompt": None,
                }
            ],
        }

    monkeypatch.setattr(module, "_generate_article_json", fake_generate_article_json)

    result = await module.write_stage(str(research_path), output_dir=str(tmp_path / "out"))

    assert result.ok is True
    assert captured["topic"] == "回填测试"
    assert str(material_path) in captured["context"]
    assert "这里是本地素材正文" in captured["context"]


@pytest.mark.asyncio
async def test_run_single_stage_write_accepts_markdown_source(monkeypatch, tmp_path: Path):
    module = _load_execute_module()
    material_path = tmp_path / "prompt-flow.md"
    material_path.write_text("# 标题\n\n内容", encoding="utf-8")

    captured: dict[str, object] = {}

    async def fake_write_stage(source: str, *, output_dir=None, topic=None, ctx=None):
        captured["source"] = source
        captured["output_dir"] = output_dir
        captured["topic"] = topic
        captured["ctx"] = ctx
        return module.StageResult.success({"title": "x"}, str(tmp_path / "article.json"))

    monkeypatch.setattr(module, "write_stage", fake_write_stage)

    ctx = SimpleNamespace(message=SimpleNamespace(text="", user=SimpleNamespace(id="u-1")))
    result = await module.run_single_stage(
        "write",
        str(material_path),
        ctx=ctx,
        output_dir=str(tmp_path / "out"),
    )

    assert result.ok is True
    assert captured["source"] == str(material_path)
    assert captured["output_dir"] == str(tmp_path / "out")
    assert captured["topic"] == "prompt-flow"
    assert captured["ctx"] is ctx

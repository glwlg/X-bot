import handlers.skill_handlers as skill_handlers_module


def test_visible_skill_items_use_enabled_skill_index(monkeypatch):
    monkeypatch.setattr(
        skill_handlers_module.skill_loader,
        "get_enabled_skill_index",
        lambda: {
            "rss_subscribe": {
                "name": "rss_subscribe",
                "description": "RSS",
                "source": "learned",
                "ikaros_only": False,
            },
            "internal_ops": {
                "name": "internal_ops",
                "description": "internal",
                "source": "builtin",
                "ikaros_only": True,
            },
        },
    )

    items = skill_handlers_module._visible_skill_items()

    assert [name for name, _info in items] == ["rss_subscribe"]

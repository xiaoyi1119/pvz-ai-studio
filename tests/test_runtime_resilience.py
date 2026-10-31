import threading
import time
from pathlib import Path
import base64

import httpx

from pvz_agent_plugin.capture import GameCaptureHub
from pvz_agent_plugin.plugin import PvZPlugin
from pvz_agent_plugin import service
from pvz_agent_plugin.service import ModelsRequest, ProfileRequest, SettingsRequest, _update_env_file


def test_agent_wait_can_be_interrupted(tmp_path):
    plugin = PvZPlugin(log_dir=tmp_path)
    stop = threading.Event()
    stop.set()
    started = time.perf_counter()
    result = plugin.execute("wait", _cancel_event=stop, time=5)
    assert time.perf_counter() - started < 0.2
    assert result["interrupted"] is True


def test_jpeg_dimension_parser_rejects_missing_sof():
    assert GameCaptureHub._jpeg_dimensions(b"\xff\xd8\xff\xd9") == (0, 0)


def test_settings_update_preserves_unrelated_env_lines(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text("# local settings\nKEEP_ME=yes\nDEEPSEEK_MODEL=old\n", encoding="utf-8")
    _update_env_file(
        env_path,
        {
            "DEEPSEEK_MODEL": "deepseek-flash",
            "AGENT_GOAL": "完成当前关卡",
        },
    )
    content = env_path.read_text(encoding="utf-8")
    assert "# local settings" in content
    assert "KEEP_ME=yes" in content
    assert "DEEPSEEK_MODEL=deepseek-flash" in content
    assert "AGENT_GOAL=完成当前关卡" in content


def test_model_discovery_returns_model_ids(monkeypatch):
    response = httpx.Response(200, json={"object": "list", "data": [
        {"id": "deepseek-v4-pro", "object": "model"},
        {"id": "deepseek-flash", "object": "model"},
    ]})
    monkeypatch.setattr(service.httpx, "get", lambda *args, **kwargs: response)
    result = service.api_settings_models(ModelsRequest(
        base_url="https://api.deepseek.com", api_key="local-test-key"
    ))
    assert result == {"models": ["deepseek-flash", "deepseek-v4-pro"]}


def test_model_test_saves_only_after_tool_call(monkeypatch):
    response = httpx.Response(200, json={"choices": [{"message": {"tool_calls": [
        {"function": {"name": "connection_test", "arguments": '{"ok":true}'}}
    ]}}]})
    monkeypatch.setattr(service.httpx, "post", lambda *args, **kwargs: response)
    saved = {}

    def fake_save(base_url, api_key, model, goal):
        saved.update(base_url=base_url, api_key=api_key, model=model, goal=goal)
        return {"base_url": base_url, "model": model, "goal": goal, "api_key_configured": True}

    monkeypatch.setattr(service, "_save_settings", fake_save)
    result = service.api_settings_test_and_save(SettingsRequest(
        base_url="https://api.deepseek.com", api_key="local-test-key",
        model="deepseek-flash", goal="完成当前关卡",
    ))
    assert result["test"]["ok"] is True
    assert saved["model"] == "deepseek-flash"


def test_profile_name_and_avatar_are_saved_locally(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "PROFILE_DIR", tmp_path / "profile")
    monkeypatch.setattr(service, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(service.settings, "channel_name", "旧名称")
    monkeypatch.setattr(service.settings, "channel_avatar_path", "")
    png = base64.b64encode(b"\x89PNG\r\n\x1a\nlocal-avatar").decode("ascii")
    result = service._save_profile(ProfileRequest(
        name="新频道", avatar_data_url=f"data:image/png;base64,{png}"
    ))
    assert result["name"] == "新频道"
    assert (tmp_path / "profile" / "avatar.png").read_bytes().startswith(b"\x89PNG")
    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "CHANNEL_NAME=新频道" in env


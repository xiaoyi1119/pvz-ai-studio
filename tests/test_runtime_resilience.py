import threading
import time
from pathlib import Path
import base64

import httpx
import pytest

from pvz_agent_plugin.capture import GameCaptureHub
from pvz_agent_plugin.plugin import PvZPlugin
from pvz_agent_plugin.pvz_native.reader import GameState, PlantInfo, SeedInfo
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


def test_read_state_preserves_user_pause(tmp_path):
    plugin = PvZPlugin(log_dir=tmp_path)

    class Memory:
        def is_connected(self):
            return True

    class Injector:
        def set_game_paused(self, paused):
            raise AssertionError("read_state must not mutate the user's pause state")

    class Reader:
        def read_state(self):
            from pvz_agent_plugin.pvz_native.reader import GameState
            return GameState(game_ui=3, is_paused=True)

    plugin._memory = Memory()
    plugin._injector = Injector()
    plugin._reader = Reader()
    assert plugin.read_state()["game"]["paused"] is True


def test_select_seeds_is_idempotent_during_level_intro(tmp_path):
    plugin = PvZPlugin(log_dir=tmp_path)

    class Memory:
        def is_connected(self):
            return True

        def get_select_card_ui_ptr(self):
            return 0x1234

    class Injector:
        def __init__(self):
            self.rocks = 0

        def choose_card(self, seed):
            return None

        def pick_random_seeds(self):
            return None

        def rock(self):
            self.rocks += 1

    class Reader:
        def read_state(self):
            return GameState(
                game_ui=2, game_mode=0, adventure_level=8,
                is_paused=False, seed_chooser_ready=True,
                seed_chooser_visible=True, seeds=[_seed(0, 0)],
            )

    injector = Injector()
    plugin._memory = Memory()
    plugin._injector = injector
    plugin._reader = Reader()
    first = plugin.select_seeds([0], action_id="first")
    second = plugin.select_seeds([0], action_id="second")
    assert first["status"] == "ok"
    assert second == {
        "status": "waiting", "action": "select_seeds", "action_id": "second",
        "reason": "selection_already_submitted",
    }
    assert injector.rocks == 1


def test_select_seeds_rejects_allocated_but_hidden_chooser(tmp_path):
    plugin = PvZPlugin(log_dir=tmp_path)

    class Memory:
        def is_connected(self):
            return True

    class Injector:
        def choose_card(self, seed):
            raise AssertionError("hidden chooser must never be injected")

    class Reader:
        def read_state(self):
            return GameState(
                game_ui=2, game_mode=0, adventure_level=8,
                seed_chooser_ready=False, seed_chooser_visible=False,
                seeds=[_seed(0, 0)],
            )

    plugin._memory = Memory()
    plugin._injector = Injector()
    plugin._reader = Reader()
    with pytest.raises(ValueError, match="当前不在选卡界面"):
        plugin.select_seeds([0])


def _seed(index: int, plant_type: int, *, cost: int = 0) -> SeedInfo:
    return SeedInfo(
        index=index, plant_type=plant_type, name=f"type-{plant_type}",
        sun_cost=cost, cd=0, initial_cd=0, is_usable=True,
        imitator_type=-1, x=100 + index * 50, y=8, width=50, height=70,
    )


def test_conveyor_rejects_empty_negative_card_before_any_injection(tmp_path):
    plugin = PvZPlugin(log_dir=tmp_path)

    class Memory:
        def is_connected(self):
            return True

    class Reader:
        def read_state(self):
            return GameState(
                game_ui=3, game_mode=0, adventure_level=10,
                seeds=[_seed(0, -1)],
            )

    class Injector:
        def __getattr__(self, name):
            raise AssertionError(f"empty conveyor slot must not call injector.{name}")

    plugin._memory = Memory()
    plugin._reader = Reader()
    plugin._injector = Injector()
    with pytest.raises(ValueError, match="当前为空"):
        plugin.place_plant(0, 2, 4)


def test_conveyor_card_can_be_planted_with_zero_sun(tmp_path):
    plugin = PvZPlugin(log_dir=tmp_path)

    class Memory:
        def is_connected(self):
            return True

    class Reader:
        planted = False

        def read_state(self):
            plants = []
            if self.planted:
                plants = [PlantInfo(
                    index=0, plant_type=3, name="坚果", row=2, col=4,
                    hp=4000, hp_max=4000, state=0,
                    is_sleeping=False, is_crushed=False,
                )]
            return GameState(
                game_ui=3, game_mode=0, adventure_level=10, sun=0,
                seeds=[_seed(0, 3, cost=50)], plants=plants,
            )

    reader = Reader()

    class Injector:
        def release_mouse(self):
            return None

        def grid_to_pixel(self, row, col):
            return 400, 300

        def window_drag(self, *args, **kwargs):
            reader.planted = True

    plugin._memory = Memory()
    plugin._reader = reader
    plugin._injector = Injector()
    result = plugin.place_plant(0, 2, 4)
    assert result["status"] == "ok"
    assert result["plant_type"] == 3


def test_state_hides_empty_conveyor_slots_from_ai(tmp_path):
    plugin = PvZPlugin(log_dir=tmp_path)

    class Memory:
        def is_connected(self):
            return True

    class Reader:
        def read_state(self):
            return GameState(
                game_ui=3, game_mode=0, adventure_level=10, sun=0,
                seeds=[_seed(0, 3, cost=50), _seed(1, -1)],
            )

    plugin._memory = Memory()
    plugin._reader = Reader()
    plugin._injector = object()
    state = plugin.read_state()
    assert [card["index"] for card in state["cards"]] == [0]
    assert state["cards"][0]["ready"] is True
    assert state["cards"][0]["requires_sun"] is False


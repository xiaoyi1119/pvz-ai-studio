import json
from types import SimpleNamespace
from unittest.mock import patch

from pvz_agent_plugin.agent import DeepSeekAgent


class FakePlugin:
    connected = True
    events = []

    def connect(self):
        return None

    def emit(self, *args, **kwargs):
        self.events.append((args, kwargs))
        return None

    def context_text(self, **kwargs):
        return json.dumps({"game": {"in_battle": True, "ui": 3}})

    def snapshot_context(self, *args, **kwargs):
        return None


def test_nested_deepseek_arguments_are_flattened():
    reply = {
        "choices": [{
            "message": {
                "tool_calls": [{
                    "function": {
                        "name": "pvz_action",
                        "arguments": json.dumps({
                            "arguments": {"action": "wait", "time": 1.0}
                        }),
                    }
                }]
            }
        }]
    }

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return reply

    def fake_post(url, **kwargs):
        body = kwargs["json"]
        captured["body"] = body
        assert body["thinking"] == {"type": "disabled"}
        assert body["tool_choice"] == "required"
        return FakeResponse()

    agent = DeepSeekAgent(FakePlugin(), "https://example.invalid/v1", "secret", "deepseek-flash")
    with patch.object(agent._client, "post", fake_post):
        feedback = {"status": "rejected", "error": "occupied"}
        assert agent._request_action("sun=50", feedback=feedback) == {"action": "wait", "time": 1.0}
    assert "previous_action_feedback" in captured["body"]["messages"][1]["content"]
    agent.close()


def test_rejected_actions_do_not_stop_agent_as_system_failures():
    plugin = FakePlugin()
    plugin.events = []
    plugin.calls = 0

    def execute(*args, **kwargs):
        plugin.calls += 1
        raise ValueError("主体层已有豌豆射手")

    plugin.execute = execute
    agent = DeepSeekAgent(plugin, "https://example.invalid/v1", "secret", "deepseek-flash")
    actions = [
        {"action": "place_plant", "card_index": 1, "row": 1, "col": 0},
        {"action": "place_plant", "card_index": 1, "row": 1, "col": 0},
        {"action": "place_plant", "card_index": 1, "row": 1, "col": 0},
        {"action": "terminate", "status": "success"},
    ]
    with patch.object(agent, "_request_action", side_effect=actions):
        agent._run()
    assert agent.action_rejections == 3
    assert agent.system_failures == 0
    assert plugin.calls == 3
    assert not any(args[1] == "stopped_after_failures" for args, _ in plugin.events if len(args) > 1)
    agent.close()


def test_paused_game_waits_without_calling_model():
    plugin = FakePlugin()
    plugin.events = []
    plugin.context_text = lambda **kwargs: json.dumps({
        "game": {"in_battle": True, "ui": 3, "paused": True}
    })
    agent = DeepSeekAgent(plugin, "https://example.invalid/v1", "secret", "deepseek-flash")

    def stop_after_first_wait(_seconds):
        agent._stop.set()
        return True

    agent._stop.wait = stop_after_first_wait
    with patch.object(agent, "_request_action") as request_action:
        agent._run()
    request_action.assert_not_called()
    assert agent.phase == "stopped"
    agent.close()


def test_special_flow_waits_without_calling_model():
    plugin = FakePlugin()
    plugin.events = []
    plugin.context_text = lambda **kwargs: json.dumps({
        "game": {"in_battle": True, "ui": 3, "paused": False},
        "flow": {"phase": "special_level", "agent_enabled": False},
    })
    agent = DeepSeekAgent(plugin, "https://example.invalid/v1", "secret", "deepseek-flash")

    def stop_after_first_wait(_seconds):
        agent._stop.set()
        return True

    agent._stop.wait = stop_after_first_wait
    with patch.object(agent, "_request_action") as request_action:
        agent._run()
    request_action.assert_not_called()
    agent.close()


def test_level_memory_is_scoped_by_level_key():
    agent = DeepSeekAgent(FakePlugin(), "https://example.invalid/v1", "secret", "deepseek-flash")
    agent.iteration = 3
    agent._remember_level_action("adventure:03", "wait", {"time": 1}, "ok", {"status": "ok"})
    level_three, _ = agent._context_with_level_memory({
        "game": {"level_key": "adventure:03"},
        "flow": {"level_key": "adventure:03"},
    })
    level_four, _ = agent._context_with_level_memory({
        "game": {"level_key": "adventure:04"},
        "flow": {"level_key": "adventure:04"},
    })
    assert len(json.loads(level_three)["level_memory"]["recent_actions"]) == 1
    assert json.loads(level_four)["level_memory"]["recent_actions"] == []
    agent.close()


def test_reward_flow_waits_for_fade_then_uses_local_script():
    plugin = FakePlugin()
    plugin.advance_reward_flow = lambda completed_level, step: {
        "status": "ok", "completed_level": completed_level, "step": step,
    }
    agent = DeepSeekAgent(plugin, "https://example.invalid/v1", "secret", "deepseek-flash")
    agent._last_adventure_level = 10
    flow = {"phase": "reward", "reward_flow": "zombie_note"}
    with patch("pvz_agent_plugin.agent.time.monotonic", side_effect=[100.0, 104.0]):
        agent._advance_reward_locally({}, flow)
        assert agent.last_action is None
        agent._advance_reward_locally({}, flow)
    assert agent.last_action["action"] == "advance_reward_flow"
    assert agent.last_action["arguments"] == {"completed_level": 10, "step": 0}
    agent.close()


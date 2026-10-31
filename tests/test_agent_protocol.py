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


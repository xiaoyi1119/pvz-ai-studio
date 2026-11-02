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

    agent = DeepSeekAgent(FakePlugin(), "https://api.deepseek.com", "secret", "deepseek-flash")
    with patch.object(agent._client, "post", fake_post):
        feedback = {"status": "rejected", "error": "occupied"}
        assert agent._request_action("sun=50", feedback=feedback) == {"action": "wait", "time": 1.0}
    assert "previous_action_feedback" in captured["body"]["messages"][1]["content"]
    agent.close()


def test_openai_compatible_provider_does_not_receive_deepseek_thinking_field():
    reply = {
        "choices": [{"message": {"tool_calls": [{"function": {
            "name": "pvz_action", "arguments": json.dumps({"action": "wait", "time": 1}),
        }}]}}]
    }

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return reply

    def fake_post(_url, **kwargs):
        assert "thinking" not in kwargs["json"]
        return FakeResponse()

    agent = DeepSeekAgent(FakePlugin(), "https://openrouter.ai/api/v1", "secret", "provider/model")
    with patch.object(agent._client, "post", fake_post):
        assert agent._request_action('{"game":{"in_battle":true}}')["action"] == "wait"
    agent.close()


def test_user_instruction_reply_and_game_action_share_one_model_request():
    reply = {
        "choices": [{"message": {"tool_calls": [{"function": {
            "name": "pvz_action",
            "arguments": json.dumps({
                "action": "place_plant",
                "card_index": 2,
                "row": 3,
                "col": 1,
                "user_reply": "收到，本局会优先守住第 4 路。",
            }, ensure_ascii=False),
        }}]}}]
    }
    captured = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return reply

    def fake_post(url, **kwargs):
        captured.append(kwargs["json"])
        return FakeResponse()

    agent = DeepSeekAgent(FakePlugin(), "https://example.invalid/v1", "secret", "deepseek-flash")
    pending = {"id": "ui-000001", "text": "这局优先守住第 4 路"}
    with patch.object(agent._client, "post", fake_post):
        call = agent._request_action(
            '{"game":{"in_battle":true}}',
            pending_instruction=pending,
            active_instructions=[{"id": "ui-old", "text": "本局保留 100 阳光"}],
        )

    assert len(captured) == 1
    assert call == {
        "action": "place_plant", "card_index": 2, "row": 3, "col": 1,
        "user_reply": "收到，本局会优先守住第 4 路。",
    }
    content = captured[0]["messages"][1]["content"]
    assert "<new_user_instruction>" in content
    assert "这局优先守住第 4 路" in content
    assert "<current_match_user_instructions>" in content
    assert captured[0]["max_tokens"] == 384
    agent.close()


def test_user_instruction_stays_active_only_for_current_level():
    agent = DeepSeekAgent(FakePlugin(), "https://example.invalid/v1", "secret", "deepseek-flash")
    agent._sync_instruction_level("adventure:14")
    with patch.object(agent, "start") as start:
        record = agent.submit_user_instruction("优先保住中路", auto_start_goal="完成当前关卡")
    start.assert_called_once_with("完成当前关卡")
    agent._complete_instruction(
        record["id"], "收到。", "wait", {"time": 0.5}, "adventure:14",
    )

    current = agent.user_instruction_status()
    assert current["active"][0]["text"] == "优先保住中路"
    assert current["active"][0]["action"] == {
        "action": "wait", "arguments": {"time": 0.5},
    }

    agent._sync_instruction_level("adventure:15")
    changed = agent.user_instruction_status()
    assert changed["active"] == []
    assert changed["latest"]["state"] == "expired"
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


def test_reward_flow_never_sends_automatic_input():
    plugin = FakePlugin()
    plugin.advance_reward_flow = lambda completed_level, step: (_ for _ in ()).throw(
        AssertionError("reward screens must remain manual")
    )
    agent = DeepSeekAgent(plugin, "https://example.invalid/v1", "secret", "deepseek-flash")
    agent._last_adventure_level = 10
    flow = {"phase": "reward", "reward_flow": "zombie_note"}
    agent._advance_reward_locally({}, flow)
    agent._advance_reward_locally({}, flow)
    assert agent.phase == "waiting_reward_manual"
    assert agent.last_action is None
    agent.close()


def test_frozen_battle_clock_is_detected_after_ten_seconds():
    agent = DeepSeekAgent(FakePlugin(), "https://example.invalid/v1", "secret", "deepseek-flash")
    with patch("pvz_agent_plugin.agent.time.monotonic", side_effect=[100.0, 105.0, 111.0]):
        assert agent._battle_clock_is_moving({"clock": 900}) is True
        assert agent._battle_clock_is_moving({"clock": 900}) is True
        assert agent._battle_clock_is_moving({"clock": 900}) is False
    agent.close()


def test_seed_selection_waits_for_stable_visible_ui():
    agent = DeepSeekAgent(FakePlugin(), "https://example.invalid/v1", "secret", "deepseek-flash")
    with patch("pvz_agent_plugin.agent.time.monotonic", side_effect=[100.0, 100.8, 101.1]):
        assert agent._selection_ui_is_stable("adventure:08") is False
        assert agent._selection_ui_is_stable("adventure:08") is False
        assert agent._selection_ui_is_stable("adventure:08") is True
    agent.close()


def test_selection_turn_is_explicitly_decided_by_ai_not_plugin_preset():
    reply = {
        "choices": [{"message": {"tool_calls": [{"function": {
            "name": "pvz_action",
            "arguments": json.dumps({
                "action": "select_seeds", "seeds": [1, 3, 4, 5, 8, 11],
                "selection_reason": "economy, defense and graves",
            }),
        }}]}}]
    }
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return reply

    def fake_post(url, **kwargs):
        captured["body"] = kwargs["json"]
        return FakeResponse()

    context = json.dumps({
        "flow": {"phase": "seed_selection"},
        "seed_selection": {
            "required_count": 6,
            "available_plants": [{"type": i, "name": f"plant-{i}"} for i in range(12)],
            "decision_owner": "ai",
        },
        "spawn_plan": [],
    })
    agent = DeepSeekAgent(FakePlugin(), "https://example.invalid/v1", "secret", "deepseek-flash")
    agent.configure_strategy_assist(True)
    with patch.object(agent._client, "post", fake_post):
        call = agent._request_action(context)
    assert call["seeds"] == [1, 3, 4, 5, 8, 11]
    system = captured["body"]["messages"][0]["content"]
    assert "plugin has no preset deck" in system
    assert "Do not mechanically choose the first N" in system
    assert captured["body"]["max_tokens"] == 384
    agent.close()


def test_strategy_assist_is_a_hard_gate_and_only_injects_current_level_hint():
    reply = {
        "choices": [{"message": {"tool_calls": [{"function": {
            "name": "pvz_action", "arguments": '{"action":"wait","time":1}'
        }}]}}]
    }
    captured = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return reply

    def fake_post(url, **kwargs):
        captured.append(kwargs["json"]["messages"][0]["content"])
        return FakeResponse()

    context = json.dumps({
        "game": {"mode": 0, "level": 11, "level_label": "冒险 2-1"},
        "flow": {"phase": "battle", "level_label": "冒险 2-1"},
    })
    agent = DeepSeekAgent(FakePlugin(), "https://example.invalid/v1", "secret", "deepseek-flash")
    with patch.object(agent._client, "post", fake_post):
        agent._request_action(context)
        agent.configure_strategy_assist(True)
        agent._request_action(context)

    assert "PVZ 辅助增强" not in captured[0]
    assert "PVZ 辅助增强：整体玩法" in captured[1]
    assert "【当前关卡：冒险 2-1】" in captured[1]
    assert "首个夜晚尚无阳光菇" in captured[1]
    assert "首次泳池关" not in captured[1]
    agent.close()


def test_strategy_assist_flags_puff_shroom_spam_from_live_board():
    reply = {
        "choices": [{"message": {"tool_calls": [{"function": {
            "name": "pvz_action", "arguments": '{"action":"wait","time":1}'
        }}]}}]
    }
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return reply

    def fake_post(url, **kwargs):
        captured["system"] = kwargs["json"]["messages"][0]["content"]
        return FakeResponse()

    cells = []
    for index in range(7):
        cells.append({
            "row": index % 5, "col": 2 + index // 5,
            "layers": {"main": [{"name": "小喷菇", "type": 8}]},
        })
    cells.append({
        "row": 0, "col": 0,
        "layers": {"main": [{"name": "阳光菇", "type": 9}]},
    })
    context = json.dumps({
        "game": {"mode": 0, "level": 14, "level_label": "冒险 2-4", "board_rows": 5},
        "flow": {"phase": "battle", "level_label": "冒险 2-4"},
        "occupied_cells": cells,
    })
    agent = DeepSeekAgent(FakePlugin(), "https://example.invalid/v1", "secret", "deepseek-flash")
    agent.configure_strategy_assist(True)
    with patch.object(agent._client, "post", fake_post):
        agent._request_action(context)
    assert "小喷菇×7" in captured["system"]
    assert "当前小喷菇明显过量" in captured["system"]
    assert "5–7株后排阳光菇" in captured["system"]
    agent.close()


def test_level_memory_keeps_plant_identity_for_repetition_detection():
    agent = DeepSeekAgent(FakePlugin(), "https://example.invalid/v1", "secret", "deepseek-flash")
    agent._remember_level_action(
        "adventure:14", "place_plant", {"card_index": 1, "row": 2, "col": 3}, "ok",
        {"status": "ok", "plant": "小喷菇", "plant_type": 8, "row": 2, "col": 3},
    )
    memory = agent._level_memories["adventure:14"][0]["result"]
    assert memory["plant"] == "小喷菇"
    assert memory["plant_type"] == 8
    agent.close()


def test_level_memory_compacts_history_and_summarizes_rejected_targets():
    agent = DeepSeekAgent(FakePlugin(), "https://example.invalid/v1", "secret", "deepseek-flash")
    for iteration in range(12):
        agent.iteration = iteration
        agent._remember_level_action(
            "adventure:14", "place_plant", {"card_index": 0, "row": 2, "col": 4},
            "rejected", {"error": "目标格有墓碑"},
        )
    context, _ = agent._context_with_level_memory({
        "game": {"level_key": "adventure:14"},
        "flow": {"level_key": "adventure:14"},
    })
    memory = json.loads(context)["level_memory"]
    assert len(memory["recent_actions"]) == 8
    assert memory["rejected_targets"] == {"place_plant@[2,4]": 12}
    agent.close()


def test_strategy_snapshot_recommends_safe_grave_and_planned_shovel_replacement():
    observed = {
        "game": {"level": 14, "board_rows": 5},
        "occupied_cells": [
            {"row": 0, "col": 2, "layers": {"main": [{"name": "小喷菇", "type": 8}]}},
            {"row": 0, "col": 1, "layers": {"main": [{"name": "大喷菇", "type": 10}]}},
        ],
        "cards": [
            {"index": 2, "name": "大喷菇", "actual_type": 10, "ready": True},
            {"index": 3, "name": "墓碑吞噬者", "actual_type": 11, "ready": True},
        ],
        "lane_threats": [
            {"row": 0, "count": 0, "nearest_col": None, "eating_count": 0},
            {"row": 2, "count": 1, "nearest_col": 6, "eating_count": 0},
        ],
        "decision_board": {"grave_cells": [[2, 5], [4, 3]]},
    }
    snapshot = DeepSeekAgent._strategy_snapshot(observed)
    assert "可计划替换：小喷菇[[0, 2]]" in snapshot
    assert "2:大喷菇" in snapshot
    assert "安全清墓碑候选：[[4, 3], [2, 5]]" in snapshot


def test_tool_protocol_requires_card_legal_targets_and_shovel_replacement_plan():
    reply = {
        "choices": [{"message": {"tool_calls": [{"function": {
            "name": "pvz_action", "arguments": '{"action":"wait","time":1}'
        }}]}}]
    }
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return reply

    def fake_post(url, **kwargs):
        captured["body"] = kwargs["json"]
        return FakeResponse()

    agent = DeepSeekAgent(FakePlugin(), "https://example.invalid/v1", "secret", "deepseek-flash")
    agent.configure_strategy_assist(True)
    with patch.object(agent._client, "post", fake_post):
        agent._request_action(json.dumps({"flow": {"phase": "battle"}, "game": {}}))
    system = captured["body"]["messages"][0]["content"]
    properties = captured["body"]["tools"][0]["function"]["parameters"]["properties"]
    assert "MUST appear in that exact card's legal_targets" in system
    assert "replacement_card_index" in system
    assert "replacement_card_index" in properties
    assert "decision_reason" in properties
    agent.close()


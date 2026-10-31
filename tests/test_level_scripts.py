from pvz_agent_plugin.level_scripts import (
    ADVENTURE_LEVEL_SCRIPTS,
    available_adventure_seed_types,
    get_adventure_script,
)


def test_every_first_adventure_level_has_an_explicit_script():
    assert set(ADVENTURE_LEVEL_SCRIPTS) == set(range(1, 51))
    assert all(script.strategy for script in ADVENTURE_LEVEL_SCRIPTS.values())


def test_tutorial_conveyor_special_and_chooser_stages_are_separate():
    assert [get_adventure_script(level).seed_system for level in range(1, 8)] == [
        "fixed", "fixed", "fixed", "fixed", "special", "fixed", "fixed",
    ]
    for level in (10, 20, 25, 30, 40, 45, 50):
        assert get_adventure_script(level).seed_system == "conveyor"
    for level in (5, 15, 35):
        assert get_adventure_script(level).seed_system == "special"
    assert get_adventure_script(8).seed_system == "chooser"
    assert get_adventure_script(49).seed_system == "chooser"


def test_ai_context_unlocks_melon_pult_for_level_five_nine():
    assert 39 not in available_adventure_seed_types(48)
    assert 39 in available_adventure_seed_types(49)


def test_reward_followups_are_explicit():
    assert get_adventure_script(10).reward_flow == "zombie_note"
    assert get_adventure_script(15).reward_flow == "almanac"
    assert get_adventure_script(25).reward_flow == "store_intro"
    assert get_adventure_script(45).reward_flow == "zen_tutorial"
    assert get_adventure_script(50).reward_flow == "credits_note"

from pvz_agent_plugin.flow import classify_flow


def flow(scene, level, *, mode=0, background=0, chooser=False):
    return classify_flow(
        app_scene=scene,
        game_mode=mode,
        adventure_level=level,
        background=background,
        seed_chooser_ready=chooser,
    )


def test_reward_and_special_levels_never_call_agent():
    assert flow(5, 4).phase == "reward"
    assert flow(5, 4).agent_enabled is False
    for level in (5, 15, 35):
        decision = flow(3, level)
        assert decision.kind == "special"
        assert decision.agent_enabled is False


def test_seed_chooser_only_runs_when_real_chooser_pointer_is_ready():
    # First-adventure 1-1 through 1-7 use fixed tutorial seed banks even
    # though the game allocates a hidden SeedChooserScreen object.
    assert flow(2, 4, chooser=True).phase == "level_intro"
    assert flow(3, 4, chooser=True).seed_system == "fixed"
    assert flow(2, 8, chooser=False).phase == "level_intro"
    decision = flow(2, 8, chooser=True)
    assert decision.phase == "seed_selection"
    assert decision.agent_enabled is True


def test_conveyor_planting_is_supported_but_has_no_seed_selection():
    intro = flow(2, 10, chooser=True)
    assert intro.kind == "conveyor"
    assert intro.phase == "level_intro"
    assert intro.agent_enabled is False
    battle = flow(3, 10)
    assert battle.seed_system == "conveyor"
    assert battle.agent_enabled is True


def test_pool_and_fog_have_six_rows_other_backgrounds_have_five():
    assert flow(3, 21, background=2).board_rows == 6
    assert flow(3, 31, background=3).board_rows == 6
    assert flow(3, 41, background=4).board_rows == 5


def test_non_planting_game_modes_are_suppressed():
    for mode in (17, 18, 20, 23, 30, 33, 43, 51, 61):
        decision = flow(3, 0, mode=mode)
        assert decision.phase == "special_level"
        assert decision.agent_enabled is False

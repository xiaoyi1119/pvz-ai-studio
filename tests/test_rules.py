from types import SimpleNamespace

from pvz_agent_plugin.rules import build_board, check_placement


def plant(plant_type, row=0, col=0, sleeping=False, index=0):
    return SimpleNamespace(
        index=index, plant_type=plant_type, name=f"p{plant_type}", row=row, col=col,
        hp=300, hp_max=300, hp_ratio=1.0, state=0,
        is_sleeping=sleeping, is_crushed=False,
    )


def grid_item(item_type, row=0, col=0):
    return SimpleNamespace(item_type=item_type, row=row, col=col)


def test_pumpkin_can_stack_but_not_duplicate():
    sunflower = plant(1)
    assert check_placement(30, 0, 0, [sunflower]).allowed
    assert not check_placement(30, 0, 0, [sunflower, plant(30, index=1)]).allowed


def test_coffee_requires_sleeping_mushroom():
    assert check_placement(35, 0, 0, [plant(10, sleeping=True)]).allowed
    assert not check_placement(35, 0, 0, [plant(10, sleeping=False)]).allowed
    assert not check_placement(35, 0, 0, [plant(1)]).allowed


def test_upgrade_and_cob_require_bases():
    assert check_placement(40, 0, 0, [plant(7)]).allowed
    assert not check_placement(40, 0, 0, [plant(0)]).allowed
    kernels = [plant(34, col=2), plant(34, col=3, index=1)]
    assert check_placement(47, 0, 2, kernels).allowed
    assert not check_placement(47, 0, 3, kernels).allowed


def test_board_keeps_multiple_layers():
    cell = build_board([plant(16), plant(1, index=1), plant(30, index=2)])[0]
    assert cell["layers"]["support"][0]["type"] == 16
    assert cell["layers"]["main"][0]["type"] == 1
    assert cell["layers"]["armor"][0]["type"] == 30


def test_grave_blocks_normal_plants_and_only_accepts_grave_buster():
    grave = grid_item(1, row=2, col=6)
    blocked = check_placement(8, 2, 6, [], [grave])
    assert not blocked.allowed
    assert "墓碑" in blocked.reason

    allowed = check_placement(11, 2, 6, [], [grave])
    assert allowed.allowed
    assert "grave_at_target" in allowed.requirements

    no_grave = check_placement(11, 2, 5, [], [grave])
    assert not no_grave.allowed
    assert "真实墓碑格" in no_grave.reason

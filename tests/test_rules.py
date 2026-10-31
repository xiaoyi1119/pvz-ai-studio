from types import SimpleNamespace

from pvz_agent_plugin.rules import build_board, check_placement


def plant(plant_type, row=0, col=0, sleeping=False, index=0):
    return SimpleNamespace(
        index=index, plant_type=plant_type, name=f"p{plant_type}", row=row, col=col,
        hp=300, hp_max=300, hp_ratio=1.0, state=0,
        is_sleeping=sleeping, is_crushed=False,
    )


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

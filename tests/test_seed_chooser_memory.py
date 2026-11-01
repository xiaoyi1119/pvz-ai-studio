from pvz_agent_plugin.pvz_native.memory import PvZMemory


def test_seed_chooser_requires_game_mouse_visibility_flag():
    memory = object.__new__(PvZMemory)
    memory.get_select_card_ui_ptr = lambda: 0x1000
    memory.read_bool = lambda address: address == 0x1059
    values = {0x1D1C: 240, 0x1D38: 0}
    memory.read_int = lambda address: values[address]

    status = memory.get_seed_chooser_status()

    assert status == {
        "pointer": 0x1000,
        "visible": True,
        "age": 240,
        "choose_state": 0,
    }

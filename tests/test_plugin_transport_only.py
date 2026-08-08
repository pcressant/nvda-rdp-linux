from __future__ import annotations

from pathlib import Path

PLUGIN_SOURCE = Path(__file__).parents[1] / "freerdp-plugin" / "nvda_rdp_dvc.c"


def test_c_plugin_contains_no_rdaccess_protocol_logic() -> None:
    source = PLUGIN_SOURCE.read_text()
    lower_source = source.casefold()

    for forbidden in (
        "xon",
        "xoff",
        "0x11",
        "0x13",
        "rdaccess",
        "protocolversion",
        "json",
        "speak",
        "cancel",
        "indexcommand",
        "charactermodecommand",
        "speech dispatcher",
        "brlapi",
    ):
        assert forbidden not in lower_source


def test_c_plugin_writes_only_ipc_payloads_to_dvc() -> None:
    source = " ".join(PLUGIN_SOURCE.read_text().split())
    assert source.count("->Write(") == 1
    assert "->Write(callback->base.channel, size, payload, NULL)" in source

from __future__ import annotations

from nvda_rdp_linux.rdaccess.capslock import CapsLockSync
from nvda_rdp_linux.rdaccess.notify import ConnectionNotifier, message_for


class FakeBackend:
    def __init__(self, state: bool) -> None:
        self.state = state
        self.toggles = 0

    def get(self) -> bool:
        return self.state

    def toggle(self) -> None:
        self.toggles += 1
        self.state = not self.state


def test_caps_lock_is_aligned_only_outside_freerdp() -> None:
    backend = FakeBackend(False)
    sync = CapsLockSync(backend)
    sync.set_remote_focused(True)
    sync.set_remote_state(True)
    assert backend.toggles == 0
    sync.set_remote_focused(False)
    assert backend.state is True
    sync.set_remote_focused(True)
    sync.set_remote_focused(False)
    assert backend.toggles == 1


def test_caps_lock_unknown_remote_state_changes_nothing() -> None:
    backend = FakeBackend(True)
    CapsLockSync(backend).set_remote_focused(False)
    assert backend.toggles == 0


class FakeAudio:
    def __init__(self) -> None:
        self.waves: list[str] = []

    def wave(self, file_name: str) -> bool:
        self.waves.append(file_name)
        return True


def test_notifications_by_mode() -> None:
    spoken: list[str] = []
    audio = FakeAudio()
    ConnectionNotifier(
        "both", kind="speech", language="fr", speak=spoken.append, audio=audio
    ).notify(True)
    ConnectionNotifier(
        "none", kind="speech", language="fr", speak=spoken.append, audio=audio
    ).notify(False)
    assert spoken == ["Parole distante connectée"]
    assert audio.waves == ["waves\\connected.wav"]


def test_notification_language_fallback() -> None:
    assert message_for("braille", False, "de_DE") == "Remote braille disconnected"
    assert message_for("braille", True, "fr_FR") == "Braille distant connecté"


def test_notification_failure_is_contained() -> None:
    def fail(_text: str) -> None:
        raise ConnectionError("speech-dispatcher gone")

    ConnectionNotifier("message", kind="speech", language="en", speak=fail).notify(True)

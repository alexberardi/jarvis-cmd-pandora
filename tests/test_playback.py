"""The IJarvisMusicCommand side: what the node's display, the app and the built-in transport
commands see. Pause, volume and position are read from mpv's control socket, here a fake one."""
from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import commands.pandora.command as command_mod
from commands.pandora.command import PandoraCommand
from pandora_shared.pandora_service import PandoraService


class FakeMPV:
    """mpv's JSON IPC: get_property / set_property on pause, volume and time-pos."""

    def __init__(self, path: str) -> None:
        self.props: dict = {"pause": False, "volume": 80.0, "time-pos": 61.5}
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(path)
        self._sock.listen()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            with conn:
                req = json.loads(conn.makefile().readline())
                verb, name, *value = req["command"]
                # mpv interleaves events with replies.
                conn.sendall(b'{"event":"playback-restart"}\n')
                if verb == "set_property":
                    self.props[name] = value[0]
                reply = {"request_id": req["request_id"], "error": "success", "data": self.props.get(name)}
                conn.sendall(json.dumps(reply).encode() + b"\n")

    def close(self) -> None:
        self._sock.close()


def _track(**over) -> SimpleNamespace:
    t = SimpleNamespace(song_name="Song", artist_name="Artist", album_name="Album", song_rating=0,
                        track_length=200, album_art_url="https://content-images.p-cdn.com/a.jpg",
                        thumbs_up=MagicMock())
    t.__dict__.update(over)
    return t


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> Iterator[PandoraService]:
    """A logged-in service playing a track through mpv, cached as the command finds it."""
    with patch("pandora_shared.pandora_service._find_player", return_value="mpv"):
        svc = PandoraService()
    svc._ipc_path = os.path.join(tempfile.mkdtemp(), "mpv.sock")
    svc._current_station = SimpleNamespace(name="Jazz Radio")
    svc._current_track = _track()
    svc._player_process = MagicMock()
    svc._playing = True
    monkeypatch.setattr(command_mod, "_cached_service", svc)
    yield svc


@pytest.fixture
def mpv(service: PandoraService) -> Iterator[FakeMPV]:
    fake = FakeMPV(service._ipc_path)
    yield fake
    fake.close()


@pytest.fixture
def cmd() -> PandoraCommand:
    with patch("commands.pandora.command.JarvisStorage"):
        return PandoraCommand()


def test_status_reads_the_player(cmd: PandoraCommand, mpv: FakeMPV) -> None:
    assert cmd.playback_status().to_dict() == {
        "state": "playing", "media_title": "Song", "media_artist": "Artist", "media_album": "Album",
        "context": "Jazz Radio", "actions": ["play", "pause", "next", "like", "volume"],
        "position": 61.5, "duration": 200.0, "volume": 80, "liked": False,
    }


def test_nothing_playing_never_logs_in(cmd: PandoraCommand, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(command_mod, "_cached_service", None)
    assert cmd.playback_status() is None
    assert cmd.playback_artwork() is None
    with pytest.raises(RuntimeError):
        cmd.playback_control("pause")


def test_stopped_is_nothing_playing(cmd: PandoraCommand, service: PandoraService) -> None:
    service.stop()
    assert cmd.playback_status() is None


def test_unanswering_mpv_still_shows_the_track(cmd: PandoraCommand, service: PandoraService) -> None:
    # No socket: mpv starting up, or held stopped by the node while the user talks.
    st = cmd.playback_status()
    assert (st.state, st.media_title, st.position, st.volume) == ("playing", "Song", None, None)


def test_controls_read_back(cmd: PandoraCommand, service: PandoraService, mpv: FakeMPV) -> None:
    assert cmd.playback_control("pause").state == "paused"
    assert mpv.props["pause"] is True
    assert cmd.playback_control("play").state == "playing"
    assert mpv.props["pause"] is False
    assert cmd.playback_control("volume", volume=130).volume == 100
    assert cmd.playback_control("like").liked is True
    service._current_track.thumbs_up.assert_called_once()
    with patch.object(service, "_play_next") as play_next:
        cmd.playback_control("next")
    play_next.assert_called_once()
    for action in ("previous", "seek", "shuffle", "repeat"):
        with pytest.raises(ValueError):
            cmd.playback_control(action)


def test_players_without_a_socket_only_skip_and_like(cmd: PandoraCommand, service: PandoraService) -> None:
    service._player_bin = "ffplay"
    assert cmd.playback_status().actions == ("next", "like")
    with pytest.raises(RuntimeError):
        cmd.playback_control("pause")


def test_mpv_gets_the_control_socket(service: PandoraService) -> None:
    # Only this module's subprocess: the SDK's Bluetooth lookup keeps the real one.
    with patch("pandora_shared.pandora_service.subprocess") as sp, \
            patch("pandora_shared.pandora_service.threading.Thread"):
        service._start_player("https://audio")
    assert f"--input-ipc-server={service._ipc_path}" in sp.Popen.call_args.args[0]


def test_artwork_is_fetched_once_per_track(cmd: PandoraCommand, service: PandoraService) -> None:
    resp = MagicMock(content=b"\xff\xd8jpeg", headers={"content-type": "image/jpeg"})
    with patch("requests.get", return_value=resp) as get:
        art = cmd.playback_artwork()
        assert (art.data, art.mime) == (b"\xff\xd8jpeg", "image/jpeg")
        assert cmd.playback_artwork() is art
        get.assert_called_once_with("https://content-images.p-cdn.com/a.jpg", timeout=5)
        service._current_track = _track(album_art_url="http://insecure/a.jpg")
        assert cmd.playback_artwork() is None
        assert get.call_count == 1

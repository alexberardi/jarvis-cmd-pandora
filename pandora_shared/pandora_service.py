"""Pandora streaming service wrapper around pydora."""

import os
import re
import shutil
import subprocess
import threading
import time
from typing import Any

try:
    from jarvis_log_client import JarvisLogger
except ImportError:
    import logging

    class JarvisLogger:
        def __init__(self, **kw: str) -> None:
            self._log = logging.getLogger(kw.get("service", __name__))

        def info(self, msg: str, **kw: object) -> None:
            self._log.info(msg)

        def warning(self, msg: str, **kw: object) -> None:
            self._log.warning(msg)

        def error(self, msg: str, **kw: object) -> None:
            self._log.error(msg)

        def debug(self, msg: str, **kw: object) -> None:
            self._log.debug(msg)


logger = JarvisLogger(service="jarvis-node")

# Well-known Android partner credentials (public, used by all unofficial clients)
_PARTNER_SETTINGS: dict[str, str] = {
    "DECRYPTION_KEY": "R=U!LH$O2B#",
    "ENCRYPTION_KEY": "6#26FRL$ZWD",
    "PARTNER_USER": "android",
    "PARTNER_PASSWORD": "AC7IBG09A3DTSYM4R41UJWL07VLN8JI7",
    "DEVICE": "android-generic",
}


def _find_player() -> str | None:
    """Find an available audio player binary.

    Order matters: ffplay is preferred over vlc because vlc refuses to run
    as root, and jarvis-node runs as root on Pi installs. mpv stays first
    because it has the cleanest streaming behavior when available.
    """
    for player in ("mpv", "ffplay", "vlc", "cvlc"):
        if shutil.which(player):
            return player
    return None


def _pulseaudio_accessible() -> bool:
    """Check if PulseAudio is reachable from this process.

    On Pi installs jarvis-node runs as root, but PulseAudio runs per-user
    (as `pi`) — so root can't connect. SDL2-backed players (ffplay) default
    to PulseAudio when libpulse is installed and silently fail to open audio
    when it can't connect. We detect that case and force the player to use
    ALSA directly instead.
    """
    if shutil.which("pactl") is None:
        return False
    try:
        result = subprocess.run(
            ["pactl", "info"],
            capture_output=True, text=True, timeout=2.0,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _pulseaudio_has_real_output() -> bool:
    """Check if PA's default sink is a real audio output (not auto_null/Dummy).

    WirePlumber inserts a placeholder ``auto_null`` (a.k.a. "Dummy Output")
    sink when no real ALSA cards are detected. Routing a player to that
    sink looks fine to the player but produces no audible sound. We treat
    that case the same as "PulseAudio unreachable" — fall back to ALSA
    directly so the local speaker hears something.
    """
    if shutil.which("pactl") is None:
        return False
    try:
        result = subprocess.run(
            ["pactl", "get-default-sink"],
            capture_output=True, text=True, timeout=2.0,
        )
        if result.returncode != 0:
            return False
        sink = result.stdout.strip().lower()
        return bool(sink) and "auto_null" not in sink and "dummy" not in sink
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


# Minimum number of seconds a player must stay alive before we treat its exit
# as "track ended naturally" rather than "playback failed immediately".
# Below this threshold we stop the auto-advance loop to prevent runaway
# Pandora API calls (which previously got the account rate-limited).
_MIN_TRACK_DURATION_SECONDS: float = 3.0


class PandoraService:
    """Manages Pandora client, playback state, and audio player subprocess."""

    def __init__(self) -> None:
        self._client: Any = None
        self._stations: list[Any] = []
        self._current_station: Any = None
        self._playlist: Any = None
        self._current_track: Any = None
        self._player_process: subprocess.Popen | None = None
        self._player_started_at: float = 0.0
        self._playback_thread: threading.Thread | None = None
        self._playing: bool = False
        self._player_bin: str | None = _find_player()
        self._last_player_error: str | None = None

    @property
    def is_logged_in(self) -> bool:
        return self._client is not None

    @property
    def is_playing(self) -> bool:
        return self._playing and self._player_process is not None

    def login(self, email: str, password: str) -> None:
        """Authenticate with Pandora."""
        from pandora.clientbuilder import SettingsDictBuilder

        self._client = SettingsDictBuilder(_PARTNER_SETTINGS).build()
        self._client.login(email, password)
        logger.info("Pandora login successful", email=email)

    def list_stations(self) -> list[dict[str, str]]:
        """Return list of user's stations."""
        self._stations = self._client.get_station_list()
        return [
            {"name": s.name, "token": s.token}
            for s in self._stations
            if not s.is_quickmix
        ]

    def find_station(self, query: str) -> Any | None:
        """Find a station by name (fuzzy match).

        Tries, in order:
          1. exact match on normalized name
          2. substring match on normalized name (either direction)
          3. token-overlap match — at least 60% of query tokens appear in
             the station name (handles "1990s summer hits" → "Summer Hits
             of the 90s Radio" where the LLM normalized "the 90s" to "1990s")
        """
        if not self._stations:
            self._stations = self._client.get_station_list()

        query_norm = self._normalize_for_match(query)
        if not query_norm:
            return None

        # 1. Exact match
        for station in self._stations:
            if self._normalize_for_match(station.name) == query_norm:
                return station

        # 2. Substring (either direction)
        for station in self._stations:
            station_norm = self._normalize_for_match(station.name)
            if query_norm in station_norm or station_norm in query_norm:
                return station

        # 3. Token-overlap fallback
        query_tokens = set(query_norm.split())
        if not query_tokens:
            return None
        best: tuple[float, Any] | None = None
        for station in self._stations:
            station_tokens = set(self._normalize_for_match(station.name).split())
            if not station_tokens:
                continue
            overlap = len(query_tokens & station_tokens) / len(query_tokens)
            if overlap >= 0.6 and (best is None or overlap > best[0]):
                best = (overlap, station)
        return best[1] if best else None

    @staticmethod
    def _normalize_for_match(text: str) -> str:
        """Normalize a station name or query for fuzzy matching.

        Lowercases, strips Pandora-specific suffixes ("Radio", "Station"),
        and folds decade phrasings so "1990s" / "90's" / "90s" all compare equal.
        """
        s = text.lower().strip()
        s = re.sub(r"\b19(\d0)s\b", r"\1s", s)   # 1990s -> 90s, 1980s -> 80s
        s = re.sub(r"\b20(\d0)s\b", r"\1s", s)   # 2000s -> 00s, etc.
        s = re.sub(r"\b(\d0)'s\b", r"\1s", s)    # 90's -> 90s
        s = re.sub(r"\b(?:radio|station)\b", "", s)
        s = re.sub(r"[^\w\s]", " ", s)            # drop punctuation
        s = re.sub(r"\s+", " ", s).strip()
        return s

    def play_station(self, station_name: str | None = None) -> dict[str, str]:
        """Start playing a station. Returns current track info."""
        if not self._player_bin:
            raise RuntimeError(
                "No audio player found. Install mpv, vlc, or ffplay."
            )

        if station_name:
            station = self.find_station(station_name)
            if not station:
                raise ValueError(f"Station '{station_name}' not found")
            self._current_station = station
        elif self._current_station is None:
            # Default to first station
            stations = self.list_stations()
            if not stations:
                raise ValueError("No stations found on your account")
            self._current_station = self._stations[0]

        self.stop()
        self._playlist = iter(
            self._client.get_playlist(self._current_station.token)
        )
        return self._play_next()

    def _play_next(self) -> dict[str, str]:
        """Advance to the next track and start playback."""
        track = next(self._playlist, None)
        # Playlist exhausted, fetch a new batch
        if track is None:
            self._playlist = iter(
                self._client.get_playlist(self._current_station.token)
            )
            track = next(self._playlist, None)
            if track is None:
                raise RuntimeError("No tracks available for this station")

        # Skip ads
        if getattr(track, "is_ad", False):
            return self._play_next()

        self._current_track = track
        self._start_player(track.audio_url)
        return self._track_info(track)

    def _start_player(self, audio_url: str) -> None:
        """Start audio player subprocess and background monitor thread.

        Routes audio to Bluetooth speaker if one is connected, otherwise
        plays on the local (default) speaker. stderr is captured so that
        immediate-failure diagnostics survive into _monitor_playback.
        """
        self._kill_player()

        # Decide routing: BT sink wins; else ALSA-direct if PA has no real
        # output; else PA default sink.
        bt_sink: str | None = None
        try:
            from jarvis_command_sdk import BluetoothAudio
            bt_sink = BluetoothAudio.target_sink()
        except ImportError:
            pass

        # Use ALSA-direct when there's no BT and either (a) PA is
        # unreachable or (b) PA's default sink is the placeholder
        # auto_null/Dummy that WirePlumber inserts when no cards are
        # detected. Without (b), the player happily streams into the
        # null sink and produces silence.
        use_alsa_direct = bt_sink is None and (
            not _pulseaudio_accessible() or not _pulseaudio_has_real_output()
        )

        cmd: list[str] = []
        if self._player_bin == "mpv":
            if use_alsa_direct:
                cmd = ["mpv", "--no-video", "--really-quiet",
                       "--ao=alsa", "--audio-device=alsa/output", audio_url]
            else:
                cmd = ["mpv", "--no-video", "--really-quiet", audio_url]
        elif self._player_bin == "ffplay":
            # -loglevel error keeps fatal errors visible while staying quiet
            cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", audio_url]
        elif self._player_bin in ("vlc", "cvlc"):
            if use_alsa_direct:
                cmd = ["cvlc", "--play-and-exit", "--no-video", "-q",
                       "--aout=alsa", "--alsa-audio-device=output", audio_url]
            else:
                cmd = ["cvlc", "--play-and-exit", "--no-video", "-q", audio_url]

        env = dict(os.environ)
        if bt_sink:
            env["PULSE_SINK"] = bt_sink
        elif use_alsa_direct:
            # SDL-backed players (ffplay) need both env vars to bypass PA:
            #   SDL_AUDIODRIVER=alsa  → use ALSA, not pulse
            #   AUDIODEV=output       → open the "output" PCM alias from
            #                            jarvis-node-setup's asound.conf
            #                            (softvol → seeed2micvoicec card)
            env["SDL_AUDIODRIVER"] = "alsa"
            env["AUDIODEV"] = "output"

        self._player_process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        self._player_started_at = time.monotonic()
        self._playing = True

        self._playback_thread = threading.Thread(
            target=self._monitor_playback, daemon=True
        )
        self._playback_thread.start()
        logger.info(
            "Playing track",
            song=self._current_track.song_name,
            artist=self._current_track.artist_name,
        )

    def _monitor_playback(self) -> None:
        """Wait for current track to end, then auto-advance.

        Guards against a runaway loop: if the player exits faster than
        _MIN_TRACK_DURATION_SECONDS we treat it as a failure (stderr captured),
        log the failure, and stop the loop. Without this guard a silently-
        failing player (e.g. vlc-as-root) hammers the Pandora API and
        gets the account rate-limited.
        """
        proc = self._player_process
        if proc is None:
            return

        proc.wait()
        duration = time.monotonic() - self._player_started_at
        rc = proc.returncode
        stderr_text = ""
        if proc.stderr is not None:
            try:
                stderr_text = proc.stderr.read() or ""
            except Exception:
                pass

        if duration < _MIN_TRACK_DURATION_SECONDS:
            self._last_player_error = stderr_text.strip()[:500] or f"player exited with rc={rc}"
            logger.error(
                "Audio player exited too quickly — aborting playback to prevent loop",
                player=self._player_bin,
                return_code=rc,
                duration_seconds=round(duration, 2),
                stderr=self._last_player_error,
            )
            self._playing = False
            return

        if self._playing:
            try:
                self._play_next()
            except Exception as e:
                logger.error("Auto-advance failed", error=str(e))
                self._playing = False

    def _kill_player(self) -> None:
        """Kill the current audio player subprocess."""
        if self._player_process is not None:
            try:
                self._player_process.terminate()
                self._player_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._player_process.kill()
            self._player_process = None

    def skip(self) -> dict[str, str]:
        """Skip to next track. Returns new track info."""
        if not self._current_station:
            raise RuntimeError("Nothing is playing")
        return self._play_next()

    def stop(self) -> None:
        """Stop playback."""
        self._playing = False
        self._kill_player()
        self._current_track = None

    def thumbs_up(self) -> dict[str, str]:
        """Thumbs up the current track."""
        if not self._current_track:
            raise RuntimeError("No track is currently playing")
        self._current_track.thumbs_up()
        logger.info(
            "Thumbs up",
            song=self._current_track.song_name,
            artist=self._current_track.artist_name,
        )
        return self._track_info(self._current_track)

    def thumbs_down(self) -> dict[str, str]:
        """Thumbs down the current track and skip to next."""
        if not self._current_track:
            raise RuntimeError("No track is currently playing")
        self._current_track.thumbs_down()
        logger.info(
            "Thumbs down",
            song=self._current_track.song_name,
            artist=self._current_track.artist_name,
        )
        return self.skip()

    def now_playing(self) -> dict[str, str] | None:
        """Return info about the current track."""
        if not self._current_track:
            return None
        return self._track_info(self._current_track)

    def search_and_create_station(self, query: str) -> dict[str, str]:
        """Search for an artist/song and create a station from it."""
        results = self._client.search(query)
        # Prefer artist match, then song
        if results.artists:
            station = results.artists[0].create_station()
            return {"name": station.name, "token": station.token, "source": "artist"}
        if results.songs:
            station = results.songs[0].create_station()
            return {"name": station.name, "token": station.token, "source": "song"}
        raise ValueError(f"No results found for '{query}'")

    def _track_info(self, track: Any) -> dict[str, str]:
        """Extract track metadata into a dict."""
        return {
            "song": track.song_name,
            "artist": track.artist_name,
            "album": track.album_name,
            "station": self._current_station.name if self._current_station else "",
        }

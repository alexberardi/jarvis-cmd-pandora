"""Pandora streaming service wrapper around pydora."""

import shutil
import subprocess
import threading
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
    """Find an available audio player binary."""
    for player in ("mpv", "vlc", "cvlc", "ffplay"):
        if shutil.which(player):
            return player
    return None


class PandoraService:
    """Manages Pandora client, playback state, and audio player subprocess."""

    def __init__(self) -> None:
        self._client: Any = None
        self._stations: list[Any] = []
        self._current_station: Any = None
        self._playlist: Any = None
        self._current_track: Any = None
        self._player_process: subprocess.Popen | None = None
        self._playback_thread: threading.Thread | None = None
        self._playing: bool = False
        self._player_bin: str | None = _find_player()

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
        """Find a station by name (fuzzy match)."""
        if not self._stations:
            self._stations = self._client.get_station_list()
        query_lower = query.lower()
        # Exact match first
        for station in self._stations:
            if station.name.lower() == query_lower:
                return station
        # Substring match
        for station in self._stations:
            if query_lower in station.name.lower():
                return station
        return None

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
        """Start audio player subprocess and background monitor thread."""
        self._kill_player()

        cmd: list[str] = []
        if self._player_bin in ("mpv",):
            cmd = ["mpv", "--no-video", "--really-quiet", audio_url]
        elif self._player_bin in ("vlc", "cvlc"):
            cmd = ["cvlc", "--play-and-exit", "--no-video", "-q", audio_url]
        elif self._player_bin == "ffplay":
            cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", audio_url]

        self._player_process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
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
        """Wait for current track to end, then auto-advance."""
        if self._player_process is None:
            return
        self._player_process.wait()
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

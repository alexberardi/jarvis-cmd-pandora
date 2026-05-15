"""Pandora radio voice command — stream stations, skip, thumbs up/down."""

import re
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


from jarvis_command_sdk import (
    CommandExample,
    CommandResponse,
    IJarvisCommand,
    IJarvisSecret,
    JarvisPackage,
    JarvisParameter,
    JarvisSecret,
    JarvisStorage,
    PreRouteResult,
    RequestInformation,
)

logger = JarvisLogger(service="jarvis-node")


# Module-level singleton — survives across tool calls within the same process.
# Without this, each MQTT tool_call re-instantiates PandoraCommand and the
# authenticated session is lost, causing repeated logins and rate limiting.
_cached_service: Any = None


class PandoraCommand(IJarvisCommand):
    """Stream Pandora radio stations with voice control."""

    def __init__(self) -> None:
        self._storage = JarvisStorage("pandora")

    def _get_service(self) -> Any:
        """Get or create an authenticated PandoraService (cached at module level)."""
        global _cached_service
        if _cached_service is not None and _cached_service.is_logged_in:
            return _cached_service

        from pandora_shared.pandora_service import PandoraService

        email = self._storage.get_secret("PANDORA_EMAIL", scope="integration")
        password = self._storage.get_secret("PANDORA_PASSWORD", scope="integration")
        if not email or not password:
            raise ValueError(
                "Pandora credentials not configured. "
                "Set PANDORA_EMAIL and PANDORA_PASSWORD in your secrets."
            )

        service = PandoraService()
        service.login(email, password)
        _cached_service = service
        return service

    # -- Metadata --

    @property
    def command_name(self) -> str:
        return "pandora"

    @property
    def description(self) -> str:
        return (
            "Stream Pandora radio. Play stations, skip tracks, "
            "thumbs up or down, list stations, or create new ones."
        )

    @property
    def keywords(self) -> list[str]:
        return [
            "pandora",
            "radio",
            "station",
            "stream",
            "thumbs up",
            "thumbs down",
            "skip song",
            "play music",
            "next track",
            "now playing",
        ]

    @property
    def associated_service(self) -> str | None:
        return "Pandora"

    # -- Parameters --

    @property
    def parameters(self) -> list[JarvisParameter]:
        return [
            JarvisParameter(
                "action",
                "string",
                required=True,
                enum_values=[
                    "play",
                    "skip",
                    "stop",
                    "thumbs_up",
                    "thumbs_down",
                    "stations",
                    "now_playing",
                ],
                description="The action to perform",
            ),
            JarvisParameter(
                "query",
                "string",
                required=False,
                description=(
                    "What to play. Matches an existing station by name, or "
                    "creates and plays a new station from an artist/song/genre "
                    "if no match is found."
                ),
            ),
        ]

    # -- Secrets --

    @property
    def required_secrets(self) -> list[IJarvisSecret]:
        return [
            JarvisSecret(
                "PANDORA_EMAIL",
                "Pandora account email address",
                "integration",
                "string",
                is_sensitive=False,
                required=True,
                friendly_name="Pandora Email",
            ),
            JarvisSecret(
                "PANDORA_PASSWORD",
                "Pandora account password",
                "integration",
                "string",
                is_sensitive=True,
                required=True,
                friendly_name="Pandora Password",
            ),
        ]

    @property
    def required_packages(self) -> list[JarvisPackage]:
        return [JarvisPackage("pydora", ">=2.1.0")]

    # -- Rules --

    @property
    def rules(self) -> list[str]:
        return [
            "If user says 'like this song' or 'love this', use action='thumbs_up'",
            "If user says 'don't like this' or 'dislike', use action='thumbs_down'",
            "If user says 'next' or 'next song', use action='skip'",
            "If user says 'what's playing' or 'what song is this', use action='now_playing'",
            "If user says 'play pandora' without a station name, use action='play' with no query",
            "If user says 'play [X]', 'play my [X] station', 'play [X] radio', or "
            "'put on [X]', use action='play' with query='[X]'. The query may be a "
            "station name, an artist, a song, or a genre — play handles all of them.",
        ]

    @property
    def critical_rules(self) -> list[str]:
        return [
            "Use 'thumbs_down' not 'skip' when user expresses dislike for a song.",
            "Always use action='play' when the user wants to hear something — never "
            "ask the user to choose between an existing station and a new one.",
        ]

    # -- Pre-routing --

    def pre_route(self, voice_command: str) -> PreRouteResult | None:
        text = voice_command.lower().strip().rstrip(".!?")

        # Explicit phrases — always claim them, even when Pandora isn't
        # the active player. "stop pandora" should stop Pandora no matter
        # what else is playing.
        explicit_map: dict[str, dict[str, str]] = {
            "stop pandora": {"action": "stop"},
            "play pandora": {"action": "play"},
            "my stations": {"action": "stations"},
            "list stations": {"action": "stations"},
        }
        if text in explicit_map:
            return PreRouteResult(arguments=explicit_map[text])

        # Ambiguous phrases — "stop", "skip", "next", "thumbs up", etc.
        # could mean Pandora OR Spotify (or another music service). Only
        # claim them when Pandora is the currently-active player.
        # Otherwise return None so the next command's pre_route (or the
        # LLM) can handle. This prevents Pandora from swallowing a "stop"
        # when Spotify is actually playing.
        ambiguous_map: dict[str, dict[str, str]] = {
            "skip": {"action": "skip"},
            "next": {"action": "skip"},
            "next song": {"action": "skip"},
            "skip song": {"action": "skip"},
            "stop": {"action": "stop"},
            "thumbs up": {"action": "thumbs_up"},
            "thumbs down": {"action": "thumbs_down"},
            "like this song": {"action": "thumbs_up"},
            "i like this": {"action": "thumbs_up"},
            "i don't like this": {"action": "thumbs_down"},
            "what's playing": {"action": "now_playing"},
            "what song is this": {"action": "now_playing"},
            "now playing": {"action": "now_playing"},
        }
        if text in ambiguous_map:
            if _cached_service is not None and _cached_service.is_playing:
                return PreRouteResult(arguments=ambiguous_map[text])
            return None

        # "play X", "play my X", "play X station", "play X radio",
        # "play X on Pandora", "put on X", "listen to X", "start X" — extract X.
        # Trailing "station|radio" and "on pandora" are phrasing, not part of the name.
        m = re.match(
            r"^(?:play|put on|listen to|start)\s+"
            r"(?:my\s+)?(?:pandora\s+)?"
            r"(.+?)"
            r"(?:\s+(?:station|radio))?"
            r"(?:\s+on\s+pandora)?$",
            text,
        )
        if m:
            query = m.group(1).strip()
            # Filter out generic words that don't identify a station.
            if query in ("", "pandora", "music", "radio", "something"):
                return PreRouteResult(arguments={"action": "play"})
            return PreRouteResult(arguments={"action": "play", "query": query})

        return None

    # -- Post-processing --

    def post_process_tool_call(
        self, args: dict[str, Any], voice_command: str
    ) -> dict[str, Any]:
        if args.get("action") == "play" and not args.get("query"):
            stripped = re.sub(
                r"^(?:play|put on|listen to|start)\s+(?:my\s+)?(?:pandora\s+)?",
                "",
                voice_command,
                flags=re.IGNORECASE,
            ).strip().rstrip(".!?")
            # Strip trailing "station" or "radio" — those are phrasing, not part of the name
            stripped = re.sub(r"\s+(?:station|radio)$", "", stripped, flags=re.IGNORECASE)
            # Only set query if we extracted something meaningful
            if stripped and stripped.lower() not in ("pandora", "radio", "music", ""):
                args["query"] = stripped
        return args

    # -- Examples --

    def generate_prompt_examples(self) -> list[CommandExample]:
        return [
            CommandExample(
                voice_command="Play my Pandora",
                expected_parameters={"action": "play"},
                is_primary=True,
            ),
            CommandExample(
                voice_command="Play my jazz station on Pandora",
                expected_parameters={"action": "play", "query": "jazz"},
            ),
            CommandExample(
                voice_command="Play summer hits of the 90s radio",
                expected_parameters={"action": "play", "query": "summer hits of the 90s"},
            ),
            CommandExample(
                voice_command="Put on some Radiohead",
                expected_parameters={"action": "play", "query": "Radiohead"},
            ),
            CommandExample(
                voice_command="Skip this song",
                expected_parameters={"action": "skip"},
            ),
            CommandExample(
                voice_command="Thumbs up",
                expected_parameters={"action": "thumbs_up"},
            ),
            CommandExample(
                voice_command="I don't like this song",
                expected_parameters={"action": "thumbs_down"},
            ),
            CommandExample(
                voice_command="What stations do I have on Pandora",
                expected_parameters={"action": "stations"},
            ),
            CommandExample(
                voice_command="What's playing right now",
                expected_parameters={"action": "now_playing"},
            ),
        ]

    def generate_adapter_examples(self) -> list[CommandExample]:
        items: list[tuple[str, dict[str, str]]] = [
            ("Play my Pandora", {"action": "play"}),
            ("Play Pandora", {"action": "play"}),
            ("Start Pandora", {"action": "play"}),
            ("Put on Pandora", {"action": "play"}),
            ("Play my jazz station", {"action": "play", "query": "jazz"}),
            ("Play the rock station on Pandora", {"action": "play", "query": "rock"}),
            ("Play my Today's Hits station", {"action": "play", "query": "Today's Hits"}),
            ("Listen to my chill station", {"action": "play", "query": "chill"}),
            ("Put on my 90s station", {"action": "play", "query": "90s"}),
            ("Play my classical station", {"action": "play", "query": "classical"}),
            ("Play some country on Pandora", {"action": "play", "query": "country"}),
            ("Play summer hits of the 90s radio", {"action": "play", "query": "summer hits of the 90s"}),
            ("Play the Beatles radio", {"action": "play", "query": "the Beatles"}),
            ("Put on Tom Petty", {"action": "play", "query": "Tom Petty"}),
            ("Play some Taylor Swift", {"action": "play", "query": "Taylor Swift"}),
            ("Listen to Radiohead", {"action": "play", "query": "Radiohead"}),
            ("Play electronic music", {"action": "play", "query": "electronic"}),
            ("Skip", {"action": "skip"}),
            ("Skip this song", {"action": "skip"}),
            ("Next song", {"action": "skip"}),
            ("Next", {"action": "skip"}),
            ("Play the next one", {"action": "skip"}),
            ("Thumbs up", {"action": "thumbs_up"}),
            ("I like this song", {"action": "thumbs_up"}),
            ("Love this", {"action": "thumbs_up"}),
            ("This song is great", {"action": "thumbs_up"}),
            ("Thumbs down", {"action": "thumbs_down"}),
            ("I don't like this", {"action": "thumbs_down"}),
            ("Dislike", {"action": "thumbs_down"}),
            ("I hate this song", {"action": "thumbs_down"}),
            ("Stop Pandora", {"action": "stop"}),
            ("Stop the music", {"action": "stop"}),
            ("Turn off Pandora", {"action": "stop"}),
            ("What's playing", {"action": "now_playing"}),
            ("What song is this", {"action": "now_playing"}),
            ("Who sings this", {"action": "now_playing"}),
            ("What am I listening to", {"action": "now_playing"}),
            ("What are my stations", {"action": "stations"}),
            ("List my Pandora stations", {"action": "stations"}),
            ("Show my stations", {"action": "stations"}),
        ]
        return [
            CommandExample(
                voice_command=vc,
                expected_parameters=params,
                is_primary=(i == 0),
            )
            for i, (vc, params) in enumerate(items)
        ]

    # -- Execution --

    def run(self, request_info: RequestInformation, **kwargs: Any) -> CommandResponse:
        action: str | None = kwargs.get("action")
        query: str | None = kwargs.get("query")

        if not action:
            return CommandResponse.error_response(
                error_details="What would you like to do with Pandora?",
                context_data={"error": "missing_action"},
            )

        try:
            service = self._get_service()
        except ValueError as e:
            return CommandResponse.error_response(
                error_details=str(e),
                context_data={"error": "not_configured"},
            )
        except Exception as e:
            logger.error("Pandora login failed", error=str(e))
            return CommandResponse.error_response(
                error_details=f"Pandora login failed: {e}",
                context_data={"error": "login_failed"},
            )

        handler = {
            "play": self._handle_play,
            "skip": self._handle_skip,
            "stop": self._handle_stop,
            "thumbs_up": self._handle_thumbs_up,
            "thumbs_down": self._handle_thumbs_down,
            "stations": self._handle_stations,
            "now_playing": self._handle_now_playing,
        }.get(action)

        if not handler:
            return CommandResponse.error_response(
                error_details=f"Unknown action: {action}",
                context_data={"error": "unknown_action"},
            )

        return handler(service, query)

    def _handle_play(self, service: Any, query: str | None) -> CommandResponse:
        """Play a station, creating it from a search if no existing match.

        Resolution order:
          1. No query → resume current station / first station
          2. Query matches an existing station (exact or substring) → play it
          3. Query matches an artist/song via Pandora search → create + play
          4. No match anywhere → error
        """
        try:
            # Case 1: no query
            if not query:
                track = service.play_station(station_name=None)
                return self._play_response(track, created=False)

            # Case 2: query matches an existing station
            existing = service.find_station(query)
            if existing is not None:
                track = service.play_station(station_name=query)
                return self._play_response(track, created=False)

            # Case 3: fall through to search + create
            logger.info("No existing Pandora station matched, creating new", query=query)
            created = service.search_and_create_station(query)
            track = service.play_station(station_name=created["name"])
            return self._play_response(track, created=True)

        except ValueError as e:
            # search_and_create_station raises ValueError on no results
            return CommandResponse.error_response(
                error_details=(
                    f"I couldn't find a Pandora station or artist for '{query}'. "
                    "Try a different name."
                ),
                context_data={"error": "no_results", "query": query or "", "detail": str(e)},
            )
        except RuntimeError as e:
            return CommandResponse.error_response(
                error_details=str(e),
                context_data={"error": "playback_error"},
            )

    @staticmethod
    def _play_response(track: dict[str, str], *, created: bool) -> CommandResponse:
        prefix = "Created and now playing" if created else "Now playing"
        return CommandResponse.success_response(
            context_data={
                "action": "play",
                "created_station": created,
                "message": (
                    f"{prefix} {track['song']} by {track['artist']} "
                    f"on your {track['station']} station"
                ),
                **track,
            },
        )

    def _handle_skip(self, service: Any, _query: str | None) -> CommandResponse:
        try:
            track = service.skip()
            return CommandResponse.success_response(
                context_data={
                    "action": "skip",
                    "message": f"Skipped to {track['song']} by {track['artist']}",
                    **track,
                },
            )
        except RuntimeError as e:
            return CommandResponse.error_response(
                error_details=str(e),
                context_data={"error": "nothing_playing"},
            )

    def _handle_stop(self, service: Any, _query: str | None) -> CommandResponse:
        service.stop()
        return CommandResponse.success_response(
            context_data={"action": "stop", "message": "Pandora stopped"},
        )

    def _handle_thumbs_up(self, service: Any, _query: str | None) -> CommandResponse:
        try:
            track = service.thumbs_up()
            return CommandResponse.success_response(
                context_data={
                    "action": "thumbs_up",
                    "message": f"Liked {track['song']} by {track['artist']}",
                    **track,
                },
            )
        except RuntimeError as e:
            return CommandResponse.error_response(
                error_details=str(e),
                context_data={"error": "nothing_playing"},
            )

    def _handle_thumbs_down(self, service: Any, _query: str | None) -> CommandResponse:
        try:
            track = service.thumbs_down()
            return CommandResponse.success_response(
                context_data={
                    "action": "thumbs_down",
                    "message": f"Disliked that track. Now playing {track['song']} by {track['artist']}",
                    **track,
                },
            )
        except RuntimeError as e:
            return CommandResponse.error_response(
                error_details=str(e),
                context_data={"error": "nothing_playing"},
            )

    def _handle_stations(self, service: Any, _query: str | None) -> CommandResponse:
        stations = service.list_stations()
        if not stations:
            return CommandResponse.error_response(
                error_details="No stations found on your Pandora account",
                context_data={"error": "no_stations"},
            )
        names = [s["name"] for s in stations]
        return CommandResponse.success_response(
            context_data={
                "action": "stations",
                "message": f"You have {len(names)} stations: {', '.join(names)}",
                "stations": names,
            },
        )

    def _handle_now_playing(
        self, service: Any, _query: str | None
    ) -> CommandResponse:
        track = service.now_playing()
        if not track:
            return CommandResponse.error_response(
                error_details="Nothing is currently playing on Pandora",
                context_data={"error": "nothing_playing"},
            )
        return CommandResponse.success_response(
            context_data={
                "action": "now_playing",
                "message": (
                    f"Currently playing {track['song']} by {track['artist']} "
                    f"from the album {track['album']}"
                ),
                **track,
            },
        )

    # -- Init data --

    def init_data(self) -> dict[str, Any]:
        """Test Pandora connection during setup."""
        email = self._storage.get_secret("PANDORA_EMAIL", scope="integration")
        password = self._storage.get_secret("PANDORA_PASSWORD", scope="integration")

        if not email or not password:
            return {"status": "error", "message": "Pandora credentials not set"}

        try:
            service = self._get_service()
            stations = service.list_stations()
            return {
                "status": "success",
                "stations_found": len(stations),
                "stations": [s["name"] for s in stations[:10]],
                "message": f"Connected to Pandora with {len(stations)} station(s)",
            }
        except Exception as e:
            return {"status": "error", "message": f"Pandora login failed: {e}"}

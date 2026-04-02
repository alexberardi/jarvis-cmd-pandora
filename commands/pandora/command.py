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


class PandoraCommand(IJarvisCommand):
    """Stream Pandora radio stations with voice control."""

    def __init__(self) -> None:
        self._storage = JarvisStorage("pandora")
        self._service: Any = None

    def _get_service(self) -> Any:
        """Get or create an authenticated PandoraService."""
        if self._service is not None and self._service.is_logged_in:
            return self._service

        from pandora_shared.pandora_service import PandoraService

        email = self._storage.get_secret("PANDORA_EMAIL", scope="user")
        password = self._storage.get_secret("PANDORA_PASSWORD", scope="user")
        if not email or not password:
            raise ValueError(
                "Pandora credentials not configured. "
                "Set PANDORA_EMAIL and PANDORA_PASSWORD in your secrets."
            )

        service = PandoraService()
        service.login(email, password)
        self._service = service
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
                    "create_station",
                    "now_playing",
                ],
                description="The action to perform",
            ),
            JarvisParameter(
                "query",
                "string",
                required=False,
                description="Station name to play, or artist/song to create a station from",
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
            "If user says 'play my [X] station', use action='play' with query='[X]'",
            "If user says 'make a station for [X]' or 'create [X] station', use action='create_station'",
        ]

    @property
    def critical_rules(self) -> list[str]:
        return [
            "Use 'thumbs_down' not 'skip' when user expresses dislike for a song.",
            "Use 'play' not 'create_station' when referring to an existing station.",
        ]

    # -- Pre-routing --

    def pre_route(self, voice_command: str) -> PreRouteResult | None:
        text = voice_command.lower().strip()

        simple_map: dict[str, dict[str, str]] = {
            "skip": {"action": "skip"},
            "next": {"action": "skip"},
            "next song": {"action": "skip"},
            "skip song": {"action": "skip"},
            "stop": {"action": "stop"},
            "stop pandora": {"action": "stop"},
            "thumbs up": {"action": "thumbs_up"},
            "thumbs down": {"action": "thumbs_down"},
            "like this song": {"action": "thumbs_up"},
            "i like this": {"action": "thumbs_up"},
            "i don't like this": {"action": "thumbs_down"},
            "what's playing": {"action": "now_playing"},
            "what song is this": {"action": "now_playing"},
            "now playing": {"action": "now_playing"},
            "my stations": {"action": "stations"},
            "list stations": {"action": "stations"},
            "play pandora": {"action": "play"},
        }

        if text in simple_map:
            return PreRouteResult(arguments=simple_map[text])

        m = re.match(r"^play (?:my )?(.+?) station$", text)
        if m:
            return PreRouteResult(arguments={"action": "play", "query": m.group(1)})

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
            ).strip()
            # Only set query if we extracted something meaningful
            if stripped and stripped.lower() not in ("pandora", "radio", "music", ""):
                stripped = re.sub(r"\s+station$", "", stripped, flags=re.IGNORECASE)
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
                voice_command="Create a Radiohead station",
                expected_parameters={"action": "create_station", "query": "Radiohead"},
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
            ("Create a Beatles station", {"action": "create_station", "query": "Beatles"}),
            ("Make a station for Taylor Swift", {"action": "create_station", "query": "Taylor Swift"}),
            ("Start a new station for electronic music", {"action": "create_station", "query": "electronic music"}),
            ("Create a station based on Bohemian Rhapsody", {"action": "create_station", "query": "Bohemian Rhapsody"}),
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
            "create_station": self._handle_create_station,
            "now_playing": self._handle_now_playing,
        }.get(action)

        if not handler:
            return CommandResponse.error_response(
                error_details=f"Unknown action: {action}",
                context_data={"error": "unknown_action"},
            )

        return handler(service, query)

    def _handle_play(self, service: Any, query: str | None) -> CommandResponse:
        try:
            track = service.play_station(station_name=query)
            return CommandResponse.success_response(
                context_data={
                    "action": "play",
                    "message": (
                        f"Now playing {track['song']} by {track['artist']} "
                        f"on your {track['station']} station"
                    ),
                    **track,
                },
            )
        except ValueError as e:
            return CommandResponse.error_response(
                error_details=str(e),
                context_data={"error": "station_not_found", "query": query or ""},
            )
        except RuntimeError as e:
            return CommandResponse.error_response(
                error_details=str(e),
                context_data={"error": "playback_error"},
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

    def _handle_create_station(
        self, service: Any, query: str | None
    ) -> CommandResponse:
        if not query:
            return CommandResponse.error_response(
                error_details="What artist or song should I create a station from?",
                context_data={"error": "missing_query"},
            )
        try:
            result = service.search_and_create_station(query)
            return CommandResponse.success_response(
                context_data={
                    "action": "create_station",
                    "message": f"Created station '{result['name']}' based on {result['source']} match for '{query}'",
                    "station_name": result["name"],
                    "query": query,
                },
            )
        except ValueError as e:
            return CommandResponse.error_response(
                error_details=str(e),
                context_data={"error": "no_results", "query": query},
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
        email = self._storage.get_secret("PANDORA_EMAIL", scope="user")
        password = self._storage.get_secret("PANDORA_PASSWORD", scope="user")

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

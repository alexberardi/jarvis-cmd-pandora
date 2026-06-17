# jarvis-cmd-pandora

Pandora radio voice command for [Jarvis](https://github.com/alexberardi/jarvis).

## Features

- **Play anything** — "Play my jazz station", "Put on Tom Petty", "Play summer hits of the 90s radio". Matches an existing station by name, or creates a new one from the artist/song/genre when no match is found.
- **Skip tracks** — "Skip this song" / "Next"
- **Thumbs up/down** — "I like this song" / "Thumbs down"
- **List stations** — "What are my Pandora stations?"
- **Now playing** — "What song is this?"

## Requirements

- A Pandora account (free or premium)
- An audio player: `mpv` (recommended), `vlc`, or `ffplay`
- Python 3.10+

## Setup

1. Install the package via Jarvis Pantry or manually
2. Set your secrets:
   - `PANDORA_EMAIL` — your Pandora account email
   - `PANDORA_PASSWORD` — your Pandora account password
3. Ensure `mpv` is installed (`brew install mpv` / `apt install mpv`)

## Disclaimer

This is an **unofficial** Pandora integration. It is not affiliated with,
endorsed by, or supported by Pandora Media, LLC.

It authenticates against Pandora's unofficial JSON API using the well-known
Android partner credentials that are public knowledge and shared by all
unofficial Pandora clients (the same keys used by `pydora` and similar
projects). Those credentials are reverse-engineered from the official client
and are not issued to this project (see the in-code comment in
`pandora_shared/pandora_service.py`). **Using this integration may violate
Pandora's Terms of Service.** Use it at your own risk and only with an account
you own.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

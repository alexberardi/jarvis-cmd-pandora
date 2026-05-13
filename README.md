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

## License

MIT

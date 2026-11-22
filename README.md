# md2switch

Turns a Muse Dash custom chart (`.mdm`) into an [Atmosphère](https://github.com/Atmosphere-NX/Atmosphere)
LayeredFS mod that **replaces an existing song** in the Switch version of Muse Dash.

It is meant for people who run a modded Switch, own the game, and want to play a custom chart
that is normally only available on PC. It was tested with game version 6.7.0 (Title ID `01008E200C5C2000`).

The chart conversion is a Python port of the BMS loader from
[MDMods/CustomAlbums](https://github.com/MDMods/CustomAlbums) (MIT), so timing, bosses, scene
switches and effects should behave as they do with CustomAlbums on PC.

> This project was made with [Claude Code](https://claude.com/claude-code).
> It is shared in the hope that it helps someone else. It is a small personal tool, not a polished product.

## What it does

`build` takes your game's extracted RomFS, a `.mdm` file and the id of the song to overwrite, and writes
a mod folder containing patched copies of these files (the originals are never modified):

- the song's music bundle: charts converted from `map1..4.bms`, plus the full music track
- the song bundle (if the song has one): demo audio and cover image
- the album config bundles: title, artist, level designer, difficulty numbers, BPM, scene
  (all languages get the new title and artist)
- `catalog.json`: CRC checks for the changed bundles are disabled so the game accepts them

If the target song has difficulty slots your chart does not provide, those slots get the nearest
available map from your chart. `--only` limits the replacement to specific slots.

## Requirements

- Linux (setup uses `apt` for ffmpeg; other systems should work if `ffmpeg` is on your `PATH`)
- Python 3 and `python3-venv`
- `ffmpeg` (used to convert audio)
- A Switch with Atmosphère and the game installed
- The game's RomFS extracted to a folder on your computer (the folder that contains `Data/`).
  This tool does not extract it for you, and you must dump it from your own copy of the game.
- A Muse Dash custom chart as `.mdm`

## Install

```bash
./setup.sh
```

This installs `ffmpeg` via `apt` if missing, creates a virtualenv in `.venv`, and installs
`UnityPy` (pinned to 1.25.4), `numpy` and `pillow`. Use the `./md2switch` wrapper afterwards; it runs
the script with the virtualenv's Python, so you do not need to activate anything.

## Usage

Find the id of the song you want to overwrite (searches id, title and artist):

```bash
./md2switch search ~/muserom bamboo
```

Build the mod. All difficulties of the target song get your chart:

```bash
./md2switch build ~/muserom MyChart.mdm bamboo -o ~/my_mod
```

Overwrite only one difficulty slot:

```bash
./md2switch build ~/muserom MyChart.mdm bamboo --only 2
```

Without `-o`, the output goes to `./<song_id>_mod`. When it finishes, copy the `atmosphere` folder
from the output to the root of the SD card (merge with the existing folder).

## The `.mdm` format

An `.mdm` is a zip file containing `info.json`, `map1.bms` to `map4.bms` (at least one),
a `music.(ogg|wav|mp3|flac)`, and optionally `demo.*` and `cover.(png|jpg|gif)`.
If there is no demo, the first 20 seconds of the music are used.

## Folders

- `lib/` – the code the main script imports:
  - `bms2stage.py`: BMS to Muse Dash note data (the CustomAlbums port)
  - `odin_stage.py`: writes the game's binary stage format
  - `fsb5pcm.py`: wraps raw PCM audio in an FSB5 container
  - `catalog.py`: reads and edits the Addressables `catalog.json`
- `data/` – values extracted from the game that the converter needs:
  `prefab_data.json` (enemy start delays and boss data) and `boss_durations.json` (boss animation lengths).
- `.venv/` – created by `setup.sh`; not part of the project.

## Known limits

- `#SCROLL` speed changes in charts are ignored (same as CustomAlbums).
- Audio is stored as uncompressed PCM, so the mod gets large (roughly 30 MB for a 3-minute song).
- Only one existing song is replaced per build; it cannot add new songs.
- Tested only with game version 6.7.0. After a game update, re-extract the RomFS and rebuild;
  the files in `data/` may also need to be regenerated, and nothing here does that for you.
- Tested on Linux only.
- No automated tests. Check the result on your own console and keep a backup of your SD card.
- Use at your own risk. This is not affiliated with the game's developers.

## License

MIT, see [LICENSE](LICENSE). The chart conversion in `lib/bms2stage.py` is a port of
[MDMods/CustomAlbums](https://github.com/MDMods/CustomAlbums), which is also MIT-licensed.

# Music Sync Checker

Desktop utility to synchronize music libraries between a master folder (HDD/SSD) and portable storage devices (MP3 players, DAPs, SD cards).

Instead of relying solely on filenames, tracks are matched primarily by metadata (title, artist, and duration via `mutagen`). This detects files that have been renamed, tracks that only exist on the device, and tracks missing from the master library.

## Features

- Metadata-first matching (ID3, Vorbis, MP4, FLAC tags), with fallback to file size and normalized filenames.
- Multi-device support (e.g., dual SD card slots).
- Scan categorization:
  - In sync: Identical metadata and filenames.
  - Renamed: Same track with different filenames between storage locations.
  - Not in master: Tracks present on the device but missing from the master archive.
  - Needs review: Ambiguous matches requiring user decision (decisions are remembered).
- Master-to-device transfer panel with available storage checking.
- Non-destructive operations: file copies write to temporary `.part` files first, filename collisions append numeric suffixes, and matching `.lrc` lyric files are copied alongside audio.

## Requirements

- Windows 10 or later
- Python 3.9+
- Dependencies: `mutagen >= 1.47` (and `pyinstaller` if compiling the executable)

## Installation

### Clone the Repository

```bash
git clone https://github.com/koutasatouu/music-sync-checker.git
cd music-sync-checker
```

### Setup Environment

1. Create and activate a virtual environment (optional):
   ```cmd
   python -m venv venv
   venv\Scripts\activate
   ```

2. Install dependencies:
   ```cmd
   pip install -r requirements.txt
   ```

## Running the Application

### Option 1: Run from Source

Launch directly using Python:
```cmd
python "Music Sync Checker.pyw"
```
Or double-click `run_from_source.bat` on Windows.

### Option 2: Build Standalone Executable (.exe)

Run the automated build script:
```cmd
build.bat
```

This will:
1. Verify and install build requirements (`pyinstaller`, `mutagen`).
2. Run the test suite.
3. Compile the standalone binary to `dist\MusicSyncChecker\MusicSyncChecker.exe`.
4. Generate Desktop and Start Menu shortcuts.

## Usage

1. Select the **Master folder** containing your main music archive.
2. Add one or more **Device folders** pointing to your portable player or SD card volumes.
3. Click **Check Now** to run comparison.
4. Review the results:
   - Synchronize renamed files or copy missing tracks from device to master.
   - Use the **Master -> device** tab to copy selected albums or tracks to the player.
5. Review pending operations in the confirmation dialog before applying changes.

## Running Tests

Execute the unit test suite:

```cmd
python -m unittest discover -s tests
```

## Configuration & Logs

App configuration, decisions, and metadata caches are stored at:
```
%APPDATA%\MusicSyncChecker
```
Error details and crash logs are written to `crash.log` in that directory.

# VCD – Vadana Class Downloader (v0.1)

**Automatically download & merge Adobe Connect recordings into a single synced MP4.**

---

## ⚠️ Version 0.1 – Important Limitations
- **Audio + Screenshare only** – shared files (PDF, PPTX, WhiteBoard, etc.) are **not** processed.
- No support for webcam video streams.
- Expect breaking changes in future releases.

---

## Requirements
- **Python 3.8 or newer**
- **FFmpeg** (with `ffmpeg` and `ffprobe` available in your system PATH)

---

## Installation

1. Clone the repository or download `vcd.py`.
   ```bash
   git clone https://github.com/IAUCourseExp/VCD
   cd VCD
   ```
3. Install the required Python packages:

   ```bash
   pip install -r requirements.txt
   ```



4. Verify FFmpeg is installed:

   ```bash
   ffmpeg -version
   ffprobe -version
   ```

   If the commands are not recognised, install FFmpeg from [ffmpeg.org](https://ffmpeg.org/download.html) and **ensure the executables are in your PATH.**

---

## Usage

Run the script from your terminal:

```bash
python VCD.py
```

You will be prompted to enter the meeting URL:

```
Enter meeting URL: (e.g. https://vadavc32.ec.iau.ir/l2e72tear3ee)
```

The script will:
- Download the recording ZIP from the Adobe Connect(Vadana) server.
- Extract all FLV and XML files into a new folder named after the recording ID.
- Align all streams using internal pacing ticks.
- Generate a detailed `timeline.xml`.
- Merge everything into a single MP4 file: `Class-<recording_id>.mp4`

If you run the script again with the same URL, it will detect the existing folder and skip the download.

---

## How It Works (briefly)
1. **Download** – fetches the ZIP from `https://<server>/<recording_id>/output/<recording_id>.zip`.
2. **Tick extraction** – reads pacingTick timestamps from XML files to build a common timeline.
3. **Segment creation** – determines when each screenshare video and audio file is active.
4. **Timeline XML** – writes a unified timeline with exact offsets.
5. **Rendering** – uses FFmpeg to overlay video segments on a black canvas (no stale frames) and mix all audio pieces with proper delays and trimming.

---

## Output
- The final MP4 is placed in the folder where you ran the script, named `Class-<recording_id>.mp4`.
- The raw extracted files remain in a folder named after the recording ID (e.g., `l2e72tear3ee/`). You can delete this folder after a successful run.

---

## Troubleshooting

### `Download failed with HTTP 403/404`
The server may require authentication. Download the ZIP manually via your browser, extract it into a folder named exactly like the recording ID, and run the script again. It will skip the download.

### `ffmpeg / ffprobe not found`
Install FFmpeg and ensure its `bin` folder is in your system PATH. You can also place `ffmpeg.exe` and `ffprobe.exe` next to `vcd.py`.

### Script hangs at 95 % during rendering
This can happen with very long recordings. It will eventually finish; give it extra time. A future update will optimise the audio mixing.

### No video, only audio
Check that the source recording actually contains a screenshare. The script only processes `screenshare*.flv` files with a video track.

---

## Contributing
Pull requests and issues are welcome. Please keep in mind this is an early beta – report any bugs with the full console output.

---

## License
MIT

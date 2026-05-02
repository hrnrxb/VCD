import subprocess
import shutil
import json
import math
import time
import sys, os
from pathlib import Path
import xml.etree.ElementTree as ET
from xml.dom import minidom
from datetime import datetime
import requests
import zipfile
from urllib.parse import urlparse, urljoin
import urllib3
from tqdm import tqdm
from colorama import init, Fore, Style
init(autoreset=True)   

try:
    from pyfiglet import Figlet
except ImportError:
    Figlet = None

# Ignore insecure HTTPS warnings (common on internal servers)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def find_tool(tool_name):
    """
    Return the path to a tool (ffmpeg/ffprobe).
    If bundled by PyInstaller, use the copy in the temp folder;
    otherwise fall back to the system PATH.
    """
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.abspath(".")
    bundled_path = os.path.join(base_path, tool_name)
    if os.path.exists(bundled_path):
        return bundled_path
    # Not bundled, rely on PATH
    system_path = shutil.which(tool_name)
    if system_path:
        return system_path
    return None

def download_and_extract_zip(meeting_url, target_dir="class-files"):
    """
    Build the ZIP download link from a meeting URL, download it,
    and extract its content into target_dir.
    No authentication is attempted – if the server requires cookies,
    the download will fail and fall back to manual instructions.
    """
    parsed = urlparse(meeting_url)
    path = parsed.path.rstrip("/")
    recording_id = path.split("/")[-1] if path else ""
    if not recording_id:
        raise ValueError("Could not extract recording ID from the URL.")

    base_url = f"{parsed.scheme}://{parsed.netloc}"
    zip_url = f"{base_url}/{recording_id}/output/{recording_id}.zip?download=zip"

    log(f"Attempting download from: {zip_url}")

    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(zip_url, headers=headers, verify=False, stream=True)

    if resp.status_code != 200:
        log(f"Download failed with HTTP {resp.status_code}", "ERROR")
        log(
            "You may need to authenticate manually. "
            "Place the ZIP file in 'class-files' and extract it yourself.",
            "INFO",
        )
        return None

    file_size = int(resp.headers.get("content-length", 0))
    zip_path = Path(f"{recording_id}.zip")
    with open(zip_path, "wb") as f:
        with tqdm(
            total=file_size,
            unit="B",
            unit_scale=True,
            desc="Downloading ZIP",
            colour="#00ff00",
        ) as pbar:
            for chunk in resp.iter_content(8192):
                f.write(chunk)
                pbar.update(len(chunk))

    # Extract the archive
    extract_dir = Path(target_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as archive:
        archive.extractall(extract_dir)

    log(f"Files extracted to '{target_dir}'.")
    zip_path.unlink()  # delete temporary ZIP
    return extract_dir


# ------------------------------------------------------------
# 0. Logging & tools
# ------------------------------------------------------------
def log(msg, level="INFO"):
    now = datetime.now().strftime("%H:%M:%S")
    colors = {
        "INFO": Fore.GREEN,
        "WARN": Fore.YELLOW,
        "ERROR": Fore.RED,
        "SUCCESS": Fore.CYAN,
        "STEP": Fore.MAGENTA,
    }
    color = colors.get(level, Fore.WHITE)
    prefix = f"[{now}]"
    print(f"{Style.DIM}{prefix}{Style.RESET_ALL} {color}{level:7s}{Style.RESET_ALL} {msg}", flush=True)


def check_ffmpeg():
    for tool in ["ffmpeg", "ffprobe"]:
        if shutil.which(tool) is None:
            log(f"{tool} not found in PATH. Please install FFmpeg and add it to your PATH, or place ffmpeg.exe/ffprobe.exe next to this executable.", "ERROR")
            sys.exit(1)


def execute_ffmpeg(cmd_parts, description="FFmpeg", duration_sec=None):
    """
    Run ffmpeg with a progress bar. duration_sec is used to scale the bar;
    if unknown, the bar simply reflects the processed data.
    """
    log("Launching FFmpeg …")
    full_cmd = (
        cmd_parts[:1]
        + ["-progress", "pipe:1", "-nostats", "-loglevel", "quiet"]
        + cmd_parts[1:]
    )
    proc = subprocess.Popen(
        full_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    pbar = None
    if duration_sec and duration_sec > 0:
        pbar = tqdm(
            total=duration_sec * 1_000_000,
            unit="us",
            desc=description,
            colour="cyan",
            smoothing=0.01,
        )

    last_us = 0
    for line in proc.stdout:
        if "out_time_ms=" in line:
            try:
                current_us = int(line.strip().split("=")[1])
                if pbar:
                    pbar.update(current_us - last_us)
                    last_us = current_us
            except (ValueError, IndexError):
                pass
        elif "progress=end" in line:
            break

    proc.wait()
    if pbar:
        pbar.close()
    if proc.returncode != 0:
        log("FFmpeg command failed.", "ERROR")
        raise RuntimeError(f"ffmpeg exited with code {proc.returncode}")
    log("FFmpeg finished successfully.")


# ------------------------------------------------------------
# 1. Extracting timing info from XML & building playable intervals
# ------------------------------------------------------------
def contains_stream(file_path, stream_kind):
    """Return True if file contains at least one stream of type stream_kind (video/audio)."""
    ffprobe_path = find_tool("ffprobe.exe")
    if not ffprobe_path:
        log("ffprobe.exe not found.", "ERROR")
        sys.exit(1)
    
    cmd = [ffprobe_path, "-v", "quiet", "-print_format", "json", "-show_streams", str(file_path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return False
    try:
        info = json.loads(result.stdout)
        for stream in info.get("streams", []):
            if stream.get("codec_type") == stream_kind:
                return True
    except (json.JSONDecodeError, KeyError):
        pass
    return False


def probe_duration(file_path):
    """Return media duration in seconds using ffprobe."""
    ffprobe_path = find_tool("ffprobe.exe")
    if not ffprobe_path:
        log("ffprobe.exe not found.", "ERROR")
        sys.exit(1)
    
    cmd = [ffprobe_path, "-v", "quiet", "-print_format", "json", "-show_streams", str(file_path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return 0.0
    try:
        info = json.loads(result.stdout)
        return float(info["format"]["duration"])
    except (json.JSONDecodeError, KeyError, ValueError):
        return 0.0


def find_base_tick_from_xml(xml_path):
    """
    Scan the XML for pacingTick messages, pick the one with the smallest
    'time' value, and compute the base tick (tick number minus offset).
    This gives a reliable zero point for all streams.
    """
    try:
        tree = ET.parse(str(xml_path))
        root = tree.getroot()
    except ET.ParseError:
        return None

    best_base = None
    earliest_time = float("inf")

    for elem in root.findall(".//Message"):
        method = elem.find("Method")
        if method is None or not method.text or "pacingTick" not in method.text:
            continue
        time_str = elem.get("time")
        number = elem.find("Number")
        if time_str is None or number is None or not number.text:
            continue
        try:
            offset = int(time_str.strip())
            tick = int(number.text.strip())
            if offset < 0:  # ignore negative offsets (shouldn't happen)
                continue
        except ValueError:
            continue

        if offset < earliest_time:
            earliest_time = offset
            best_base = tick - offset

    return best_base


def collect_media_intervals(media_folder):
    """
    Go through all FLV/XML pairs in the folder, determine the base tick,
    and build two lists:
      - screen_video_segments : screenshare files that actually contain video
      - audio_segments         : files that contain an audio track
    Each entry is a dict with the file path, start_ms, end_ms, duration_ms.
    """
    folder = Path(media_folder)
    xml_files = list(folder.glob("*.xml"))
    xml_bases = {}
    log("Extracting base tick from XML files (smallest time heuristic) …")
    for xml_path in xml_files:
        base = find_base_tick_from_xml(xml_path)
        if base is not None:
            xml_bases[xml_path.stem] = base

    if not xml_bases:
        return [], [], None

    global_base = min(xml_bases.values())
    log(f"Earliest base tick: {global_base}")

    flv_files = list(folder.glob("*.flv"))
    screen_video_segments = []
    audio_segments = []

    for flv in flv_files:
        stem = flv.stem
        if stem not in xml_bases:
            log(f"  ⚠ {flv.name} – no valid pacingTick", "WARN")
            continue
        has_video = contains_stream(flv, "video")
        has_audio = contains_stream(flv, "audio")
        if not has_video and not has_audio:
            log(f"  ⚠ {flv.name} – empty stream", "WARN")
            continue
        dur_sec = probe_duration(flv)
        if dur_sec <= 0:
            log(f"  ⚠ {flv.name} – duration zero", "WARN")
            continue

        local_base = xml_bases[stem]
        start_ms = local_base - global_base
        if start_ms < 0:
            log(f"  ⚠ {flv.name} negative start ({start_ms} ms), clamped to 0", "WARN")
            start_ms = 0
        duration_ms = dur_sec * 1000
        end_ms = start_ms + duration_ms

        entry = {
            "file": flv,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "duration_ms": duration_ms,
        }

        # Only screenshare files that have video
        if has_video and flv.name.startswith("screenshare"):
            screen_video_segments.append(entry)
        # All files that carry audio (needed for mixing)
        if has_audio:
            audio_segments.append(entry)

        log(
            f"   {flv.name}: start={start_ms/1000:.1f}s, end={end_ms/1000:.1f}s"
        )

    return screen_video_segments, audio_segments, global_base


# ------------------------------------------------------------
# 2. Building timeline.xml
# ------------------------------------------------------------
def build_continuous_segments(clips, total_ms):
    """
    Given a list of {start_ms, end_ms, file} and a total length,
    compute an ordered list of non‑overlapping, continuous segments.
    Segments are glued together where the same file covers consecutive ranges.
    """
    if not clips:
        return [{"start": 0, "end": total_ms, "file": None}]

    breakpoints = sorted({0, total_ms} | {c["start_ms"] for c in clips} | {c["end_ms"] for c in clips})
    segments = []

    for i in range(len(breakpoints) - 1):
        seg_start = breakpoints[i]
        seg_end = breakpoints[i + 1]
        if seg_end <= seg_start:
            continue

        # Which source covers this whole sub‑interval?
        covering = [c for c in clips if c["start_ms"] <= seg_start and c["end_ms"] >= seg_end]
        chosen_file = None
        if covering:
            # pick the latest start among candidates
            chosen = max(covering, key=lambda x: x["start_ms"])
            chosen_file = chosen["file"]

        if segments and segments[-1]["file"] == chosen_file:
            segments[-1]["end"] = seg_end
        else:
            segments.append({"start": seg_start, "end": seg_end, "file": chosen_file})

    return segments


def build_audio_mix_segments(audio_clips, total_ms):
    """
    Similar to build_continuous_segments, but each segment stores a list
    of all audio clips that are active during that slice.
    """
    if not audio_clips:
        return [{"start": 0, "end": total_ms, "files": []}]

    breakpoints = sorted({0, total_ms} | {c["start_ms"] for c in audio_clips} | {c["end_ms"] for c in audio_clips})
    segments = []

    for i in range(len(breakpoints) - 1):
        seg_start = breakpoints[i]
        seg_end = breakpoints[i + 1]
        if seg_end <= seg_start:
            continue

        active = [c for c in audio_clips if c["start_ms"] <= seg_start and c["end_ms"] >= seg_end]

        if segments and segments[-1]["files"] == active:
            segments[-1]["end"] = seg_end
        else:
            segments.append({"start": seg_start, "end": seg_end, "files": active})

    return segments


def write_timeline_xml(folder, screen_clips, audio_clips, total_ms, out_path):
    """
    Build a timeline.xml that unifies video and audio segments.
    Each unified segment carries one video source and zero or more audio sources.
    """
    video_segs = build_continuous_segments(screen_clips, total_ms)
    audio_segs = build_audio_mix_segments(audio_clips, total_ms)

    # Collect all segment boundaries
    all_times = set()
    for seg in video_segs:
        all_times.add(seg["start"])
        all_times.add(seg["end"])
    for seg in audio_segs:
        all_times.add(seg["start"])
        all_times.add(seg["end"])
    all_times = sorted(all_times)

    def video_at(segs, t):
        for seg in segs:
            if seg["start"] <= t < seg["end"]:
                return seg["file"]
        return None

    def audio_list_at(segs, t):
        for seg in segs:
            if seg["start"] <= t < seg["end"]:
                return seg["files"]
        return []

    unified = []
    for i in range(len(all_times) - 1):
        t0 = all_times[i]
        t1 = all_times[i + 1]
        if t1 <= t0:
            continue
        mid = (t0 + t1) / 2.0
        vid_file = video_at(video_segs, mid)
        aud_files = audio_list_at(audio_segs, mid)
        unified.append((t0, t1, vid_file, aud_files))

    # Build XML tree
    root = ET.Element("timeline")
    dur_elem = ET.SubElement(root, "total_duration_ms")
    dur_elem.text = str(int(total_ms))
    segments_elem = ET.SubElement(root, "segments")

    # Pre‑compute each file's earliest appearance to derive offsets
    file_start_map = {}
    for clip in screen_clips + audio_clips:
        file_start_map[clip["file"]] = clip["start_ms"]

    for t0, t1, vid_file, aud_list in unified:
        seg = ET.SubElement(segments_elem, "segment", start=str(int(t0)), end=str(int(t1)))
        seg_dur_ms = t1 - t0

        # Video entry
        if vid_file is None:
            ET.SubElement(seg, "video", file="black")
        else:
            offset_s = (t0 - file_start_map[vid_file]) / 1000.0
            ET.SubElement(
                seg,
                "video",
                file=vid_file.name,
                offset=str(round(offset_s, 3)),
                dur=str(round(seg_dur_ms / 1000.0, 3)),
            )

        # Audio entries
        if not aud_list:
            ET.SubElement(seg, "audio", file="silence")
        else:
            for aud_clip in aud_list:
                aud_file = aud_clip["file"]
                offset_s = (t0 - file_start_map[aud_file]) / 1000.0
                ET.SubElement(
                    seg,
                    "audio",
                    file=aud_file.name,
                    offset=str(round(offset_s, 3)),
                    dur=str(round(seg_dur_ms / 1000.0, 3)),
                )

    # Pretty‑print XML
    raw = ET.tostring(root, encoding="utf-8")
    dom = minidom.parseString(raw)
    pretty = dom.toprettyxml(indent="  ", encoding="utf-8")
    with open(out_path, "wb") as f:
        f.write(pretty)
    log(f"✅ timeline.xml saved to {out_path}")


# ------------------------------------------------------------
# 3. Reading timeline.xml and rendering the final video
# ------------------------------------------------------------
def read_timeline_xml(timeline_path):
    """
    Parse timeline.xml and return:
      - video_plan: list of dicts with start_ms, end_ms, file, offset, dur
      - audio_meta: dict mapping filename -> {first_start_ms, first_offset_ms, active_dur_ms, latest_end_ms}
      - total_dur_ms
    """
    tree = ET.parse(timeline_path)
    root = tree.getroot()
    total_ms = int(root.find("total_duration_ms").text)

    video_plan = []
    audio_meta = {}

    for seg_elem in root.findall(".//segment"):
        seg_start = int(seg_elem.get("start"))
        seg_end = int(seg_elem.get("end"))
        vid_elem = seg_elem.find("video")
        vid_file = vid_elem.get("file")
        if vid_file and vid_file != "black":
            vid_offset = float(vid_elem.get("offset", 0))
            vid_dur = float(vid_elem.get("dur", (seg_end - seg_start) / 1000.0))
        else:
            vid_offset = None
            vid_dur = (seg_end - seg_start) / 1000.0

        video_plan.append(
            {
                "start_ms": seg_start,
                "end_ms": seg_end,
                "file": vid_file,
                "offset": vid_offset,
                "dur": vid_dur,
            }
        )

        for audio_elem in seg_elem.findall("audio"):
            af = audio_elem.get("file")
            if af == "silence":
                continue
            seg_dur = float(audio_elem.get("dur", (seg_end - seg_start) / 1000.0)) * 1000
            offset = float(audio_elem.get("offset", 0)) * 1000

            if af not in audio_meta:
                audio_meta[af] = {
                    "first_start_ms": seg_start,
                    "first_offset_ms": offset,
                    "active_dur_ms": seg_dur,
                    "latest_end_ms": seg_start + seg_dur,
                }
            else:
                info = audio_meta[af]
                if seg_start < info["first_start_ms"]:
                    info["first_start_ms"] = seg_start
                    info["first_offset_ms"] = offset
                info["latest_end_ms"] = max(info["latest_end_ms"], seg_start + seg_dur)
                info["active_dur_ms"] = info["latest_end_ms"] - info["first_start_ms"]

    return video_plan, audio_meta, total_ms


def render_video_from_timeline(
    media_folder, timeline_path, output_video, canvas_w=1280, canvas_h=720, fps=30
):
    """
    Produce the final synced MP4 by reading timeline.xml and invoking ffmpeg
    with appropriate PTS offsets and an audio mixer.
    """
    video_plan, audio_meta, total_ms = read_timeline_xml(timeline_path)
    total_sec = total_ms / 1000.0
    log(f"Total duration from XML: {total_sec:.1f} s")

    folder = Path(media_folder)

    # -- Re‑derive continuous clips for ffmpeg (same file may appear in multiple segments)
    processed_vid = set()
    derived_video_clips = []
    for seg in video_plan:
        fname = seg["file"]
        if fname == "black" or fname in processed_vid:
            continue
        segs_of_file = [s for s in video_plan if s["file"] == fname]
        first = min(s["start_ms"] for s in segs_of_file)
        last = max(s["end_ms"] for s in segs_of_file)
        derived_video_clips.append(
            {
                "file": folder / fname,
                "start_ms": first,
                "end_ms": last,
                "duration_ms": last - first,
            }
        )
        processed_vid.add(fname)

    derived_audio_clips = []
    for fname, info in audio_meta.items():
        derived_audio_clips.append(
            {
                "file": folder / fname,
                "start_ms": info["first_start_ms"],
                "end_ms": info["latest_end_ms"],
                "duration_ms": info["active_dur_ms"],
            }
        )

    video_srcs = sorted(derived_video_clips, key=lambda x: x["start_ms"])
    audio_srcs = sorted(derived_audio_clips, key=lambda x: x["start_ms"])

    ffmpeg_path = find_tool("ffmpeg.exe")
    if not ffmpeg_path:
        log("ffmpeg.exe not found. Please check your installation.", "ERROR")
        sys.exit(1)
    cmd = [ffmpeg_path, "-y"]

    # Input 0: black canvas
    cmd.extend(
        [
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s={canvas_w}x{canvas_h}:r={fps}:d={total_sec},format=yuv420p",
        ]
    )

    # Subsequent inputs: video sources
    for vsrc in video_srcs:
        cmd.extend(["-i", str(vsrc["file"])])
    # Then audio sources
    for asrc in audio_srcs:
        cmd.extend(["-i", str(asrc["file"])])
    # Finally a silent audio stream as a fallback
    cmd.extend(
        [
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=channel_layout=stereo:sample_rate=44100:duration={total_sec}",
        ]
    )

    # ---- Complex filter ----
    filter_lines = []

    num_vid = len(video_srcs)
    if num_vid > 0:
        for idx, vsrc in enumerate(video_srcs):
            in_idx = 1 + idx  # inputs are 1‑based after canvas
            start_sec = vsrc["start_ms"] / 1000.0
            filter_lines.append(
                f"[{in_idx}:v]scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=decrease,"
                f"pad={canvas_w}:{canvas_h}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
                f"format=rgba,setpts=PTS-STARTPTS+{start_sec}/TB[v{idx}];"
            )

        prev = "[0:v]"
        for idx in range(num_vid):
            out_label = f"vo{idx}" if idx < num_vid - 1 else "vout"
            filter_lines.append(f"{prev}[v{idx}]overlay=0:0[{out_label}];")
            prev = f"[{out_label}]"
    else:
        filter_lines.append("[0:v]null[vout];")

    # Audio processing
    audio_labels = []
    audio_input_base = 1 + num_vid  # first audio input index
    for idx, asrc in enumerate(audio_srcs):
        in_idx = audio_input_base + idx
        delay_ms = asrc["start_ms"]
        label = f"a{idx}"
        filter_lines.append(
            f"[{in_idx}:a]asetpts=PTS-STARTPTS,aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
            f"adelay={delay_ms}|{delay_ms}:all=1[{label}];"
        )
        audio_labels.append(f"[{label}]")

    silence_idx = 1 + num_vid + len(audio_srcs)
    filter_lines.append(
        f"[{silence_idx}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[silence];"
    )
    audio_labels.append("[silence]")

    mixer_count = len(audio_labels)
    filter_lines.append(
        f"{''.join(audio_labels)}amix=inputs={mixer_count}:duration=longest:dropout_transition=0[outa];"
    )

    cmd.extend(
        [
            "-filter_complex",
            "".join(filter_lines),
            "-map",
            "[vout]",
            "-map",
            "[outa]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "30",
            "-c:a",
            "aac",
            "-b:a",
            "92k",
            "-movflags",
            "+faststart",
            "-avoid_negative_ts",
            "make_zero",
            str(output_video),
        ]
    )

    execute_ffmpeg(cmd, description="Merging final video", duration_sec=total_sec)
    log(f"🎉 Final video written to {output_video}")


# ------------------------------------------------------------
# 4. Main orchestrator
# ------------------------------------------------------------
def process_recording(folder_path, output_video="synced_class.mp4", xml_only=False):
    folder = Path(folder_path)
    if not folder.is_dir():
        log("Provided path is not a valid directory.", "ERROR")
        return

    screen_clips, audio_clips, _ = collect_media_intervals(folder)
    if not screen_clips and not audio_clips:
        log("No media files with a valid pacingTick found.", "ERROR")
        return

    # Determine overall length (+ 2 seconds padding)
    last_end = max(
        (c["end_ms"] for c in screen_clips + audio_clips),
        default=0,
    )
    total_ms = last_end + 2000

    xml_path = folder / "timeline.xml"
    log("Generating timeline.xml …")
    write_timeline_xml(folder, screen_clips, audio_clips, total_ms, xml_path)

    if xml_only:
        log("XML generation complete. Stopping as requested.")
        return

    log("Starting video assembly from timeline.xml …")
    render_video_from_timeline(folder, xml_path, output_video)

# در سطح ماژول
FFMPEG_PATH = None
FFPROBE_PATH = None

def init_tools():
    global FFMPEG_PATH, FFPROBE_PATH
    FFMPEG_PATH = find_tool("ffmpeg.exe")
    FFPROBE_PATH = find_tool("ffprobe.exe")
    if not FFMPEG_PATH or not FFPROBE_PATH:
        log("ffmpeg.exe or ffprobe.exe not found. Aborting.", "ERROR")
        sys.exit(1)


if __name__ == "__main__":
    init_tools()
    
    # Get terminal width (fallback to 80)
    term_width = shutil.get_terminal_size().columns

    if Figlet:
        f = Figlet(font='slant')
        banner = f.renderText("VCD - v 0.1")
    else:
        # simple fallback banner
        banner = "VCD - v0.1"
    
    # Print banner centered
    for line in banner.splitlines():
        print(Fore.CYAN + line.center(term_width) + Style.RESET_ALL)
    
    # Print description line, centered, in a dim style
    description = ("Currently processes only classes with screenshare & audio; "
                           "file sharing (PDF, PPTX, etc.) is not yet supported. "
                            "(Future updates will address this. :) )")
    # First line of description (or just one line)
    print(Style.DIM + Fore.CYAN + description.center(term_width) + Style.RESET_ALL)
    print()  # blank line for spacing
    
    check_ffmpeg()

    meeting_url = input(Fore.LIGHTMAGENTA_EX + "Enter meeting URL:  (e.g. https://vadavc32.ec.iau.ir/l2e72tear3ee)" + Style.RESET_ALL).strip()

    # Extract recording ID early, same logic as inside download_and_extract_zip
    parsed = urlparse(meeting_url)
    path = parsed.path.rstrip("/")
    recording_id = path.split("/")[-1] if path else ""
    if not recording_id:
        log("Could not extract recording ID from the URL.", "ERROR")
        sys.exit(1)

    working_dir = recording_id          # e.g. "liw4ztmnwbh2" — matches the zip name

    if Path(working_dir).is_dir():
        log(f"Folder '{working_dir}' already exists. Skipping download.")
        result_dir = Path(working_dir)  # a Path object, same as what download_and_extract_zip returns
    else:
        # Download & extract into a folder named after the recording ID
        result_dir = download_and_extract_zip(meeting_url, target_dir=working_dir)
        if result_dir is None:
            log("Automatic download failed – stopping.", "ERROR")
            sys.exit(1)

    #  produce the video
    final_mp4 = f"Class-{recording_id}.mp4"
    process_recording(str(result_dir), output_video=final_mp4)

    log("All done. Final video is ready.")

# v0.1
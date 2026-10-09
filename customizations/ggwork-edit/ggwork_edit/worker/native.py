"""Real whisper.cpp transcription and original-audio H.264/AAC rendering."""

import asyncio
import hashlib
import json
import math
import os
import platform
import re
import shutil
import signal
import tempfile
import wave
from pathlib import Path

from .storage import WorkerError, confined, digest, identifier, no_symlink, private_json


class WorkerStopped(WorkerError):
    pass


class ProcessRunner:
    def __init__(self, stop=None, lock_fd=None):
        self.stop = stop or asyncio.Event()
        self.lock_fd = lock_fd

    async def run(self, argv):
        if self.stop.is_set():
            raise WorkerStopped("stopped")
        # File-backed logs bound memory even when a native process is verbose.
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            process = await asyncio.create_subprocess_exec(
                *map(str, argv),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
                pass_fds=(self.lock_fd,) if self.lock_fd is not None else (),
            )
            waiter = asyncio.create_task(process.wait())
            stopper = asyncio.create_task(self.stop.wait())
            try:
                await asyncio.wait((waiter, stopper), return_when=asyncio.FIRST_COMPLETED)
                if self.stop.is_set():
                    await self._terminate(process)
                    raise WorkerStopped("stopped")
                if process.returncode:
                    raise WorkerError("native_process_failed")
                stdout.seek(0)
                return stdout.read(8 * 1024 * 1024)
            except asyncio.CancelledError:
                await self._terminate(process)
                raise
            finally:
                stopper.cancel()
                await asyncio.gather(stopper, return_exceptions=True)
                await waiter

    async def _terminate(self, process):
        # Signal the complete process group, then reap the process before acknowledging.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
        # A wrapper may exit before its descendants: kill survivors in the same group.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


async def inspect_model(store, runner):
    """Read actual loaded model metadata; a CLI label is not capability evidence."""
    config = store.config()
    with tempfile.TemporaryDirectory(prefix=".model-probe-", dir=store.home) as directory:
        wav = Path(directory) / "probe.wav"
        prefix = Path(directory) / "metadata"
        with wave.open(str(wav), "wb") as stream:
            stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            stream.writeframes(b"\0" * 32000)
        await runner.run(["whisper-cli", "-m", config["model"], "-f", wav, "-l", "en", "-ojf", "-of", prefix])
        try:
            metadata = json.loads(prefix.with_suffix(".json").read_text(encoding="utf-8"))
            multilingual = metadata["model"]["multilingual"]
            if not isinstance(multilingual, bool):
                raise ValueError("invalid model metadata")
        except (KeyError, ValueError, OSError) as error:
            raise WorkerError("model_metadata_invalid") from error
        if config["model_language"] == "multilingual" and not multilingual:
            raise WorkerError("model_language_mismatch")
        return multilingual


async def doctor(store):
    config = store.config()
    reasons = []
    supported = platform.system() == "Darwin" and platform.machine() == "arm64"
    if not supported:
        reasons.append("unsupported_platform")
    for executable in ("ffmpeg", "ffprobe", "whisper-cli"):
        if not shutil.which(executable):
            reasons.append(executable + "_missing")
    model = Path(config["model"])
    if not model.is_file() or model.name.startswith("for-tests-") or model.stat().st_size < 1_000_000:
        reasons.append("transcription_model_missing")
    elif await asyncio.to_thread(digest, model) != config["model_sha256"]:
        reasons.append("transcription_model_changed")
    if not config["grants"]:
        reasons.append("source_grant_missing")
    for root in config["grants"].values():
        try:
            if not no_symlink(root).is_dir():
                reasons.append("source_grant_unavailable")
        except WorkerError:
            reasons.append("source_grant_unavailable")
    try:
        output_root = no_symlink(config["output_root"])
        with tempfile.TemporaryFile(dir=output_root):
            pass
    except (OSError, WorkerError):
        reasons.append("output_directory_unavailable")
    if not reasons:
        try:
            runner = ProcessRunner(lock_fd=store.lock_fd)
            await inspect_model(store, runner)
            await runner.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    "file",
                    "-format_whitelist",
                    "lavfi",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=size=64x64:duration=0.1",
                    "-c:v",
                    "libx264",
                    "-f",
                    "null",
                    "-",
                ]
            )
        except WorkerError as error:
            reasons.append(str(error))
        except OSError:
            reasons.append("native_toolchain_unusable")
    return {
        "platform": "darwin-arm64" if supported else "unsupported",
        "ready": not reasons,
        "reasons": reasons,
        "grants": list(config["grants"]),
        "worker_version": "0.1.0",
    }


def media_input(path, *, suffix=None):
    """Constrain the demuxer before it can open nested paths or protocols."""
    suffix = (suffix or Path(path).suffix).lower()
    if suffix in (".mp4", ".mov", ".m4v"):
        demuxer = "mov"
        options = ["-enable_drefs", "0", "-use_absolute_path", "0"]
    elif suffix in (".mkv", ".webm"):
        demuxer = "matroska"
        options = []
    else:
        raise WorkerError("native_process_failed")
    return ["-protocol_whitelist", "file", "-format_whitelist", "mov,matroska,webm", "-f", demuxer, *options, "-i", path]


class NativeWorker:
    def __init__(self, store, stop=None):
        self.store = store
        self.runner = ProcessRunner(stop, store.lock_fd)
        self._proofs = {}
        self._model_proof = None

    async def probe(self, path, *, suffix=None):
        raw = await self.runner.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", *media_input(path, suffix=suffix)])
        value = json.loads(raw)
        video = next((s for s in value["streams"] if s["codec_type"] == "video"), None)
        audio = next((s for s in value["streams"] if s["codec_type"] == "audio"), None)
        duration = float(value["format"].get("duration", 0))
        if not video or not audio or not math.isfinite(duration) or duration <= 0:
            raise WorkerError("source_audio_video_required")
        return {
            "duration_seconds": duration,
            "width": video["width"],
            "height": video["height"],
            "video_codec": video["codec_name"],
            "audio_codec": audio["codec_name"],
        }

    async def discover(self, grant_id, relative):
        directory = self.store.directory(grant_id, relative)
        root = Path(self.store.config()["grants"][grant_id])
        files = []
        episodes = set()
        for path in sorted(directory.iterdir()):
            if path.suffix.lower() not in (".mp4", ".mov", ".mkv", ".m4v", ".webm"):
                continue
            self.store.source(grant_id, str(path.relative_to(root)))
            match = re.fullmatch(r"(?:episode[-_ ]?|ep[-_ ]?|e)?0*([1-9][0-9]*)", path.stem, re.IGNORECASE)
            if not match or int(match[1]) in episodes:
                raise WorkerError("episode_order_ambiguous")
            episode = int(match[1])
            episodes.add(episode)
            files.append(
                {
                    "media_id": "media-" + str(episode),
                    "name": path.name,
                    "episode": episode,
                    "relative_path": str(path.relative_to(root)),
                    "size_bytes": path.stat().st_size,
                    "sha256": None,
                    "duration_seconds": None,
                    "state": "selected",
                }
            )
        if not files:
            raise WorkerError("source_directory_empty")
        return {"version": 1, "grant_id": grant_id, "files": sorted(files, key=lambda f: f["episode"])}

    async def verify_manifest(self, manifest):
        verified = []
        for source in manifest["files"]:
            path = self.store.source(manifest["grant_id"], source["relative_path"])
            before = path.stat()
            sha = await asyncio.to_thread(digest, path)
            metadata = await self.probe(path)
            after = path.stat()
            if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise WorkerError("source_changed")
            if source["size_bytes"] != after.st_size or (source.get("sha256") and source["sha256"] != sha):
                raise WorkerError("source_changed")
            verified.append({**source, "sha256": sha, "duration_seconds": metadata["duration_seconds"], "state": "verified"})
        for field in ("media_id", "episode", "relative_path"):
            if len({s[field] for s in verified}) != len(verified):
                raise WorkerError("source_identity_ambiguous")
        if not verified:
            raise WorkerError("source_manifest_empty")
        return {**manifest, "files": verified}

    async def check_identity(self, manifest):
        for source in manifest["files"]:
            path = self.store.source(manifest["grant_id"], source["relative_path"])
            if source["state"] != "verified" or path.stat().st_size != source["size_bytes"] or await asyncio.to_thread(digest, path) != source["sha256"]:
                raise WorkerError("source_changed")

    async def transcribe(self, manifest, language, attempt_id):
        await self.check_identity(manifest)
        config = self.store.config()
        if config["model_language"] == "en" and language != "en":
            raise WorkerError("model_language_unsupported")
        if not re.fullmatch(r"[a-z]{2,3}|auto", language):
            raise WorkerError("language_unsupported")
        if await asyncio.to_thread(digest, config["model"]) != config["model_sha256"]:
            raise WorkerError("transcription_model_changed")
        cache = self.store.home / "transcripts"
        cache.mkdir(mode=0o700, exist_ok=True)
        work = self.store.workspace(attempt_id)
        executable_hash = await asyncio.to_thread(digest, shutil.which("whisper-cli"))
        model_proof = (config["model_sha256"], executable_hash, config["model_language"])
        if self._model_proof != model_proof:
            await inspect_model(self.store, self.runner)
            self._model_proof = model_proof
        transcripts = []
        for source in manifest["files"]:
            key = hashlib.sha256(
                json.dumps([source["sha256"], config["model_sha256"], executable_hash, language, "whisper-cpp-json-v3-file-mov-matroska-16khz"]).encode()
            ).hexdigest()
            cached = no_symlink(cache / (key + ".json"))
            if cached.exists():
                segments = json.loads(cached.read_text(encoding="utf-8"))
            else:
                path = self.store.source(manifest["grant_id"], source["relative_path"])
                # Each invocation owns fresh private scratch paths. Resumed workspace
                # entries (including symlinks) are never reused as native destinations.
                with tempfile.TemporaryDirectory(prefix=".asr-", dir=work) as scratch:
                    wav = Path(scratch) / "audio.wav"
                    prefix = Path(scratch) / "transcript"
                    await self.runner.run(["ffmpeg", "-v", "error", "-n", *media_input(path), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav])
                    await self.runner.run(["whisper-cli", "-m", config["model"], "-f", wav, "-l", language, "-ojf", "-of", prefix])
                    raw = json.loads(Path(str(prefix) + ".json").read_text(encoding="utf-8"))
                    segments = [
                        {"start": item["offsets"]["from"] / 1000, "end": item["offsets"]["to"] / 1000, "text": item["text"].strip()}
                        for item in raw["transcription"]
                        if item["text"].strip()
                    ]
                    # whisper may pad the final token beyond the WAV; reject rather than invent a timestamp.
                    if not segments or any(not 0 <= s["start"] < s["end"] <= source["duration_seconds"] for s in segments):
                        raise WorkerError("transcript_timestamp_invalid")
                    await self.check_identity(manifest)
                    private_json(cached, segments)
            transcripts.append({"media_id": source["media_id"], "segments": segments})
        return transcripts

    def validate_plan(self, manifest, requirements, plan):
        try:
            self._validate_plan(manifest, requirements, plan)
        except (KeyError, TypeError, ValueError) as error:
            raise WorkerError("plan_invalid") from error

    def _validate_plan(self, manifest, requirements, plan):
        if set(plan) != {"profile", "aspect_ratio", "language", "outputs"} or any(
            plan.get(k) != requirements[k] for k in ("profile", "aspect_ratio", "language")
        ):
            raise WorkerError("plan_requirements_mismatch")
        if plan["profile"] not in ("highlight", "hook") or plan["aspect_ratio"] not in ("9:16", "16:9", "1:1"):
            raise WorkerError("plan_profile_unsupported")
        sources = {f["media_id"]: f for f in manifest["files"]}
        expected = {f"out-{i + 1}" for i in range(requirements["output_count"])}
        if len(plan["outputs"]) != len(expected) or {o["output_id"] for o in plan["outputs"]} != expected:
            raise WorkerError("plan_output_mismatch")
        for output in plan["outputs"]:
            if set(output) != {"output_id", "segments"} or not output["segments"]:
                raise WorkerError("plan_invalid")
            duration = 0
            for segment in output["segments"]:
                source = sources.get(segment.get("media_id"))
                if set(segment) != {"media_id", "start", "end"} or source is None:
                    raise WorkerError("plan_source_invalid")
                start, end = segment["start"], segment["end"]
                if any(isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t) for t in (start, end)):
                    raise WorkerError("plan_time_invalid")
                if not 0 <= start < end <= source["duration_seconds"]:
                    raise WorkerError("plan_time_invalid")
                duration += end - start
            if abs(duration - requirements["duration_seconds"]) > max(1, requirements["duration_seconds"] * 0.1):
                raise WorkerError("plan_duration_mismatch")

    async def render(self, manifest, requirements, plan, attempt_id, output_id):
        self.validate_plan(manifest, requirements, plan)
        await self.check_identity(manifest)
        work = self.store.workspace(attempt_id)
        final = confined(work, identifier(output_id) + ".mp4")
        if final.exists():
            raise WorkerError("output_exists")
        partial = no_symlink(work / (identifier(output_id) + ".partial.mp4"))
        if partial.exists():
            partial.unlink()  # Unpublished crash residue, never a delivered output.
        output = next(o for o in plan["outputs"] if o["output_id"] == output_id)
        sizes = {"9:16": (720, 1280), "16:9": (1280, 720), "1:1": (720, 720)}
        width, height = sizes[requirements["aspect_ratio"]]
        args = ["ffmpeg", "-v", "error", "-n"]
        filters, labels = [], []
        sources = {s["media_id"]: s for s in manifest["files"]}
        for index, segment in enumerate(output["segments"]):
            path = self.store.source(manifest["grant_id"], sources[segment["media_id"]]["relative_path"])
            args.extend(media_input(path))
            start, end = segment["start"], segment["end"]
            filters.append(
                f"[{index}:v:0]trim=start={start}:end={end},setpts=PTS-STARTPTS,scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p[v{index}]"
            )
            filters.append(
                f"[{index}:a:0]atrim=start={start}:end={end},asetpts=PTS-STARTPTS,aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{index}]"
            )
            labels.append(f"[v{index}][a{index}]")
        filters.append("".join(labels) + f"concat=n={len(labels)}:v=1:a=1[v][a]")
        args.extend(
            [
                "-filter_complex",
                ";".join(filters),
                "-map",
                "[v]",
                "-map",
                "[a]",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                partial,
            ]
        )
        try:
            await self.runner.run(args)
            metadata = await self.probe(partial)
            await self.runner.run(["ffmpeg", "-v", "error", "-xerror", *media_input(partial), "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"])
            expected_duration = sum(s["end"] - s["start"] for s in output["segments"])
            if (
                (metadata["width"], metadata["height"]) != (width, height)
                or metadata["video_codec"] != "h264"
                or metadata["audio_codec"] != "aac"
                or abs(metadata["duration_seconds"] - expected_duration) > 0.25
            ):
                raise WorkerError("output_validation_failed")
            await self.check_identity(manifest)
            if self.runner.stop.is_set():
                raise WorkerStopped("stopped")
            sha = await asyncio.to_thread(digest, partial)
            artifact_id = identifier(attempt_id) + "_" + identifier(output_id)
            result = {"artifact_id": artifact_id, "sha256": sha, "size_bytes": partial.stat().st_size, **metadata, "verified": True}
            # Persist complete encoded bytes before making their immutable reference visible.
            with partial.open("rb") as stream:
                os.fsync(stream.fileno())
            # Exclusive link publishes atomically without replacing any completed bytes.
            os.link(partial, final)
            os.chmod(final, 0o400)
            indexes = no_symlink(self.store.home / "artifacts")
            indexes.mkdir(mode=0o700, exist_ok=True)
            index = indexes / (artifact_id + ".json")
            if index.exists():
                raise WorkerError("artifact_exists")
            private_json(index, {"path": str(final), "result": result})
            return result
        finally:
            partial.unlink(missing_ok=True)

    def read_artifact(self, command):
        index = no_symlink(self.store.home / "artifacts" / (identifier(command["artifact_id"]) + ".json"))
        if not index.exists():
            raise WorkerError("file_missing")
        record = json.loads(index.read_text(encoding="utf-8"))
        result = record["result"]
        if result["sha256"] != command["sha256"] or result["size_bytes"] != command["size_bytes"]:
            raise WorkerError("file_changed")
        root = no_symlink(self.store.config()["output_root"])
        path = no_symlink(record["path"])
        if not path.is_relative_to(root):
            raise WorkerError("grant_denied")
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError as error:
            raise WorkerError("file_missing") from error
        with os.fdopen(fd, "rb") as stream:
            stat = os.fstat(stream.fileno())
            identity = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            if self._proofs.get(command["artifact_id"]) != identity:
                h = hashlib.sha256()
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    h.update(chunk)
                if h.hexdigest() != result["sha256"] or stat.st_size != result["size_bytes"]:
                    raise WorkerError("file_changed")
                self._proofs[command["artifact_id"]] = identity
            offset, length = command["offset"], command["length"]
            if not isinstance(offset, int) or not isinstance(length, int) or offset < 0 or not 0 < length <= 1024 * 1024 or offset + length > stat.st_size:
                raise WorkerError("range_invalid")
            stream.seek(offset)
            data = stream.read(length)
            after = os.fstat(stream.fileno())
            if len(data) != length or identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise WorkerError("file_changed")
            return data

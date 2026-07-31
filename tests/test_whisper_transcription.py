import os
import tempfile
import threading
import time
import unittest
from dataclasses import FrozenInstanceError, dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from link2vid.core import whisper_transcription as wt


class TestVerifiedMedia(unittest.TestCase):
    def test_is_current_requires_regular_nonempty_matching_stat(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "clip.mp4"
            path.write_bytes(b"abc123")
            stat = path.stat()
            media = wt.VerifiedMedia(path=path, size=stat.st_size, modified_at=stat.st_mtime)
            self.assertTrue(media.is_current())

    def test_is_current_false_when_missing(self):
        path = Path("definitely-missing-link2vid-media.bin")
        media = wt.VerifiedMedia(path=path, size=1, modified_at=1.0)
        self.assertFalse(media.is_current())

    def test_is_current_false_when_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.mp4"
            path.write_bytes(b"")
            media = wt.VerifiedMedia(path=path, size=0, modified_at=path.stat().st_mtime)
            self.assertFalse(media.is_current())

    def test_is_current_false_for_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            media = wt.VerifiedMedia(path=path, size=1, modified_at=path.stat().st_mtime)
            self.assertFalse(media.is_current())

    def test_is_current_handles_stat_oserror(self):
        path = Path("unreadable-link2vid-media.bin")
        media = wt.VerifiedMedia(path=path, size=1, modified_at=1.0)
        with mock.patch.object(Path, "is_file", return_value=True):
            with mock.patch.object(Path, "stat", side_effect=OSError("filesystem unavailable")):
                self.assertFalse(media.is_current())

    def test_is_current_false_when_size_or_mtime_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "clip.mp4"
            path.write_bytes(b"abc123")
            stat = path.stat()
            media = wt.VerifiedMedia(path=path, size=stat.st_size, modified_at=stat.st_mtime)

            path.write_bytes(b"abc123XX")
            self.assertFalse(media.is_current())

            path.write_bytes(b"abc123")
            # Force a distinct mtime while restoring the original size.
            newer = stat.st_mtime + 5
            # Windows may have coarse timestamp resolution; nudge until it differs.
            for _ in range(20):
                os_utime = __import__("os").utime
                os_utime(path, (newer, newer))
                if path.stat().st_mtime != media.modified_at:
                    break
                newer += 1
                time.sleep(0.01)
            self.assertNotEqual(path.stat().st_mtime, media.modified_at)
            self.assertEqual(path.stat().st_size, media.size)
            self.assertFalse(media.is_current())


class TestWhisperContractShapes(unittest.TestCase):
    def test_frozen_contract_types_are_immutable(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = wt.VerifiedMedia(path=Path(tmp) / "a.mp4", size=3, modified_at=1.0)
            request = wt.TranscriptionRequest(
                request_id="req-1",
                source_url="https://example.com/v",
                title="Demo",
                output_dir=Path(tmp),
                model_name="small",
                verified_media=media,
            )
            result = wt.TranscriptionResult(
                text_path=Path(tmp) / "a.whisper.en.txt",
                srt_path=Path(tmp) / "a.whisper.en.srt",
                language="en",
                segment_count=2,
                model_name="small",
                effective_device="cpu",
                fallback_used=True,
            )
            snapshot = wt.WhisperRunSnapshot(
                request_id="req-1",
                phase=wt.WhisperPhase.COMPLETE,
                model_name="small",
                cache_root=str(Path(tmp) / "models"),
                effective_device="cpu",
                fallback_used=True,
                source_kind="reused",
                error_reason=None,
                updated_at=1.0,
            )
            with self.assertRaises(FrozenInstanceError):
                request.model_name = "tiny"  # type: ignore[misc]
            with self.assertRaises(FrozenInstanceError):
                result.segment_count = 9  # type: ignore[misc]
            with self.assertRaises(FrozenInstanceError):
                snapshot.phase = wt.WhisperPhase.FAILED  # type: ignore[misc]
            self.assertIsInstance(wt.GpuInitializationUnavailable("cuda unavailable"), Exception)
            self.assertEqual(
                set(wt.WhisperPhase),
                {
                    wt.WhisperPhase.PREPARING_AUDIO,
                    wt.WhisperPhase.DOWNLOADING_AUDIO,
                    wt.WhisperPhase.WAITING_FOR_SERVICE,
                    wt.WhisperPhase.LOADING_MODEL,
                    wt.WhisperPhase.FALLING_BACK_CPU,
                    wt.WhisperPhase.TRANSCRIBING,
                    wt.WhisperPhase.WRITING,
                    wt.WhisperPhase.COMPLETE,
                    wt.WhisperPhase.FAILED,
                },
            )


class TestWhisperModelAdapter(unittest.TestCase):
    def test_available_models_is_lazy_and_returns_exact_aliases(self):
        sentinel = ["tiny", "small", "medium"]
        with mock.patch.object(wt, "_import_available_models", return_value=lambda: sentinel) as importer:
            self.assertEqual(wt.available_models(), sentinel)
            importer.assert_called_once()

    def test_validate_model_name_accepts_exact_alias_only(self):
        aliases = ["tiny", "small", "medium"]
        self.assertEqual(wt.validate_model_name("small", models=aliases), "small")
        with self.assertRaises(wt.InvalidWhisperModelError):
            wt.validate_model_name("Systran/faster-whisper-small", models=aliases)
        with self.assertRaises(wt.InvalidWhisperModelError):
            wt.validate_model_name(r"C:\models\small", models=aliases)
        with self.assertRaises(wt.InvalidWhisperModelError):
            wt.validate_model_name("SMALL", models=aliases)

    def test_default_model_name_prefers_large_v3(self):
        self.assertEqual(
            wt.default_model_name(
                ["tiny", "small", "large-v3", "distil-large-v3.5"]
            ),
            "large-v3",
        )
        self.assertEqual(wt.default_model_name(["tiny", "base"]), "tiny")
        with self.assertRaises(wt.InvalidWhisperModelError):
            wt.default_model_name([])

    def test_module_source_keeps_faster_whisper_import_lazy(self):
        import ast

        source = Path(wt.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        top_level_imports = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                top_level_imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                top_level_imports.append(node.module)
        self.assertNotIn("faster_whisper", top_level_imports)
        self.assertNotIn("ctranslate2", top_level_imports)

        lazy_helpers = {
            "_import_available_models",
            "_import_whisper_model",
            "_import_cuda_device_count",
        }
        found = set()
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in lazy_helpers:
                found.add(node.name)
                nested = []
                for child in ast.walk(node):
                    if isinstance(child, ast.ImportFrom) and child.module:
                        nested.append(child.module)
                    elif isinstance(child, ast.Import):
                        nested.extend(alias.name for alias in child.names)
                if node.name == "_import_cuda_device_count":
                    self.assertIn("ctranslate2", nested)
                else:
                    self.assertIn("faster_whisper", nested)
        self.assertEqual(found, lazy_helpers)


class TestCorePackageExports(unittest.TestCase):
    def test_core_exports_contract_symbols(self):
        import link2vid.core as core

        self.assertIs(core.VerifiedMedia, wt.VerifiedMedia)
        self.assertIs(core.WhisperPhase, wt.WhisperPhase)
        self.assertIs(core.TranscriptionRequest, wt.TranscriptionRequest)
        self.assertIs(core.TranscriptionResult, wt.TranscriptionResult)
        self.assertIs(core.WhisperRunSnapshot, wt.WhisperRunSnapshot)
        self.assertIs(core.GpuInitializationUnavailable, wt.GpuInitializationUnavailable)
        self.assertIs(core.WhisperService, wt.WhisperService)
        self.assertNotIn("available_models", core.__all__)
        self.assertNotIn("InvalidWhisperModelError", core.__all__)


@dataclass
class _FakeSegment:
    start: float
    end: float
    text: str


class _TrackingSegments:
    def __init__(self, items):
        self._items = list(items)
        self.started = False
        self.exhausted = False
        self.yielded = 0

    def __iter__(self):
        self.started = True
        for item in self._items:
            self.yielded += 1
            yield item
        self.exhausted = True


class _FakeModel:
    def __init__(self, segments, language="en", on_transcribe=None):
        self.segments = segments
        self.language = language
        self.on_transcribe = on_transcribe
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append((audio, dict(kwargs)))
        if self.on_transcribe is not None:
            self.on_transcribe(audio, kwargs)
        info = SimpleNamespace(language=self.language)
        return self.segments, info


def _make_request(tmp: Path, *, model_name: str = "small", title: str = "Demo Clip") -> wt.TranscriptionRequest:
    return wt.TranscriptionRequest(
        request_id="req-1",
        source_url="https://example.com/v",
        title=title,
        output_dir=tmp,
        model_name=model_name,
        verified_media=None,
    )


def _service(
    *,
    factory,
    cuda_count=1,
    models=("tiny", "small", "medium"),
    cache_dir=None,
):
    cache = cache_dir or (lambda: Path(tempfile.mkdtemp()))
    return wt.WhisperService(
        model_factory=factory,
        cuda_device_count=(cuda_count if callable(cuda_count) else (lambda: cuda_count)),
        available_models_fn=lambda: list(models),
        cache_dir_fn=cache,
    )


class TestLanguageAndTimestamps(unittest.TestCase):
    def test_sanitize_language_code_defensive_und(self):
        self.assertEqual(wt.sanitize_language_code("en"), "en")
        self.assertEqual(wt.sanitize_language_code("EN"), "en")
        self.assertEqual(wt.sanitize_language_code(None), "und")
        self.assertEqual(wt.sanitize_language_code(""), "und")
        self.assertEqual(wt.sanitize_language_code("??"), "und")
        self.assertEqual(wt.sanitize_language_code("zh-CN"), "zh-cn")

    def test_format_srt_timestamp_edge_cases(self):
        self.assertEqual(wt.format_srt_timestamp(0), "00:00:00,000")
        self.assertEqual(wt.format_srt_timestamp(1.5), "00:00:01,500")
        self.assertEqual(wt.format_srt_timestamp(3661.234), "01:01:01,234")
        self.assertEqual(wt.format_srt_timestamp(-1), "00:00:00,000")
        self.assertEqual(wt.format_srt_timestamp(0.0004), "00:00:00,000")
        self.assertEqual(wt.format_srt_timestamp(0.0015), "00:00:00,002")

    def test_format_srt_timestamp_rejects_nonfinite_values(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "must be finite"):
                    wt.format_srt_timestamp(value)


class TestPairedWriters(unittest.TestCase):
    def test_streams_exact_text_and_srt_and_skips_blanks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            segments = _TrackingSegments(
                [
                    _FakeSegment(0.0, 1.0, "  Hello  "),
                    _FakeSegment(1.0, 2.0, "   "),
                    _FakeSegment(2.5, 3.25, "World"),
                ]
            )
            text_path, srt_path, count = wt.write_paired_transcripts(
                output_dir=root,
                title='Bonus: "Demo"',
                language="en",
                segments=segments,
            )
            self.assertTrue(segments.started)
            self.assertTrue(segments.exhausted)
            self.assertEqual(count, 2)
            self.assertEqual(text_path.name, "Bonus_ _Demo_.whisper.en.txt")
            self.assertEqual(srt_path.name, "Bonus_ _Demo_.whisper.en.srt")
            self.assertEqual(text_path.read_text(encoding="utf-8"), "Hello\nWorld\n")
            self.assertEqual(
                srt_path.read_text(encoding="utf-8"),
                "1\n"
                "00:00:00,000 --> 00:00:01,000\n"
                "Hello\n"
                "\n"
                "2\n"
                "00:00:02,500 --> 00:00:03,250\n"
                "World\n",
            )

    def test_zero_segments_raises_and_leaves_no_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(wt.NoSpeechError):
                wt.write_paired_transcripts(
                    output_dir=root,
                    title="Silent",
                    language="en",
                    segments=[_FakeSegment(0, 1, "  "), _FakeSegment(1, 2, "")],
                )
            self.assertEqual(list(root.glob("*.txt")), [])
            self.assertEqual(list(root.glob("*.srt")), [])
            self.assertEqual(list(root.glob(".link2vid-whisper-*")), [])

    def test_nonfinite_timestamp_cleans_owned_pair_and_temps(self):
        cases = [
            (float("nan"), 1.0),
            (float("inf"), 1.0),
            (float("-inf"), 1.0),
            (0.0, float("nan")),
            (0.0, float("inf")),
            (0.0, float("-inf")),
        ]
        for start, end in cases:
            with self.subTest(start=start, end=end), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                with self.assertRaisesRegex(ValueError, "must be finite"):
                    wt.write_paired_transcripts(
                        output_dir=root,
                        title="Invalid Time",
                        language="en",
                        segments=[_FakeSegment(start, end, "speech")],
                    )
                self.assertEqual(list(root.iterdir()), [])

    def test_none_end_defaults_to_start_and_zero_end_is_reversed_clamped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, srt_path, count = wt.write_paired_transcripts(
                output_dir=root,
                title="Timing",
                language="en",
                segments=[
                    _FakeSegment(2.0, 0.0, "zero end"),
                    _FakeSegment(3.0, None, "missing end"),  # type: ignore[arg-type]
                ],
            )
            self.assertEqual(count, 2)
            self.assertEqual(
                srt_path.read_text(encoding="utf-8"),
                "1\n"
                "00:00:02,000 --> 00:00:02,000\n"
                "zero end\n"
                "\n"
                "2\n"
                "00:00:03,000 --> 00:00:03,000\n"
                "missing end\n",
            )

    def test_collision_uses_shared_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            existing_txt = root / "Demo.whisper.en.txt"
            existing_txt.write_text("old\n", encoding="utf-8")
            text_path, srt_path, count = wt.write_paired_transcripts(
                output_dir=root,
                title="Demo",
                language="en",
                segments=[_FakeSegment(0, 1, "new")],
            )
            self.assertEqual(count, 1)
            self.assertEqual(text_path.name, "Demo.whisper.en (2).txt")
            self.assertEqual(srt_path.name, "Demo.whisper.en (2).srt")
            self.assertEqual(existing_txt.read_text(encoding="utf-8"), "old\n")
            self.assertEqual(text_path.read_text(encoding="utf-8"), "new\n")

    def test_second_publish_failure_rolls_back_partial_member(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real_replace = os.replace
            calls = {"n": 0}

            def flaky_replace(src, dst):
                calls["n"] += 1
                if calls["n"] == 2:
                    raise OSError("simulated second publish failure")
                return real_replace(src, dst)

            with mock.patch.object(wt.os, "replace", side_effect=flaky_replace):
                with self.assertRaises(OSError):
                    wt.write_paired_transcripts(
                        output_dir=root,
                        title="Demo",
                        language="en",
                        segments=[_FakeSegment(0, 1, "hello")],
                    )
            self.assertEqual(list(root.glob("Demo.whisper.en*")), [])
            self.assertEqual(list(root.glob(".link2vid-whisper-*")), [])

    def test_race_on_placeholder_advances_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Occupy unsuffixed pair exclusively so reservation must advance.
            (root / "Demo.whisper.en.txt").write_text("x", encoding="utf-8")
            (root / "Demo.whisper.en.srt").write_text("x", encoding="utf-8")
            reserved = wt.reserve_output_pair(root, "Demo", "en")
            self.assertEqual(reserved.text_path.name, "Demo.whisper.en (2).txt")
            self.assertEqual(reserved.srt_path.name, "Demo.whisper.en (2).srt")
            reserved.text_path.unlink()
            reserved.srt_path.unlink()

    def test_second_placeholder_oserror_removes_first_placeholder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real_create = wt._exclusive_create
            calls = {"count": 0}

            def fail_second_create(path):
                calls["count"] += 1
                if calls["count"] == 2:
                    raise OSError("simulated second placeholder failure")
                real_create(path)

            with mock.patch.object(wt, "_exclusive_create", side_effect=fail_second_create):
                with self.assertRaisesRegex(OSError, "second placeholder failure"):
                    wt.reserve_output_pair(root, "Demo", "en")
            self.assertEqual(list(root.iterdir()), [])

    def test_first_placeholder_close_failure_removes_only_action_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            existing = root / "existing.txt"
            existing.write_text("keep me", encoding="utf-8")
            unrelated = root / "unrelated-first.bin"
            real_close = os.close
            close_error = OSError("first placeholder close failed")
            close_calls = []
            state = {}
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
            if hasattr(os, "O_BINARY"):
                flags |= os.O_BINARY

            def ambiguously_fail_first_close(fd):
                close_calls.append(fd)
                if len(close_calls) == 1:
                    real_close(fd)
                    state["closed_fd"] = fd
                    state["unrelated_fd"] = os.open(str(unrelated), flags)
                    raise close_error
                return real_close(fd)

            with mock.patch.object(wt.os, "close", side_effect=ambiguously_fail_first_close):
                with self.assertRaisesRegex(OSError, "first placeholder close failed") as raised:
                    wt.write_paired_transcripts(
                        output_dir=root,
                        title="Demo",
                        language="en",
                        segments=[_FakeSegment(0, 1, "hello")],
                    )

            unrelated_fd = state["unrelated_fd"]
            try:
                self.assertIs(raised.exception, close_error)
                self.assertEqual(close_calls, [state["closed_fd"]])
                self.assertEqual(unrelated_fd, state["closed_fd"])
                os.write(unrelated_fd, b"still usable")
            finally:
                real_close(unrelated_fd)

            self.assertEqual(existing.read_text(encoding="utf-8"), "keep me")
            self.assertEqual(unrelated.read_bytes(), b"still usable")
            self.assertEqual(list(root.glob("Demo.whisper.*")), [])
            self.assertEqual(list(root.glob(".link2vid-whisper-*")), [])

    def test_second_placeholder_close_failure_removes_only_action_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            existing = root / "existing.txt"
            existing.write_text("keep me", encoding="utf-8")
            unrelated = root / "unrelated-second.bin"
            real_close = os.close
            close_error = OSError("second placeholder close failed")
            close_calls = []
            state = {}
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
            if hasattr(os, "O_BINARY"):
                flags |= os.O_BINARY

            def ambiguously_fail_second_close(fd):
                close_calls.append(fd)
                if len(close_calls) == 1:
                    return real_close(fd)
                if len(close_calls) == 2:
                    real_close(fd)
                    state["closed_fd"] = fd
                    state["unrelated_fd"] = os.open(str(unrelated), flags)
                    raise close_error
                return real_close(fd)

            with mock.patch.object(wt.os, "close", side_effect=ambiguously_fail_second_close):
                with self.assertRaisesRegex(OSError, "second placeholder close failed") as raised:
                    wt.write_paired_transcripts(
                        output_dir=root,
                        title="Demo",
                        language="en",
                        segments=[_FakeSegment(0, 1, "hello")],
                    )

            unrelated_fd = state["unrelated_fd"]
            try:
                self.assertIs(raised.exception, close_error)
                self.assertEqual(len(close_calls), 2)
                self.assertEqual(close_calls[-1], state["closed_fd"])
                self.assertEqual(unrelated_fd, state["closed_fd"])
                os.write(unrelated_fd, b"still usable")
            finally:
                real_close(unrelated_fd)

            self.assertEqual(existing.read_text(encoding="utf-8"), "keep me")
            self.assertEqual(unrelated.read_bytes(), b"still usable")
            self.assertEqual(list(root.glob("Demo.whisper.*")), [])
            self.assertEqual(list(root.glob(".link2vid-whisper-*")), [])


class TestWhisperService(unittest.TestCase):
    def test_list_available_models_defers_default_native_adapter_until_called(self):
        aliases = ["tiny", "small"]
        with mock.patch.object(
            wt,
            "_import_available_models",
            return_value=lambda: aliases,
        ) as importer:
            service = wt.WhisperService()
            importer.assert_not_called()

            self.assertEqual(service.list_available_models(), aliases)
            importer.assert_called_once_with()

    def test_list_available_models_is_lazy_and_defensive(self):
        aliases = ["tiny", "small"]
        calls = []

        def adapter():
            calls.append(1)
            return aliases

        service = wt.WhisperService(available_models_fn=adapter)
        self.assertEqual(calls, [])

        first = service.list_available_models()
        self.assertEqual(calls, [1])
        self.assertEqual(first, aliases)
        self.assertIsNot(first, aliases)

        first.append("local-mutation")
        self.assertEqual(service.list_available_models(), aliases)
        self.assertEqual(calls, [1, 1])

    def test_exact_transcribe_args_and_cuda_preference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "models"
            source = root / "clip.mp4"
            source.write_bytes(b"media")
            builds = []
            model = _FakeModel([_FakeSegment(0, 1, "hi"), _FakeSegment(1, 2, "there")])

            def factory(model_name, device, compute_type, download_root):
                builds.append((model_name, device, compute_type, download_root))
                return model

            phases = []
            service = _service(factory=factory, cuda_count=1, cache_dir=lambda: cache)
            result = service.transcribe(
                _make_request(root),
                source,
                on_phase=phases.append,
            )
            self.assertEqual(builds, [("small", "cuda", "float16", str(cache))])
            self.assertEqual(model.calls[0][0], str(source))
            self.assertEqual(
                model.calls[0][1],
                {
                    "language": None,
                    "task": "transcribe",
                    "beam_size": 5,
                    "word_timestamps": False,
                },
            )
            self.assertEqual(result.effective_device, "cuda")
            self.assertFalse(result.fallback_used)
            self.assertEqual(result.segment_count, 2)
            self.assertEqual(result.language, "en")
            self.assertTrue(result.text_path.is_file())
            self.assertTrue(result.srt_path.is_file())
            self.assertEqual(
                phases,
                [
                    wt.WhisperPhase.WAITING_FOR_SERVICE,
                    wt.WhisperPhase.LOADING_MODEL,
                    wt.WhisperPhase.TRANSCRIBING,
                    wt.WhisperPhase.WRITING,
                    wt.WhisperPhase.COMPLETE,
                ],
            )
            self.assertTrue(source.exists(), "user media must never be removed")

    def test_invalid_alias_rejected_before_model_or_source_work(self):
        factory = mock.Mock(side_effect=AssertionError("factory must not run"))
        service = _service(factory=factory, cuda_count=1)
        phases = []
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(wt.InvalidWhisperModelError):
                service.transcribe(
                    _make_request(Path(tmp), model_name="not-a-real-alias"),
                    Path(tmp) / "missing.mp4",
                    on_phase=phases.append,
                )
        factory.assert_not_called()
        self.assertEqual(phases, [])

    def test_same_key_reuses_loaded_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "clip.mp4"
            source.write_bytes(b"media")
            builds = []
            model = _FakeModel([_FakeSegment(0, 1, "one")])

            def factory(model_name, device, compute_type, download_root):
                builds.append((model_name, device, compute_type))
                return model

            service = _service(factory=factory, cuda_count=1, cache_dir=lambda: root / "models")
            service.transcribe(_make_request(root), source)
            service.transcribe(_make_request(root), source)
            self.assertEqual(builds, [("small", "cuda", "float16")])
            self.assertEqual(len(model.calls), 2)

    def test_changed_model_replaces_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "clip.mp4"
            source.write_bytes(b"media")
            builds = []

            def factory(model_name, device, compute_type, download_root):
                builds.append((model_name, device, compute_type))
                return _FakeModel([_FakeSegment(0, 1, model_name)])

            service = _service(factory=factory, cuda_count=1, cache_dir=lambda: root / "models")
            service.transcribe(_make_request(root, model_name="small"), source)
            service.transcribe(_make_request(root, model_name="tiny"), source)
            self.assertEqual(
                builds,
                [("small", "cuda", "float16"), ("tiny", "cuda", "float16")],
            )
            self.assertEqual(service.loaded_slot, ("tiny", "cuda", "float16"))

    def test_failed_gpu_construction_does_not_commit_slot_or_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "clip.mp4"
            source.write_bytes(b"media")

            def factory(model_name, device, compute_type, download_root):
                raise RuntimeError("constructor/download failed")

            service = _service(factory=factory, cuda_count=1, cache_dir=lambda: root / "models")
            with self.assertRaises(RuntimeError):
                service.transcribe(_make_request(root), source)
            self.assertIsNone(service.loaded_slot)

    def test_failed_different_model_construction_preserves_prior_good_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "clip.mp4"
            source.write_bytes(b"media")
            good_model = _FakeModel([_FakeSegment(0, 1, "good")])

            def factory(model_name, device, compute_type, download_root):
                if model_name == "tiny":
                    raise RuntimeError("tiny construction failed")
                return good_model

            service = _service(factory=factory, cuda_count=1, cache_dir=lambda: root / "models")
            first = service.transcribe(_make_request(root, model_name="small"), source)
            with self.assertRaisesRegex(RuntimeError, "tiny construction failed"):
                service.transcribe(_make_request(root, model_name="tiny"), source)
            self.assertEqual(service.loaded_slot, ("small", "cuda", "float16"))
            self.assertTrue(first.text_path.is_file())
            self.assertTrue(first.srt_path.is_file())

    def test_availability_fallback_once_to_cpu_int8(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "models"
            source = root / "clip.mp4"
            source.write_bytes(b"media")
            builds = []
            model = _FakeModel([_FakeSegment(0, 1, "cpu")])

            def factory(model_name, device, compute_type, download_root):
                builds.append((model_name, device, compute_type, download_root))
                return model

            phases = []
            service = _service(factory=factory, cuda_count=0, cache_dir=lambda: cache)
            result = service.transcribe(_make_request(root), source, on_phase=phases.append)
            self.assertEqual(builds, [("small", "cpu", "int8", str(cache))])
            self.assertEqual(result.effective_device, "cpu")
            self.assertTrue(result.fallback_used)
            self.assertIn(wt.WhisperPhase.FALLING_BACK_CPU, phases)
            self.assertEqual(service.loaded_slot, ("small", "cpu", "int8"))

    def test_no_fallback_on_inference_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "clip.mp4"
            source.write_bytes(b"media")
            builds = []

            def factory(model_name, device, compute_type, download_root):
                builds.append((device, compute_type))

                class Boom:
                    def transcribe(self, audio, **kwargs):
                        raise RuntimeError("decode/inference failed")

                return Boom()

            service = _service(factory=factory, cuda_count=1, cache_dir=lambda: root / "models")
            with self.assertRaises(RuntimeError):
                service.transcribe(_make_request(root), source)
            self.assertEqual(builds, [("cuda", "float16")])

    def test_no_fallback_on_write_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "clip.mp4"
            source.write_bytes(b"media")
            builds = []

            def factory(model_name, device, compute_type, download_root):
                builds.append((device, compute_type))
                return _FakeModel([_FakeSegment(0, 1, "x")])

            service = _service(factory=factory, cuda_count=1, cache_dir=lambda: root / "models")
            with mock.patch.object(wt, "write_paired_transcripts", side_effect=OSError("write failed")):
                with self.assertRaises(OSError):
                    service.transcribe(_make_request(root), source)
            self.assertEqual(builds, [("cuda", "float16")])

    def test_no_fallback_when_constructor_raises_gpu_unavailable(self):
        """Constructor exceptions are not mapped; only the availability API falls back."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "clip.mp4"
            source.write_bytes(b"media")
            builds = []

            def factory(model_name, device, compute_type, download_root):
                builds.append((device, compute_type))
                raise wt.GpuInitializationUnavailable("must not be mapped from constructor")

            service = _service(factory=factory, cuda_count=1, cache_dir=lambda: root / "models")
            with self.assertRaises(wt.GpuInitializationUnavailable):
                service.transcribe(_make_request(root), source)
            self.assertEqual(builds, [("cuda", "float16")])
            self.assertIsNone(service.loaded_slot)

    def test_lock_serializes_load_and_inference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "clip.mp4"
            source.write_bytes(b"media")
            started = threading.Event()
            release = threading.Event()
            order = []

            def factory(model_name, device, compute_type, download_root):
                return _FakeModel(
                    [_FakeSegment(0, 1, "a")],
                    on_transcribe=lambda audio, kwargs: (
                        order.append("transcribe-enter"),
                        started.set(),
                        release.wait(timeout=2),
                        order.append("transcribe-exit"),
                    ),
                )

            service = _service(factory=factory, cuda_count=1, cache_dir=lambda: root / "models")
            errors = []

            def worker(name):
                try:
                    service.transcribe(_make_request(root, title=name), source)
                    order.append(f"done-{name}")
                except Exception as exc:  # pragma: no cover - test failure path
                    errors.append(exc)

            t1 = threading.Thread(target=worker, args=("One",))
            t2 = threading.Thread(target=worker, args=("Two",))
            t1.start()
            self.assertTrue(started.wait(timeout=2))
            t2.start()
            # Second thread should be blocked on the service lock.
            time.sleep(0.1)
            self.assertEqual(order.count("transcribe-enter"), 1)
            release.set()
            t1.join(timeout=2)
            t2.join(timeout=2)
            self.assertEqual(errors, [])
            self.assertEqual(order.count("transcribe-enter"), 2)
            self.assertIn("done-One", order)
            self.assertIn("done-Two", order)

    def test_same_thread_callback_reentry_fails_without_slot_or_output_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "clip.mp4"
            source.write_bytes(b"media")
            builds = []

            def factory(model_name, device, compute_type, download_root):
                builds.append(model_name)
                return _FakeModel([_FakeSegment(0, 1, model_name)])

            service = _service(factory=factory, cuda_count=1, cache_dir=lambda: root / "models")
            nested_request = _make_request(root, model_name="tiny", title="Nested")

            def reenter_on_complete(phase):
                if phase == wt.WhisperPhase.COMPLETE:
                    service.transcribe(nested_request, source)

            with self.assertRaisesRegex(
                wt.WhisperServiceReentrancyError,
                "cannot be re-entered from a phase callback",
            ):
                service.transcribe(
                    _make_request(root, model_name="small", title="Outer"),
                    source,
                    on_phase=reenter_on_complete,
                )

            self.assertEqual(builds, ["small"])
            self.assertEqual(service.loaded_slot, ("small", "cuda", "float16"))
            self.assertEqual(list(root.glob("*.whisper.*.txt")), [])
            self.assertEqual(list(root.glob("*.whisper.*.srt")), [])
            self.assertEqual(list(root.glob(".link2vid-whisper-*")), [])
            self.assertTrue(source.is_file())

    def test_generator_is_consumed_lazily_during_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "clip.mp4"
            source.write_bytes(b"media")
            tracking = _TrackingSegments([_FakeSegment(0, 1, "lazy"), _FakeSegment(1, 2, "stream")])
            observed = {"during_transcribe": None, "before_write_phase": None}

            def on_transcribe(audio, kwargs):
                observed["during_transcribe"] = (tracking.started, tracking.yielded)

            model = _FakeModel(tracking, on_transcribe=on_transcribe)
            phases = []

            def on_phase(phase):
                phases.append(phase)
                if phase == wt.WhisperPhase.WRITING:
                    observed["before_write_phase"] = (tracking.started, tracking.yielded)

            service = _service(
                factory=lambda *args: model,
                cuda_count=1,
                cache_dir=lambda: root / "models",
            )
            service.transcribe(_make_request(root), source, on_phase=on_phase)
            self.assertEqual(observed["during_transcribe"], (False, 0))
            self.assertEqual(observed["before_write_phase"], (False, 0))
            self.assertTrue(tracking.exhausted)
            self.assertEqual(tracking.yielded, 2)
            self.assertIn(wt.WhisperPhase.WRITING, phases)

    def test_missing_language_uses_und_in_filenames(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "clip.mp4"
            source.write_bytes(b"media")
            model = _FakeModel([_FakeSegment(0, 1, "hi")], language=None)
            service = _service(
                factory=lambda *args: model,
                cuda_count=0,
                cache_dir=lambda: root / "models",
            )
            result = service.transcribe(_make_request(root), source)
            self.assertEqual(result.language, "und")
            self.assertTrue(result.text_path.name.endswith(".whisper.und.txt"))
            self.assertTrue(result.srt_path.name.endswith(".whisper.und.srt"))


@unittest.skipUnless(
    os.environ.get("LINK2VID_WHISPER_SMOKE_MEDIA"),
    "Set LINK2VID_WHISPER_SMOKE_MEDIA to a local spoken media path for opt-in smoke",
)
class TestWhisperServiceOptInSmoke(unittest.TestCase):
    def test_real_local_media_produces_pair(self):
        media = Path(os.environ["LINK2VID_WHISPER_SMOKE_MEDIA"])
        self.assertTrue(media.is_file(), f"smoke media missing: {media}")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "models"
            service = wt.WhisperService(cache_dir_fn=lambda: cache)
            result = service.transcribe(
                wt.TranscriptionRequest(
                    request_id="smoke",
                    source_url="file://local",
                    title="Smoke",
                    output_dir=root,
                    model_name="tiny",
                    verified_media=None,
                ),
                media,
            )
            self.assertTrue(result.text_path.is_file())
            self.assertTrue(result.srt_path.is_file())
            self.assertGreater(result.text_path.stat().st_size, 0)
            self.assertGreater(result.srt_path.stat().st_size, 0)
            self.assertGreaterEqual(result.segment_count, 1)


if __name__ == "__main__":
    unittest.main()

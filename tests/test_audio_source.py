import errno
import os
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from link2vid.core import audio_source as aus
from link2vid.core.errors import CookiesRequiredError
from link2vid.core.whisper_transcription import TranscriptionRequest, VerifiedMedia


def _request(
    *,
    tmp: Path,
    verified_media: VerifiedMedia | None,
    title: str = "Demo Title",
) -> TranscriptionRequest:
    return TranscriptionRequest(
        request_id=str(uuid.uuid4()),
        source_url="https://example.com/v",
        title=title,
        output_dir=tmp,
        model_name="small",
        verified_media=verified_media,
    )


def _verified(path: Path) -> VerifiedMedia:
    stat = path.stat()
    return VerifiedMedia(path=path, size=stat.st_size, modified_at=stat.st_mtime)


def _nudge_mtime(path: Path, original_mtime: float) -> None:
    newer = original_mtime + 5
    for _ in range(20):
        os.utime(path, (newer, newer))
        if path.stat().st_mtime != original_mtime:
            return
        newer += 1
        time.sleep(0.01)
    raise AssertionError("unable to change mtime for stale-media test")


def _remove_directory_link(path: Path) -> None:
    path_stat = os.lstat(str(path))
    if stat.S_ISLNK(path_stat.st_mode):
        os.unlink(str(path))
    else:
        os.rmdir(str(path))


class TestSourceLeaseConstruction(unittest.TestCase):
    def test_path_is_canonical_path_instance(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "clip.mp4"
            media.write_bytes(b"abc")
            lease = aus.SourceLease(str(media), aus.SourceKind.REUSED, None)
            self.assertIsInstance(lease.path, Path)
            self.assertEqual(lease.path, media)
            self.assertIsNone(lease.workspace)

    def test_reused_with_workspace_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "clip.mp4"
            workspace = Path(tmp) / "ws"
            with self.assertRaises(ValueError) as ctx:
                aus.SourceLease(media, aus.SourceKind.REUSED, workspace)
            self.assertIn("REUSED", str(ctx.exception))
            self.assertIn("workspace", str(ctx.exception).lower())

    def test_invalid_kind_type_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "clip.mp4"
            media.write_bytes(b"x")
            with self.assertRaises(TypeError):
                aus.SourceLease(media, "reused", None)  # type: ignore[arg-type]

    def test_temporary_workspace_must_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "missing-workspace"
            with self.assertRaisesRegex(ValueError, "must already exist"):
                aus.SourceLease(
                    workspace / "audio.m4a",
                    aus.SourceKind.TEMPORARY,
                    workspace,
                )

    def test_temporary_workspace_must_be_real_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "not-a-directory"
            workspace.write_bytes(b"not a workspace")
            with self.assertRaisesRegex(ValueError, "must be a directory"):
                aus.SourceLease(
                    workspace / "audio.m4a",
                    aus.SourceKind.TEMPORARY,
                    workspace,
                )

    def test_temporary_source_must_resolve_beneath_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "owned-workspace"
            workspace.mkdir()
            outside = root / "user-audio.m4a"
            outside.write_bytes(b"user media")
            with self.assertRaisesRegex(ValueError, "must resolve beneath"):
                aus.SourceLease(
                    outside,
                    aus.SourceKind.TEMPORARY,
                    workspace,
                )
            self.assertTrue(outside.is_file())
            self.assertTrue(workspace.is_dir())

    def test_symlink_workspace_is_rejected_without_touching_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "user-output"
            target.mkdir()
            precious = target / "keep.txt"
            precious.write_text("keep", encoding="utf-8")
            workspace = root / "workspace-link"
            try:
                os.symlink(str(target), str(workspace), target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"directory symlinks unavailable: {exc}")
            try:
                with self.assertRaisesRegex(ValueError, "symlink.*junction.*reparse"):
                    aus.SourceLease(
                        workspace / "audio.m4a",
                        aus.SourceKind.TEMPORARY,
                        workspace,
                    )
                self.assertEqual(precious.read_text(encoding="utf-8"), "keep")
            finally:
                _remove_directory_link(workspace)

    def test_source_symlink_resolving_outside_workspace_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "owned-workspace"
            workspace.mkdir()
            outside = root / "user-audio.m4a"
            outside.write_bytes(b"user media")
            source_link = workspace / "audio.m4a"
            try:
                os.symlink(str(outside), str(source_link))
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"file symlinks unavailable: {exc}")
            with self.assertRaisesRegex(ValueError, "must resolve beneath"):
                aus.SourceLease(
                    source_link,
                    aus.SourceKind.TEMPORARY,
                    workspace,
                )
            self.assertEqual(outside.read_bytes(), b"user media")

    @unittest.skipUnless(os.name == "nt", "Windows junction behavior")
    def test_windows_junction_workspace_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "user-output"
            target.mkdir()
            precious = target / "keep.txt"
            precious.write_text("keep", encoding="utf-8")
            workspace = root / "workspace-junction"
            result = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(workspace), str(target)],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                self.skipTest(f"junction creation unavailable: {result.stderr.strip()}")
            try:
                with self.assertRaisesRegex(ValueError, "symlink.*junction.*reparse"):
                    aus.SourceLease(
                        workspace / "audio.m4a",
                        aus.SourceKind.TEMPORARY,
                        workspace,
                    )
                self.assertEqual(precious.read_text(encoding="utf-8"), "keep")
            finally:
                _remove_directory_link(workspace)


class TestAudioSourceReuse(unittest.TestCase):
    def test_resolve_returns_reused_lease_for_current_verified_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "exact.mp4"
            media.write_bytes(b"exact-bytes")
            request = _request(tmp=root, verified_media=_verified(media))
            resolver = aus.AudioSourceResolver()

            with mock.patch.object(resolver, "_acquire_temporary") as acquire:
                lease = resolver.resolve(request)
                acquire.assert_not_called()

            self.assertEqual(lease.kind, aus.SourceKind.REUSED)
            self.assertEqual(lease.path, media)
            self.assertIsInstance(lease.path, Path)
            self.assertIsNone(lease.workspace)
            self.assertTrue(media.is_file())
            lease.release()
            self.assertTrue(media.is_file())

    def test_resolve_uses_exact_verified_path_not_title_or_output_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exact = root / "card-session.mp4"
            decoy = root / "Demo Title.mp4"
            exact.write_bytes(b"exact")
            decoy.write_bytes(b"decoy-by-title")
            request = _request(
                tmp=root,
                verified_media=_verified(exact),
                title="Demo Title",
            )
            lease = aus.AudioSourceResolver().resolve(request)
            self.assertEqual(lease.path, exact)
            self.assertNotEqual(lease.path, decoy)

    def test_missing_verified_media_without_manager_raises_actionable_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = _request(tmp=Path(tmp), verified_media=None)
            resolver = aus.AudioSourceResolver(download_manager=None)
            with self.assertRaises(aus.AudioAcquisitionError) as ctx:
                resolver.resolve(request)
            message = str(ctx.exception)
            self.assertIn("download manager", message.lower())
            self.assertIsInstance(ctx.exception, RuntimeError)

    def test_empty_verified_media_falls_through_to_acquisition(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "empty.mp4"
            media.write_bytes(b"")
            verified = VerifiedMedia(path=media, size=0, modified_at=media.stat().st_mtime)
            self.assertFalse(verified.is_current())
            request = _request(tmp=root, verified_media=verified)
            with self.assertRaises(aus.AudioAcquisitionError) as ctx:
                aus.AudioSourceResolver().resolve(request)
            self.assertIn("download manager", str(ctx.exception).lower())

    def test_changed_verified_media_falls_through_to_acquisition(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "clip.mp4"
            media.write_bytes(b"abc123")
            verified = _verified(media)
            media.write_bytes(b"abc123XX")
            self.assertFalse(verified.is_current())
            request = _request(tmp=root, verified_media=verified)
            with self.assertRaises(aus.AudioAcquisitionError) as ctx:
                aus.AudioSourceResolver().resolve(request)
            self.assertIn("download manager", str(ctx.exception).lower())

    def test_mtime_changed_verified_media_falls_through_to_acquisition(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "clip.mp4"
            media.write_bytes(b"abc123")
            verified = _verified(media)
            _nudge_mtime(media, verified.modified_at)
            self.assertFalse(verified.is_current())
            request = _request(tmp=root, verified_media=verified)
            with self.assertRaises(aus.AudioAcquisitionError):
                aus.AudioSourceResolver().resolve(request)

    def test_deleted_verified_media_falls_through_to_acquisition(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "clip.mp4"
            media.write_bytes(b"abc123")
            verified = _verified(media)
            media.unlink()
            self.assertFalse(verified.is_current())
            request = _request(tmp=root, verified_media=verified)
            with self.assertRaises(aus.AudioAcquisitionError) as ctx:
                aus.AudioSourceResolver().resolve(request)
            self.assertIn("download manager", str(ctx.exception).lower())

    def test_stale_media_does_not_scan_user_output_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stale = root / "gone.mp4"
            stale.write_bytes(b"old")
            verified = _verified(stale)
            stale.unlink()
            decoy = root / "Demo Title.mp4"
            decoy.write_bytes(b"scanned-decoy")
            request = _request(tmp=root, verified_media=verified, title="Demo Title")
            manager = mock.Mock()

            def download(url, format_id, outtmpl, progress_hook=None, post_hook=None):
                workspace = Path(outtmpl).parent
                audio = workspace / "owned.m4a"
                audio.write_bytes(b"owned-audio")
                if post_hook:
                    post_hook(str(audio))
                return True

            manager.download.side_effect = download
            lease = aus.AudioSourceResolver(download_manager=manager).resolve(request)
            try:
                self.assertEqual(lease.kind, aus.SourceKind.TEMPORARY)
                self.assertNotEqual(lease.path, decoy)
                self.assertTrue(decoy.is_file())
                self.assertEqual(decoy.read_bytes(), b"scanned-decoy")
                self.assertTrue(
                    lease.workspace is not None
                    and lease.workspace.resolve() in lease.path.resolve().parents
                )
                self.assertNotEqual(lease.workspace.resolve(), root.resolve())
            finally:
                lease.release()
            self.assertTrue(decoy.is_file())

    def test_progress_hook_is_accepted_but_unused_on_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "clip.mp4"
            media.write_bytes(b"abc")
            request = _request(tmp=root, verified_media=_verified(media))
            calls: list[object] = []

            def hook(*args, **kwargs):
                calls.append((args, kwargs))

            lease = aus.AudioSourceResolver().resolve(request, progress_hook=hook)
            self.assertEqual(lease.kind, aus.SourceKind.REUSED)
            self.assertEqual(calls, [])


class TestSourceLeaseCleanup(unittest.TestCase):
    def test_reused_release_never_deletes_user_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "user.mp4"
            media.write_bytes(b"keep-me")
            lease = aus.SourceLease(media, aus.SourceKind.REUSED, None)
            lease.release()
            lease.release()
            self.assertTrue(media.is_file())
            self.assertEqual(media.read_bytes(), b"keep-me")

    def test_reused_media_survives_exception_inside_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "user.mp4"
            media.write_bytes(b"keep-me")
            with self.assertRaisesRegex(ValueError, "body failed"):
                with aus.SourceLease(media, aus.SourceKind.REUSED, None):
                    raise ValueError("body failed")
            self.assertEqual(media.read_bytes(), b"keep-me")

    def test_context_manager_releases_on_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "owned-ws"
            workspace.mkdir()
            audio = workspace / "audio.m4a"
            audio.write_bytes(b"temp-audio")
            with aus.SourceLease(audio, aus.SourceKind.TEMPORARY, workspace) as lease:
                self.assertEqual(lease.path, audio)
                self.assertTrue(workspace.is_dir())
            self.assertFalse(workspace.exists())
            self.assertFalse(audio.exists())

    def test_context_manager_releases_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "owned-ws"
            workspace.mkdir()
            (workspace / "audio.m4a").write_bytes(b"temp-audio")
            with self.assertRaises(RuntimeError):
                with aus.SourceLease(
                    workspace / "audio.m4a",
                    aus.SourceKind.TEMPORARY,
                    workspace,
                ):
                    raise RuntimeError("worker failed")
            self.assertFalse(workspace.exists())

    def test_temporary_owned_workspace_cleanup_is_recursive_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "owned-ws"
            nested = workspace / "nested"
            nested.mkdir(parents=True)
            audio = nested / "audio.webm"
            audio.write_bytes(b"temp")
            lease = aus.SourceLease(audio, aus.SourceKind.TEMPORARY, workspace)
            lease.release()
            self.assertFalse(workspace.exists())
            lease.release()  # already absent
            lease.release()
            self.assertFalse(workspace.exists())

    def test_release_safe_when_workspace_already_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "owned-ws"
            workspace.mkdir()
            audio = workspace / "audio.m4a"
            audio.write_bytes(b"temp-audio")
            lease = aus.SourceLease(audio, aus.SourceKind.TEMPORARY, workspace)
            shutil.rmtree(workspace)
            self.assertFalse(workspace.exists())
            lease.release()
            lease.release()

    def test_readonly_workspace_contents_are_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "owned-ws"
            nested = workspace / "nested"
            nested.mkdir(parents=True)
            audio = nested / "audio.m4a"
            audio.write_bytes(b"temp-audio")
            lease = aus.SourceLease(audio, aus.SourceKind.TEMPORARY, workspace)
            os.chmod(str(audio), stat.S_IREAD)
            os.chmod(str(nested), stat.S_IREAD | stat.S_IEXEC)
            try:
                lease.release()
            finally:
                if workspace.exists():
                    if nested.exists():
                        os.chmod(
                            str(nested),
                            stat.S_IREAD | stat.S_IWRITE | stat.S_IEXEC,
                        )
                    if audio.exists():
                        os.chmod(str(audio), stat.S_IREAD | stat.S_IWRITE)
            self.assertFalse(workspace.exists())

    def test_failed_release_remains_retryable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "owned-ws"
            workspace.mkdir()
            audio = workspace / "audio.m4a"
            audio.write_bytes(b"temp-audio")
            lease = aus.SourceLease(audio, aus.SourceKind.TEMPORARY, workspace)
            real_rmtree = shutil.rmtree
            attempts = []

            def flaky_rmtree(path, *args, **kwargs):
                attempts.append(path)
                if len(attempts) == 1:
                    raise PermissionError(errno.EACCES, "workspace locked", path)
                return real_rmtree(path, *args, **kwargs)

            with mock.patch.object(aus.shutil, "rmtree", side_effect=flaky_rmtree):
                with self.assertRaisesRegex(
                    OSError, "Failed to release owned audio workspace"
                ):
                    lease.release()
                self.assertTrue(workspace.is_dir())
                lease.release()

            self.assertEqual(len(attempts), 2)
            self.assertFalse(workspace.exists())
            lease.release()
            self.assertEqual(len(attempts), 2)

    def test_concurrent_release_performs_one_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "owned-ws"
            workspace.mkdir()
            audio = workspace / "audio.m4a"
            audio.write_bytes(b"temp-audio")
            lease = aus.SourceLease(audio, aus.SourceKind.TEMPORARY, workspace)
            real_rmtree = shutil.rmtree
            cleanup_entered = threading.Event()
            allow_cleanup = threading.Event()
            calls = []
            calls_lock = threading.Lock()

            def slow_rmtree(path, *args, **kwargs):
                with calls_lock:
                    calls.append(path)
                cleanup_entered.set()
                self.assertTrue(allow_cleanup.wait(timeout=5))
                return real_rmtree(path, *args, **kwargs)

            with mock.patch.object(aus.shutil, "rmtree", side_effect=slow_rmtree):
                with ThreadPoolExecutor(max_workers=8) as executor:
                    futures = [executor.submit(lease.release) for _ in range(8)]
                    self.assertTrue(cleanup_entered.wait(timeout=5))
                    allow_cleanup.set()
                    for future in futures:
                        future.result(timeout=5)

            self.assertEqual(len(calls), 1)
            self.assertFalse(workspace.exists())

    def test_body_exception_is_preserved_when_cleanup_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "owned-ws"
            workspace.mkdir()
            audio = workspace / "audio.m4a"
            audio.write_bytes(b"temp-audio")
            lease = aus.SourceLease(audio, aus.SourceKind.TEMPORARY, workspace)

            with mock.patch.object(
                aus.shutil,
                "rmtree",
                side_effect=PermissionError(
                    errno.EACCES, "workspace locked", str(workspace)
                ),
            ):
                with self.assertRaisesRegex(ValueError, "body failed"):
                    with lease:
                        raise ValueError("body failed")

            self.assertTrue(workspace.is_dir())
            lease.release()
            self.assertFalse(workspace.exists())

    def test_readonly_callback_rejects_nested_link_before_chmod(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "owned-ws"
            nested = workspace / "nested"
            nested.mkdir(parents=True)
            external = root / "user-output"
            external.mkdir()
            precious = external / "keep.txt"
            precious.write_text("keep", encoding="utf-8")
            external_mode = stat.S_IMODE(os.lstat(str(external)).st_mode)
            precious_mode = stat.S_IMODE(os.lstat(str(precious)).st_mode)
            nested_link = nested / "external-link"
            try:
                os.symlink(
                    str(external),
                    str(nested_link),
                    target_is_directory=True,
                )
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"directory symlinks unavailable: {exc}")

            callback = aus.SourceLease._readonly_cleanup_callback(workspace)
            operation = mock.Mock()
            cause = PermissionError(
                errno.EACCES,
                "read-only through nested link",
                str(nested_link / "keep.txt"),
            )
            exc_info = (PermissionError, cause, None)
            with mock.patch.object(aus.os, "chmod") as chmod:
                with self.assertRaisesRegex(
                    OSError,
                    "link or reparse component",
                ):
                    callback(
                        operation,
                        str(nested_link / "keep.txt"),
                        exc_info,
                    )
                chmod.assert_not_called()
            operation.assert_not_called()
            self.assertEqual(precious.read_text(encoding="utf-8"), "keep")
            self.assertEqual(
                stat.S_IMODE(os.lstat(str(external)).st_mode),
                external_mode,
            )
            self.assertEqual(
                stat.S_IMODE(os.lstat(str(precious)).st_mode),
                precious_mode,
            )

    def test_release_refuses_static_symlink_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "owned-ws"
            workspace.mkdir()
            audio = workspace / "audio.m4a"
            audio.write_bytes(b"temp-audio")
            lease = aus.SourceLease(audio, aus.SourceKind.TEMPORARY, workspace)

            moved_workspace = root / "moved-owned-ws"
            workspace.rename(moved_workspace)
            target = root / "user-output"
            target.mkdir()
            precious = target / "keep.txt"
            precious.write_text("keep", encoding="utf-8")
            try:
                os.symlink(str(target), str(workspace), target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                moved_workspace.rename(workspace)
                self.skipTest(f"directory symlinks unavailable: {exc}")

            try:
                with self.assertRaisesRegex(OSError, "is a link or reparse"):
                    lease.release()
                self.assertEqual(precious.read_text(encoding="utf-8"), "keep")
                _remove_directory_link(workspace)
                lease.release()
                lease.release()
                self.assertEqual(precious.read_text(encoding="utf-8"), "keep")
            finally:
                if os.path.lexists(str(workspace)):
                    _remove_directory_link(workspace)

    @unittest.skipUnless(os.name == "nt", "Windows junction behavior")
    def test_release_refuses_static_junction_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "owned-ws"
            workspace.mkdir()
            audio = workspace / "audio.m4a"
            audio.write_bytes(b"temp-audio")
            lease = aus.SourceLease(audio, aus.SourceKind.TEMPORARY, workspace)

            moved_workspace = root / "moved-owned-ws"
            workspace.rename(moved_workspace)
            target = root / "user-output"
            target.mkdir()
            precious = target / "keep.txt"
            precious.write_text("keep", encoding="utf-8")
            result = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(workspace), str(target)],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                moved_workspace.rename(workspace)
                self.skipTest(f"junction creation unavailable: {result.stderr.strip()}")

            try:
                with self.assertRaisesRegex(OSError, "is a link or reparse"):
                    lease.release()
                self.assertEqual(precious.read_text(encoding="utf-8"), "keep")
                _remove_directory_link(workspace)
                lease.release()
                self.assertEqual(precious.read_text(encoding="utf-8"), "keep")
            finally:
                if os.path.lexists(str(workspace)):
                    _remove_directory_link(workspace)

    def test_temporary_without_workspace_never_deletes_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Path happens to be a directory; kind alone must not authorize deletion.
            decoy = root / "not-owned"
            decoy.mkdir()
            (decoy / "file.bin").write_bytes(b"survive")
            lease = aus.SourceLease(decoy, aus.SourceKind.TEMPORARY, None)
            lease.release()
            lease.release()
            self.assertTrue(decoy.is_dir())
            self.assertTrue((decoy / "file.bin").is_file())

    def test_temporary_without_workspace_never_deletes_file_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "orphan-temp.mp4"
            media.write_bytes(b"survive")
            lease = aus.SourceLease(media, aus.SourceKind.TEMPORARY, workspace=None)
            lease.release()
            self.assertTrue(media.is_file())


class TestPhase3ATemporaryAcquisition(unittest.TestCase):
    def _download_writing(
        self,
        *,
        filename: str = "clip.m4a",
        payload: bytes = b"audio-bytes",
        call_post_hook: bool = True,
        return_ok: bool = True,
        progress_events: list | None = None,
        extra_files: list[tuple[str, bytes]] | None = None,
        report_path: Path | str | None = None,
        raise_exc: BaseException | None = None,
    ):
        workspaces: list[Path] = []

        def download(url, format_id, outtmpl, progress_hook=None, post_hook=None):
            workspace = Path(outtmpl).parent
            workspaces.append(workspace)
            if raise_exc is not None:
                raise raise_exc
            if progress_hook is not None and progress_events:
                for event in progress_events:
                    progress_hook(event)
            audio = workspace / filename
            audio.write_bytes(payload)
            for name, data in extra_files or []:
                (workspace / name).write_bytes(data)
            if call_post_hook and post_hook is not None:
                post_hook(str(report_path if report_path is not None else audio))
            return return_ok

        return download, workspaces

    def test_download_uses_exact_bestaudio_args_and_unique_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request = _request(tmp=root, verified_media=None)
            manager = mock.Mock()
            download, workspaces = self._download_writing(filename="track.webm")
            manager.download.side_effect = download

            lease = aus.AudioSourceResolver(download_manager=manager).resolve(request)
            try:
                manager.download.assert_called_once()
                args, kwargs = manager.download.call_args
                self.assertEqual(args[0], request.source_url)
                self.assertEqual(args[1], "bestaudio")
                outtmpl = Path(args[2])
                self.assertEqual(outtmpl.name, "%(title)s.%(ext)s")
                self.assertEqual(outtmpl.parent, workspaces[0])
                self.assertNotEqual(workspaces[0].resolve(), root.resolve())
                self.assertTrue(workspaces[0].name.startswith("link2vid-whisper-audio-"))
                self.assertIn("progress_hook", kwargs)
                self.assertIn("post_hook", kwargs)
                self.assertEqual(lease.kind, aus.SourceKind.TEMPORARY)
                self.assertEqual(lease.path.name, "track.webm")
                self.assertEqual(lease.path.read_bytes(), b"audio-bytes")
                self.assertEqual(lease.workspace, workspaces[0].resolve())
            finally:
                lease.release()
            self.assertFalse(workspaces[0].exists())

    def test_prefers_actual_reported_extension_and_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = _request(tmp=Path(tmp), verified_media=None)
            manager = mock.Mock()
            download, workspaces = self._download_writing(filename="actual.opus")
            manager.download.side_effect = download
            lease = aus.AudioSourceResolver(download_manager=manager).resolve(request)
            try:
                self.assertEqual(lease.path.suffix, ".opus")
                self.assertEqual(lease.path.name, "actual.opus")
                self.assertTrue(lease.path.is_file())
            finally:
                lease.release()
            self.assertFalse(workspaces[0].exists())

    def test_reported_basename_is_anchored_to_owned_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = _request(tmp=Path(tmp), verified_media=None)
            manager = mock.Mock()
            download, workspaces = self._download_writing(
                filename="basename.webm",
                report_path="basename.webm",
            )
            manager.download.side_effect = download
            lease = aus.AudioSourceResolver(download_manager=manager).resolve(request)
            try:
                self.assertEqual(lease.path, workspaces[0].resolve() / "basename.webm")
            finally:
                lease.release()
            self.assertFalse(workspaces[0].exists())

    def test_reported_dot_slash_path_is_anchored_to_owned_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = _request(tmp=Path(tmp), verified_media=None)
            manager = mock.Mock()
            download, workspaces = self._download_writing(
                filename="relative.mka",
                report_path="./relative.mka",
            )
            manager.download.side_effect = download
            lease = aus.AudioSourceResolver(download_manager=manager).resolve(request)
            try:
                self.assertEqual(lease.path, workspaces[0].resolve() / "relative.mka")
            finally:
                lease.release()
            self.assertFalse(workspaces[0].exists())

    @unittest.skipUnless(os.name == "nt", "Windows native relative path behavior")
    def test_reported_dot_backslash_path_is_anchored_to_owned_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = _request(tmp=Path(tmp), verified_media=None)
            manager = mock.Mock()
            download, workspaces = self._download_writing(
                filename="relative.mp4",
                report_path=r".\relative.mp4",
            )
            manager.download.side_effect = download
            lease = aus.AudioSourceResolver(download_manager=manager).resolve(request)
            try:
                self.assertEqual(lease.path, workspaces[0].resolve() / "relative.mp4")
            finally:
                lease.release()
            self.assertFalse(workspaces[0].exists())

    def test_reported_relative_path_does_not_use_process_cwd(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as cwd:
            request = _request(tmp=Path(tmp), verified_media=None)
            manager = mock.Mock()
            download, workspaces = self._download_writing(
                filename="owned.opus",
                report_path="owned.opus",
            )
            manager.download.side_effect = download
            previous_cwd = Path.cwd()
            os.chdir(cwd)
            try:
                lease = aus.AudioSourceResolver(download_manager=manager).resolve(
                    request
                )
            finally:
                os.chdir(previous_cwd)
            try:
                self.assertEqual(lease.path, workspaces[0].resolve() / "owned.opus")
                self.assertFalse((Path(cwd) / "owned.opus").exists())
            finally:
                lease.release()
            self.assertFalse(workspaces[0].exists())

    def test_duplicate_normalized_reports_select_one_real_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = _request(tmp=Path(tmp), verified_media=None)
            manager = mock.Mock()
            workspaces: list[Path] = []

            def download(url, format_id, outtmpl, progress_hook=None, post_hook=None):
                workspace = Path(outtmpl).parent
                workspaces.append(workspace)
                audio = workspace / "same.webm"
                audio.write_bytes(b"audio")
                if post_hook:
                    post_hook(str(audio))
                    post_hook("same.webm")
                    post_hook("./same.webm")
                return True

            manager.download.side_effect = download
            lease = aus.AudioSourceResolver(download_manager=manager).resolve(request)
            try:
                self.assertEqual(lease.path, workspaces[0].resolve() / "same.webm")
            finally:
                lease.release()
            self.assertFalse(workspaces[0].exists())

    def test_any_invalid_report_rejects_other_valid_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = _request(tmp=Path(tmp), verified_media=None)
            manager = mock.Mock()
            workspaces: list[Path] = []

            def download(url, format_id, outtmpl, progress_hook=None, post_hook=None):
                workspace = Path(outtmpl).parent
                workspaces.append(workspace)
                audio = workspace / "valid.m4a"
                audio.write_bytes(b"audio")
                if post_hook:
                    post_hook("valid.m4a")
                    post_hook("missing.m4a")
                return True

            manager.download.side_effect = download
            with self.assertRaises(aus.AudioAcquisitionError):
                aus.AudioSourceResolver(download_manager=manager).resolve(request)
            self.assertFalse(workspaces[0].exists())

    def test_scan_fallback_when_success_without_post_hook_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = _request(tmp=Path(tmp), verified_media=None)
            manager = mock.Mock()
            download, workspaces = self._download_writing(
                filename="scanned.m4a",
                call_post_hook=False,
            )
            manager.download.side_effect = download
            lease = aus.AudioSourceResolver(download_manager=manager).resolve(request)
            try:
                self.assertEqual(lease.path.name, "scanned.m4a")
                self.assertEqual(lease.path.read_bytes(), b"audio-bytes")
            finally:
                lease.release()
            self.assertFalse(workspaces[0].exists())

    def test_outside_reported_path_is_rejected_and_workspace_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "user-output.m4a"
            outside.write_bytes(b"user-media")
            request = _request(tmp=root, verified_media=None)
            manager = mock.Mock()
            download, workspaces = self._download_writing(
                filename="ignored.m4a",
                report_path=outside,
            )
            manager.download.side_effect = download
            with self.assertRaises(aus.AudioAcquisitionError) as ctx:
                aus.AudioSourceResolver(download_manager=manager).resolve(request)
            self.assertIn("outside", str(ctx.exception).lower())
            self.assertTrue(outside.is_file())
            self.assertFalse(workspaces[0].exists())

    def test_partial_artifact_reported_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = _request(tmp=Path(tmp), verified_media=None)
            manager = mock.Mock()
            download, workspaces = self._download_writing(
                filename="clip.m4a.part",
                payload=b"partial",
            )
            manager.download.side_effect = download
            with self.assertRaises(aus.AudioAcquisitionError) as ctx:
                aus.AudioSourceResolver(download_manager=manager).resolve(request)
            self.assertIn("safe", str(ctx.exception).lower())
            self.assertFalse(workspaces[0].exists())

    def test_empty_reported_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = _request(tmp=Path(tmp), verified_media=None)
            manager = mock.Mock()
            download, workspaces = self._download_writing(
                filename="empty.m4a",
                payload=b"",
            )
            manager.download.side_effect = download
            with self.assertRaises(aus.AudioAcquisitionError):
                aus.AudioSourceResolver(download_manager=manager).resolve(request)
            self.assertFalse(workspaces[0].exists())

    def test_missing_reported_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = _request(tmp=Path(tmp), verified_media=None)
            manager = mock.Mock()
            workspaces: list[Path] = []

            def download(url, format_id, outtmpl, progress_hook=None, post_hook=None):
                workspace = Path(outtmpl).parent
                workspaces.append(workspace)
                missing = workspace / "missing.m4a"
                if post_hook:
                    post_hook(str(missing))
                return True

            manager.download.side_effect = download
            with self.assertRaises(aus.AudioAcquisitionError):
                aus.AudioSourceResolver(download_manager=manager).resolve(request)
            self.assertFalse(workspaces[0].exists())

    def test_ambiguous_reported_outputs_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = _request(tmp=Path(tmp), verified_media=None)
            manager = mock.Mock()
            workspaces: list[Path] = []

            def download(url, format_id, outtmpl, progress_hook=None, post_hook=None):
                workspace = Path(outtmpl).parent
                workspaces.append(workspace)
                first = workspace / "a.m4a"
                second = workspace / "b.m4a"
                first.write_bytes(b"one")
                second.write_bytes(b"two")
                if post_hook:
                    post_hook(str(first))
                    post_hook(str(second))
                return True

            manager.download.side_effect = download
            with self.assertRaises(aus.AudioAcquisitionError) as ctx:
                aus.AudioSourceResolver(download_manager=manager).resolve(request)
            self.assertIn("multiple", str(ctx.exception).lower())
            self.assertFalse(workspaces[0].exists())

    def test_ambiguous_scan_fallback_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = _request(tmp=Path(tmp), verified_media=None)
            manager = mock.Mock()
            download, workspaces = self._download_writing(
                filename="a.m4a",
                call_post_hook=False,
                extra_files=[("b.m4a", b"two")],
            )
            manager.download.side_effect = download
            with self.assertRaises(aus.AudioAcquisitionError) as ctx:
                aus.AudioSourceResolver(download_manager=manager).resolve(request)
            self.assertIn("multiple", str(ctx.exception).lower())
            self.assertFalse(workspaces[0].exists())

    def test_scan_fallback_sidecar_only_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = _request(tmp=Path(tmp), verified_media=None)
            manager = mock.Mock()
            download, workspaces = self._download_writing(
                filename="clip.info.json",
                payload=b'{"title": "metadata"}',
                call_post_hook=False,
                extra_files=[
                    ("clip.description", b"description"),
                    ("clip.live_chat.json", b"chat"),
                    ("clip.annotations.xml", b"<annotations />"),
                    ("clip.en.vtt", b"WEBVTT"),
                ],
            )
            manager.download.side_effect = download
            with self.assertRaises(aus.AudioAcquisitionError) as ctx:
                aus.AudioSourceResolver(download_manager=manager).resolve(request)
            self.assertIn("no usable audio", str(ctx.exception).lower())
            self.assertFalse(workspaces[0].exists())

    def test_scan_fallback_sidecars_plus_audio_selects_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = _request(tmp=Path(tmp), verified_media=None)
            manager = mock.Mock()
            download, workspaces = self._download_writing(
                filename="clip.mka",
                call_post_hook=False,
                extra_files=[
                    ("clip.info.json", b"metadata"),
                    ("clip.description", b"description"),
                    ("clip.en.srt", b"subtitle"),
                ],
            )
            manager.download.side_effect = download
            lease = aus.AudioSourceResolver(download_manager=manager).resolve(request)
            try:
                self.assertEqual(lease.path.name, "clip.mka")
            finally:
                lease.release()
            self.assertFalse(workspaces[0].exists())

    def test_scan_fallback_thumbnail_plus_audio_selects_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = _request(tmp=Path(tmp), verified_media=None)
            manager = mock.Mock()
            download, workspaces = self._download_writing(
                filename="clip.mp4",
                call_post_hook=False,
                extra_files=[
                    ("clip.jpg", b"jpeg"),
                    ("clip.webp", b"webp"),
                ],
            )
            manager.download.side_effect = download
            lease = aus.AudioSourceResolver(download_manager=manager).resolve(request)
            try:
                self.assertEqual(lease.path.name, "clip.mp4")
            finally:
                lease.release()
            self.assertFalse(workspaces[0].exists())

    def test_scan_fallback_sidecars_plus_two_audio_outputs_is_ambiguous(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = _request(tmp=Path(tmp), verified_media=None)
            manager = mock.Mock()
            download, workspaces = self._download_writing(
                filename="first.webm",
                call_post_hook=False,
                extra_files=[
                    ("second.m4a", b"second"),
                    ("clip.info.json", b"metadata"),
                    ("clip.png", b"thumbnail"),
                ],
            )
            manager.download.side_effect = download
            with self.assertRaises(aus.AudioAcquisitionError) as ctx:
                aus.AudioSourceResolver(download_manager=manager).resolve(request)
            self.assertIn("multiple", str(ctx.exception).lower())
            self.assertFalse(workspaces[0].exists())

    def test_download_false_cleans_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = _request(tmp=Path(tmp), verified_media=None)
            manager = mock.Mock()
            download, workspaces = self._download_writing(return_ok=False)
            manager.download.side_effect = download
            with self.assertRaises(aus.AudioAcquisitionError) as ctx:
                aus.AudioSourceResolver(download_manager=manager).resolve(request)
            self.assertIn("failed", str(ctx.exception).lower())
            self.assertFalse(workspaces[0].exists())

    def test_generic_exception_is_wrapped_with_chaining(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = _request(tmp=Path(tmp), verified_media=None)
            manager = mock.Mock()
            original = RuntimeError("yt-dlp exploded")
            download, workspaces = self._download_writing(raise_exc=original)
            manager.download.side_effect = download
            with self.assertRaises(aus.AudioAcquisitionError) as ctx:
                aus.AudioSourceResolver(download_manager=manager).resolve(request)
            self.assertIs(ctx.exception.__cause__, original)
            self.assertIn("could not obtain", str(ctx.exception).lower())
            self.assertFalse(workspaces[0].exists())

    def test_cookies_required_passthrough_and_workspace_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = _request(tmp=Path(tmp), verified_media=None)
            manager = mock.Mock()
            original = RuntimeError("login wall")
            cookie_error = CookiesRequiredError(original=original)
            download, workspaces = self._download_writing(raise_exc=cookie_error)
            manager.download.side_effect = download
            with self.assertRaises(CookiesRequiredError) as ctx:
                aus.AudioSourceResolver(download_manager=manager).resolve(request)
            self.assertIs(ctx.exception, cookie_error)
            self.assertIs(ctx.exception.original, original)
            self.assertFalse(isinstance(ctx.exception, aus.AudioAcquisitionError))
            self.assertFalse(workspaces[0].exists())

    def test_progress_hook_is_forwarded_to_downloader(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = _request(tmp=Path(tmp), verified_media=None)
            manager = mock.Mock()
            events = [{"status": "downloading", "downloaded_bytes": 10, "total_bytes": 100}]
            seen: list[object] = []

            def hook(d):
                seen.append(d)

            download, workspaces = self._download_writing(progress_events=events)
            manager.download.side_effect = download
            lease = aus.AudioSourceResolver(download_manager=manager).resolve(
                request,
                progress_hook=hook,
            )
            try:
                self.assertEqual(seen, events)
                args, kwargs = manager.download.call_args
                self.assertIs(kwargs["progress_hook"], hook)
            finally:
                lease.release()
            self.assertFalse(workspaces[0].exists())

    def test_lease_construction_failure_cleans_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = _request(tmp=Path(tmp), verified_media=None)
            manager = mock.Mock()
            download, workspaces = self._download_writing(filename="ok.m4a")
            manager.download.side_effect = download
            with mock.patch.object(
                aus,
                "SourceLease",
                side_effect=ValueError("lease boom"),
            ):
                with self.assertRaises(aus.AudioAcquisitionError) as ctx:
                    aus.AudioSourceResolver(download_manager=manager).resolve(request)
            self.assertIn("lease", str(ctx.exception).lower())
            self.assertIsInstance(ctx.exception.__cause__, ValueError)
            self.assertFalse(workspaces[0].exists())

    def test_success_lease_cleanup_removes_owned_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = _request(tmp=Path(tmp), verified_media=None)
            manager = mock.Mock()
            download, workspaces = self._download_writing()
            manager.download.side_effect = download
            lease = aus.AudioSourceResolver(download_manager=manager).resolve(request)
            self.assertTrue(workspaces[0].is_dir())
            self.assertTrue(lease.path.is_file())
            lease.release()
            lease.release()
            self.assertFalse(workspaces[0].exists())
            self.assertFalse(lease.path.exists())

    def test_service_failure_cleanup_through_context_manager(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = _request(tmp=Path(tmp), verified_media=None)
            manager = mock.Mock()
            download, workspaces = self._download_writing()
            manager.download.side_effect = download
            with self.assertRaisesRegex(RuntimeError, "service failed"):
                with aus.AudioSourceResolver(download_manager=manager).resolve(request):
                    raise RuntimeError("service failed")
            self.assertFalse(workspaces[0].exists())

    def test_reused_media_makes_zero_downloader_calls_and_survives(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "exact.mp4"
            media.write_bytes(b"keep-me")
            request = _request(tmp=root, verified_media=_verified(media))
            manager = mock.Mock()
            lease = aus.AudioSourceResolver(download_manager=manager).resolve(request)
            manager.download.assert_not_called()
            self.assertEqual(lease.kind, aus.SourceKind.REUSED)
            lease.release()
            self.assertEqual(media.read_bytes(), b"keep-me")

    def test_concurrent_acquisitions_use_distinct_workspaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = _request(tmp=Path(tmp), verified_media=None)
            manager = mock.Mock()
            workspaces: list[Path] = []
            barrier = threading.Barrier(2)
            lock = threading.Lock()

            def download(url, format_id, outtmpl, progress_hook=None, post_hook=None):
                workspace = Path(outtmpl).parent
                with lock:
                    workspaces.append(workspace)
                barrier.wait(timeout=5)
                audio = workspace / f"{threading.get_ident()}.m4a"
                audio.write_bytes(b"audio")
                if post_hook:
                    post_hook(str(audio))
                return True

            manager.download.side_effect = download
            resolver = aus.AudioSourceResolver(download_manager=manager)
            leases: list[aus.SourceLease] = []
            errors: list[BaseException] = []

            def worker():
                try:
                    leases.append(resolver.resolve(request))
                except BaseException as exc:  # noqa: BLE001 - collect for assertion
                    errors.append(exc)

            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)
            self.assertEqual(errors, [])
            self.assertEqual(len(leases), 2)
            self.assertEqual(len(workspaces), 2)
            self.assertNotEqual(workspaces[0].resolve(), workspaces[1].resolve())
            self.assertNotEqual(leases[0].workspace, leases[1].workspace)
            for lease in leases:
                lease.release()
            for workspace in workspaces:
                self.assertFalse(workspace.exists())

    def test_scan_fallback_ignores_partial_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = _request(tmp=Path(tmp), verified_media=None)
            manager = mock.Mock()
            download, workspaces = self._download_writing(
                filename="good.m4a",
                call_post_hook=False,
                extra_files=[
                    ("clip.m4a.part", b"partial"),
                    ("clip.f251.webm", b"fragment"),
                    ("notes.tmp", b"tmp"),
                ],
            )
            manager.download.side_effect = download
            lease = aus.AudioSourceResolver(download_manager=manager).resolve(request)
            try:
                self.assertEqual(lease.path.name, "good.m4a")
            finally:
                lease.release()
            self.assertFalse(workspaces[0].exists())


class TestExportsAndLazyImport(unittest.TestCase):
    def test_core_package_exports_audio_source_symbols(self):
        import link2vid.core as core

        self.assertIs(core.SourceKind, aus.SourceKind)
        self.assertIs(core.SourceLease, aus.SourceLease)
        self.assertIs(core.AudioAcquisitionError, aus.AudioAcquisitionError)
        self.assertIs(core.AudioSourceResolver, aus.AudioSourceResolver)
        self.assertIn("SourceKind", core.__all__)
        self.assertIn("AudioSourceResolver", core.__all__)

    def test_audio_source_module_has_no_eager_faster_whisper_import(self):
        import ast
        from pathlib import Path

        source = Path(aus.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertFalse(
                        alias.name == "faster_whisper" or alias.name.startswith("faster_whisper."),
                        "audio_source must not eagerly import faster_whisper",
                    )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                self.assertFalse(
                    module == "faster_whisper" or module.startswith("faster_whisper."),
                    "audio_source must not eagerly import faster_whisper",
                )


if __name__ == "__main__":
    unittest.main()

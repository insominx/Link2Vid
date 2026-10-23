"""View models that replaced the CustomTkinter widgets (cards, batch bar, log)."""

from __future__ import annotations

import unittest

from link2vid.app.viewmodels import (
    BatchBarModel,
    CardModel,
    LogModel,
    infer_card_state,
    log_level,
)
from link2vid.core.downloader import TranscriptTrack


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def __call__(self, event: str, payload: dict) -> None:
        self.events.append((event, payload))

    def last(self, event: str) -> dict:
        return [payload for name, payload in self.events if name == event][-1]


def _card(**overrides) -> tuple[CardModel, _Recorder]:
    emit = _Recorder()
    kwargs = dict(
        index=3,
        generation=2,
        title="Demo",
        details={"site": "YouTube", "duration": "1:02"},
        format_options=[
            {"label": "Best (A+V)", "format": "bestvideo+bestaudio/best"},
            {"label": "Audio (MP3)", "format": "bestaudio"},
        ],
        transcript_options=None,
        whisper_models=["tiny", "small", "large-v3"],
        on_download=lambda card, fmt: calls.append(("download", fmt)),
        on_transcript=lambda card, track: calls.append(("transcript", track)),
        on_whisper=lambda card, model: calls.append(("whisper", model)),
        emit=emit,
    )
    calls: list = []
    kwargs.update(overrides)
    card = CardModel(**kwargs)
    card.calls = calls
    return card, emit


class TestCardModelControls(unittest.TestCase):
    def test_key_combines_generation_and_index(self) -> None:
        card, _emit = _card()
        self.assertEqual(card.key, "2:3")
        self.assertEqual(card.describe()["key"], "2:3")

    def test_selection_defaults_checked_and_programmatic_updates_are_silent(self) -> None:
        notified = []
        card, _emit = _card(on_selection_change=lambda c, value: notified.append(value))
        self.assertTrue(card.is_selected())
        card.set_selected(False)
        self.assertFalse(card.is_selected())
        self.assertEqual(notified, [])
        card.toggle_selected(True)
        self.assertEqual(notified, [True])

    def test_format_and_transcript_changes_write_through_callbacks(self) -> None:
        formats, tracks = [], []
        track = TranscriptTrack(source="subtitles", language="fr", label="French")
        card, emit = _card(
            transcript_options=[track],
            on_format_change=lambda c, value: formats.append(value),
            on_transcript_change=lambda c, value: tracks.append(value),
        )
        card.choose_format("bestaudio")
        card.choose_format("not-a-format")
        card.choose_transcript(1)
        card.choose_transcript(99)
        self.assertEqual(formats, ["bestaudio"])
        self.assertEqual(tracks, [track])
        self.assertEqual(emit.last("card")["format"], "bestaudio")
        self.assertEqual(emit.last("card")["transcript_choice"], 1)

    def test_transcript_options_list_subtitles_before_automatic_captions(self) -> None:
        tracks = [
            TranscriptTrack(source="subtitles", language="fr", label="French"),
            TranscriptTrack(source="subtitles", language="de", label="German"),
            TranscriptTrack(source="automatic captions", language="en", label="English"),
        ]
        card, _emit = _card(transcript_options=tracks)
        # Provider subtitles are curated; auto-translated captions are noisy and
        # can number in the hundreds, so they follow the real tracks.
        self.assertEqual(card.describe()["transcripts"], ["Auto-select", "French", "German", "English"])
        self.assertIsNone(card.selected_transcript)

    def test_default_selects_large_v3_when_present(self) -> None:
        card, _emit = _card()
        self.assertEqual(card.get_selected_whisper_model(), "large-v3")

    def test_default_selects_first_alias_when_large_v3_absent(self) -> None:
        card, _emit = _card(whisper_models=["base", "small"])
        self.assertEqual(card.get_selected_whisper_model(), "base")

    def test_whisper_callback_receives_selected_exact_alias(self) -> None:
        card, _emit = _card()
        card.choose_whisper_model("small")
        card.choose_whisper_model("medium")  # not probed: ignored
        card.request_whisper()
        self.assertEqual(card.calls, [("whisper", "small")])

    def test_empty_aliases_disable_only_whisper(self) -> None:
        for models in ([], None):
            card, _emit = _card(whisper_models=models)
            state = card.state()
            self.assertFalse(state["can_whisper"])
            self.assertTrue(state["can_download"])
            self.assertTrue(state["can_transcript"])
            card.request_whisper()
            self.assertEqual(card.calls, [])

    def test_transcript_unavailable_does_not_disable_whisper(self) -> None:
        card, _emit = _card(transcript_options=[])
        state = card.state()
        self.assertFalse(state["can_transcript"])
        self.assertTrue(state["can_whisper"])
        self.assertEqual(card.status, "No caption tracks")
        card.request_transcript()
        self.assertEqual(card.calls, [])

    def test_busy_disables_every_action_and_reenable_respects_availability(self) -> None:
        card, emit = _card(transcript_options=[])
        card.set_actions_enabled(False)
        state = emit.last("card")
        self.assertFalse(state["can_download"] or state["can_transcript"] or state["can_whisper"])
        card.set_actions_enabled(True)
        state = emit.last("card")
        self.assertTrue(state["can_download"])
        self.assertFalse(state["can_transcript"])
        self.assertTrue(state["can_whisper"])

    def test_download_uses_the_selected_format(self) -> None:
        card, _emit = _card()
        card.choose_format("bestaudio")
        card.request_download()
        self.assertEqual(card.calls, [("download", "bestaudio")])


class TestCardModelProgress(unittest.TestCase):
    def test_set_progress_is_determinate_and_clamped(self) -> None:
        card, emit = _card()
        card.set_progress_mode("indeterminate")
        card.set_progress(1.7)
        self.assertEqual(card.get_progress_mode(), "determinate")
        self.assertEqual(emit.last("card")["progress"], 1.0)

    def test_indeterminate_keeps_value_and_reset_returns_to_zero(self) -> None:
        card, emit = _card()
        card.set_progress(0.4)
        card.set_progress_mode("indeterminate")
        self.assertEqual(emit.last("card")["mode"], "indeterminate")
        card.reset_progress()
        self.assertEqual(emit.last("card")["mode"], "determinate")
        self.assertEqual(emit.last("card")["progress"], 0.0)

    def test_unsupported_progress_mode_raises(self) -> None:
        card, _emit = _card()
        with self.assertRaises(ValueError):
            card.set_progress_mode("sideways")

    def test_new_action_clears_previous_outputs(self) -> None:
        card, emit = _card()
        card.set_outputs(["C:/out/a.mp4"])
        self.assertEqual(emit.last("card")["outputs"], ["C:/out/a.mp4"])
        card.set_status("Downloading (Best (A+V))", state="downloading")
        self.assertEqual(emit.last("card")["outputs"], [])

    def test_destroyed_card_stops_publishing(self) -> None:
        card, emit = _card()
        card.destroy()
        before = len(emit.events)
        card.set_status("Complete", state="complete")
        self.assertFalse(card.is_alive())
        self.assertEqual(len(emit.events), before)

    def test_status_state_inference(self) -> None:
        self.assertEqual(infer_card_state("Downloading transcript"), "downloading")
        self.assertEqual(infer_card_state("Complete"), "complete")
        self.assertEqual(infer_card_state("Transcript failed"), "failed")
        self.assertEqual(infer_card_state("Ready"), "ready")


class TestBatchAndLogModels(unittest.TestCase):
    def test_batch_bar_disables_while_running(self) -> None:
        emit = _Recorder()
        bar = BatchBarModel(emit)
        bar.show()
        bar.set_enabled(True)
        bar.set_running(True)
        self.assertFalse(emit.last("batch")["enabled"])
        bar.set_running(False)
        self.assertTrue(emit.last("batch")["enabled"])
        bar.set_selection_summary(3, 9)
        self.assertEqual((emit.last("batch")["selected"], emit.last("batch")["total"]), (3, 9))
        bar.hide()
        self.assertFalse(emit.last("batch")["visible"])

    def test_log_levels(self) -> None:
        self.assertEqual(log_level("[Download] DownloadError: boom (Reason: x)"), "error")
        self.assertEqual(log_level("[yt-dlp] WARNING: slow"), "warn")
        self.assertEqual(log_level("Found 3 video(s)."), "info")
        emit = _Recorder()
        LogModel(emit).append("[Fetch] ValueError: nope")
        self.assertEqual(emit.last("log")["level"], "error")


if __name__ == "__main__":
    unittest.main()

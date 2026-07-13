"""Focused VideoCard Whisper action and progress-mode tests (headless)."""

from __future__ import annotations

import inspect
import unittest
from unittest import mock

import customtkinter as ctk

from link2vid.ui.components.video_card import VideoCard


class _CardHarness(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = ctk.CTk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.root.destroy()

    def setUp(self) -> None:
        self.cards: list[VideoCard] = []

    def tearDown(self) -> None:
        for card in self.cards:
            try:
                card.destroy()
            except Exception:
                pass
        self.cards.clear()

    def _make_card(self, **kwargs) -> VideoCard:
        card = VideoCard(self.root, title="Demo", **kwargs)
        self.cards.append(card)
        return card


class TestVideoCardWhisperControls(_CardHarness):
    def test_default_selects_small_when_present(self) -> None:
        card = self._make_card(whisper_models=["tiny", "base", "small", "medium"])
        self.assertEqual(card.get_selected_whisper_model(), "small")
        self.assertEqual(card.whisper_model_menu.get(), "small")
        self.assertTrue(card.whisper_available)

    def test_default_selects_first_alias_when_small_absent(self) -> None:
        card = self._make_card(whisper_models=["tiny", "base", "medium"])
        self.assertEqual(card.get_selected_whisper_model(), "tiny")
        self.assertEqual(card.whisper_model_menu.get(), "tiny")

    def test_aliases_are_exact_not_quality_presets(self) -> None:
        aliases = ["tiny", "small", "large-v3"]
        card = self._make_card(whisper_models=aliases)
        self.assertEqual(card.whisper_models, aliases)
        self.assertEqual(list(card.whisper_model_menu.cget("values")), aliases)

    def test_empty_aliases_disable_only_whisper_controls(self) -> None:
        on_download = mock.Mock()
        on_transcript = mock.Mock()
        on_whisper = mock.Mock()
        card = self._make_card(
            whisper_models=[],
            on_download=on_download,
            on_transcript=on_transcript,
            on_whisper=on_whisper,
        )
        self.assertFalse(card.whisper_available)
        self.assertEqual(card.get_selected_whisper_model(), "")
        self.assertEqual(str(card.whisper_button.cget("state")), "disabled")
        self.assertEqual(str(card.whisper_model_menu.cget("state")), "disabled")
        self.assertEqual(str(card.download_button.cget("state")), "normal")
        self.assertEqual(str(card.transcript_button.cget("state")), "normal")

        card.whisper_button.invoke()
        on_whisper.assert_not_called()
        card.download_button.invoke()
        on_download.assert_called_once()
        card.transcript_button.invoke()
        on_transcript.assert_called_once()

    def test_none_whisper_models_matches_empty_disable(self) -> None:
        card = self._make_card(whisper_models=None, on_whisper=mock.Mock())
        self.assertFalse(card.whisper_available)
        self.assertEqual(str(card.whisper_button.cget("state")), "disabled")

    def test_whisper_callback_receives_selected_exact_alias(self) -> None:
        on_whisper = mock.Mock()
        card = self._make_card(
            whisper_models=["tiny", "small", "medium"],
            on_whisper=on_whisper,
        )
        card._on_whisper_model_change("medium")
        card.whisper_button.invoke()
        on_whisper.assert_called_once_with(card, "medium")

    def test_transcript_unavailable_does_not_disable_whisper(self) -> None:
        on_whisper = mock.Mock()
        on_transcript = mock.Mock()
        card = self._make_card(
            transcript_options=[],
            whisper_models=["small"],
            on_transcript=on_transcript,
            on_whisper=on_whisper,
        )
        self.assertFalse(card.transcript_available)
        self.assertTrue(card.whisper_available)
        self.assertEqual(str(card.transcript_button.cget("state")), "disabled")
        self.assertEqual(str(card.whisper_button.cget("state")), "normal")
        self.assertEqual(str(card.whisper_model_menu.cget("state")), "normal")

        card.whisper_button.invoke()
        on_whisper.assert_called_once_with(card, "small")

    def test_set_actions_enabled_busy_disables_all_three_and_model_menu(self) -> None:
        card = self._make_card(
            whisper_models=["small", "medium"],
            on_download=mock.Mock(),
            on_transcript=mock.Mock(),
            on_whisper=mock.Mock(),
        )
        card.set_actions_enabled(False)
        self.assertEqual(str(card.download_button.cget("state")), "disabled")
        self.assertEqual(str(card.transcript_button.cget("state")), "disabled")
        self.assertEqual(str(card.transcript_button_label.cget("state")), "disabled")
        self.assertEqual(str(card.whisper_button.cget("state")), "disabled")
        self.assertEqual(str(card.whisper_model_menu.cget("state")), "disabled")

        card.set_actions_enabled(True)
        self.assertEqual(str(card.download_button.cget("state")), "normal")
        self.assertEqual(str(card.transcript_button.cget("state")), "normal")
        self.assertEqual(str(card.whisper_button.cget("state")), "normal")
        self.assertEqual(str(card.whisper_model_menu.cget("state")), "normal")

    def test_reenable_respects_transcript_and_whisper_availability(self) -> None:
        card = self._make_card(
            transcript_options=[],
            whisper_models=[],
            on_download=mock.Mock(),
            on_transcript=mock.Mock(),
            on_whisper=mock.Mock(),
        )
        card.set_actions_enabled(False)
        card.set_actions_enabled(True)
        self.assertEqual(str(card.download_button.cget("state")), "normal")
        self.assertEqual(str(card.transcript_button.cget("state")), "disabled")
        self.assertEqual(str(card.whisper_button.cget("state")), "disabled")
        self.assertEqual(str(card.whisper_model_menu.cget("state")), "disabled")

    def test_legacy_constructor_without_whisper_args_still_works(self) -> None:
        on_download = mock.Mock()
        on_transcript = mock.Mock()
        card = self._make_card(
            format_options=[("Best", "best")],
            transcript_options=None,
            on_download=on_download,
            on_transcript=on_transcript,
        )
        self.assertFalse(card.whisper_available)
        self.assertIsNone(card.on_whisper)
        self.assertEqual(str(card.download_button.cget("state")), "normal")
        self.assertEqual(str(card.transcript_button.cget("state")), "normal")
        self.assertEqual(str(card.whisper_button.cget("state")), "disabled")

        card.download_button.invoke()
        on_download.assert_called_once_with(card, "best")
        card.transcript_button.invoke()
        on_transcript.assert_called_once_with(card, None)


class TestVideoCardProgressModes(_CardHarness):
    def test_set_progress_remains_determinate_percent(self) -> None:
        card = self._make_card()
        card.set_progress(0.42)
        self.assertEqual(card.get_progress_mode(), "determinate")
        self.assertAlmostEqual(card.progress_bar.get(), 0.42, places=5)
        self.assertEqual(card.progress_label.cget("text"), "42%")
        self.assertFalse(card.progress_bar._loop_running)

    def test_indeterminate_uses_start_without_fake_percent(self) -> None:
        card = self._make_card()
        card.set_progress_mode("indeterminate")
        self.assertEqual(card.get_progress_mode(), "indeterminate")
        self.assertEqual(card.progress_bar._mode, "indeterminate")
        self.assertTrue(card.progress_bar._loop_running)
        self.assertEqual(card.progress_label.cget("text"), "")

    def test_switch_indeterminate_to_determinate_stops_animation(self) -> None:
        card = self._make_card()
        card.set_progress_mode("indeterminate")
        self.assertTrue(card.progress_bar._loop_running)
        card.set_progress_mode("determinate")
        self.assertFalse(card.progress_bar._loop_running)
        self.assertEqual(card.progress_bar._mode, "determinate")
        self.assertEqual(card.get_progress_mode(), "determinate")

    def test_set_progress_from_indeterminate_switches_cleanly(self) -> None:
        card = self._make_card()
        card.set_progress_mode("indeterminate")
        card.set_progress(0.75)
        self.assertEqual(card.get_progress_mode(), "determinate")
        self.assertFalse(card.progress_bar._loop_running)
        self.assertAlmostEqual(card.progress_bar.get(), 0.75, places=5)
        self.assertEqual(card.progress_label.cget("text"), "75%")

    def test_reset_progress_stops_and_returns_to_zero(self) -> None:
        card = self._make_card()
        card.set_progress_mode("indeterminate")
        card.reset_progress()
        self.assertEqual(card.get_progress_mode(), "determinate")
        self.assertFalse(card.progress_bar._loop_running)
        self.assertEqual(card.progress_bar._mode, "determinate")
        self.assertAlmostEqual(card.progress_bar.get(), 0.0, places=5)
        self.assertEqual(card.progress_label.cget("text"), "0%")

    def test_unsupported_progress_mode_raises(self) -> None:
        card = self._make_card()
        with self.assertRaises(ValueError):
            card.set_progress_mode("pulse")


class TestPrototypeContract(unittest.TestCase):
    def test_prototype_module_imports_and_exposes_run(self) -> None:
        from link2vid.ui import prototype

        self.assertTrue(callable(prototype.run))
        source = inspect.getsource(prototype)
        self.assertIn("whisper_models=", source)
        self.assertIn("on_whisper=", source)
        self.assertIn("set_progress_mode", source)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from doggybag.app.controller import AppController


class _Card:
    def __init__(self): self.selected = None
    def set_selected(self, value, *, notify=False): self.selected = bool(value)


class _Bar:
    def __init__(self): self.summary = None; self.enabled = None
    def set_selection_summary(self, selected, total): self.summary = (selected, total)
    def set_enabled(self, enabled): self.enabled = enabled


class TestBatchSelectionState(unittest.TestCase):
    def setUp(self):
        self.app = object.__new__(AppController)
        self.app.video_entries = [{} for _ in range(45)]
        self.app.selected_entry_indexes = set(range(45))
        self.app.card_index_by_card = {}
        self.app.card_by_index = {index: _Card() for index in range(20)}
        self.app.output_path = "C:\\out"
        self.app.is_fetching = False
        self.app.batch_run = None
        self.app.batch_bar = _Bar()
        for name in (
            "_refresh_batch_bar", "handle_card_selection_change",
            "select_all_entries", "select_no_entries", "_selected_indexes_ordered",
        ):
            setattr(self.app, name, getattr(AppController, name).__get__(self.app))
        self.app.card_index_by_card = {
            card: index for index, card in self.app.card_by_index.items()
        }

    def test_default_all_and_card_toggle(self):
        self.app._refresh_batch_bar()
        self.assertEqual(self.app.batch_bar.summary, (45, 45))
        card = self.app.card_by_index[3]
        self.app.handle_card_selection_change(card, False)
        self.assertNotIn(3, self.app.selected_entry_indexes)
        self.assertEqual(self.app.batch_bar.summary, (44, 45))

    def test_select_none_then_one_then_all(self):
        self.app.select_no_entries()
        self.assertEqual(self.app.selected_entry_indexes, set())
        card = self.app.card_by_index[7]
        self.app.handle_card_selection_change(card, True)
        self.assertEqual(self.app.selected_entry_indexes, {7})
        self.app.select_all_entries()
        self.assertEqual(len(self.app.selected_entry_indexes), 45)

    def test_newly_rendered_card_syncs_to_full_selection_model(self):
        self.app.select_no_entries()
        new_card = _Card()
        self.app.card_by_index[25] = new_card
        self.app.selected_entry_indexes.add(25)
        self.app._refresh_batch_bar()
        self.assertTrue(new_card.selected)
        self.assertEqual(self.app.batch_bar.summary, (1, 45))


if __name__ == "__main__":
    unittest.main()

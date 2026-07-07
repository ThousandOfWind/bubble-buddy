import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copilot_voice_shell import i18n


class I18nTest(unittest.TestCase):
    def tearDown(self):
        i18n.set_language("zh")  # restore default for other tests

    def test_lookup_zh_en(self):
        i18n.set_language("zh")
        self.assertEqual(i18n.t("btn.save"), "保存")
        i18n.set_language("en")
        self.assertEqual(i18n.t("btn.save"), "Save")

    def test_unknown_key_returns_key(self):
        self.assertEqual(i18n.t("does.not.exist"), "does.not.exist")

    def test_format_placeholders(self):
        i18n.set_language("en")
        self.assertEqual(i18n.t("label.hotkey", hotkey="F9"), "Hotkey: F9")

    def test_format_missing_placeholder_is_safe(self):
        # Missing kwargs must not raise; returns the unformatted template.
        self.assertIsInstance(i18n.t("label.hotkey"), str)

    def test_resolve_language(self):
        self.assertEqual(i18n.resolve_language("zh"), "zh")
        self.assertEqual(i18n.resolve_language("en"), "en")
        self.assertIn(i18n.resolve_language("auto"), i18n.SUPPORTED)
        self.assertIn(i18n.resolve_language(None), i18n.SUPPORTED)

    def test_set_language_returns_resolved(self):
        self.assertEqual(i18n.set_language("en"), "en")
        self.assertEqual(i18n.current_language(), "en")

    def test_every_entry_has_both_languages(self):
        for key, entry in i18n.STRINGS.items():
            for lang in i18n.SUPPORTED:
                self.assertIn(lang, entry, f"{key} missing {lang}")
                self.assertTrue(entry[lang], f"{key}[{lang}] is empty")

    def test_settings_catalog_fully_translated(self):
        """Every settings section id and field key must have a catalog entry in
        both languages, so no UI label falls back to a raw key."""
        from copilot_voice_shell.qt_overlay import _SETTINGS_CATEGORIES, _field_label

        for lang in i18n.SUPPORTED:
            i18n.set_language(lang)
            for section_id, fields in _SETTINGS_CATEGORIES:
                self.assertNotEqual(
                    i18n.t(f"settings.section.{section_id}"),
                    f"settings.section.{section_id}",
                    f"section {section_id} not translated for {lang}",
                )
                for key, _kind, _opts in fields:
                    label = _field_label(key)
                    self.assertFalse(
                        label.startswith("settings.field."),
                        f"field {key} not translated for {lang}",
                    )


if __name__ == "__main__":
    unittest.main()

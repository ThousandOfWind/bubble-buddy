"""Offline consistency checks for the packaged install/account support guidance."""
import json
import re
import unittest
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1] / "skills" / "bubble-buddy"
REFS = ROOT / "references"


class SupportSkillTest(unittest.TestCase):
    def setUp(self):
        self.guide = json.loads((REFS / "install-guide.json").read_text(encoding="utf-8"))
        self.schema = json.loads((REFS / "config.schema.json").read_text(encoding="utf-8"))["keys"]

    def test_intake_confirms_platform_route_provider_and_both_components(self):
        intake = self.guide["intake"]
        self.assertEqual(intake["required_before_download_config_or_login"],
                         ["target_platform", "solution_route", "transcription", "polish"])
        self.assertTrue(intake["require_code_agent_when_selected"])
        self.assertTrue(intake["reuse_confirmed_answers"])
        questions = {q["id"]: q for q in intake["questions"]}
        self.assertEqual(list(questions)[:3], ["target_platform", "solution_route", "code_agent"])
        self.assertEqual({o["choice"] for o in questions["solution_route"]["options"]},
                         {"azure", "local", "code_agent"})
        self.assertEqual({o["choice"] for o in questions["target_platform"]["options"]},
                         {"windows", "macos_apple_silicon", "macos_intel"})
        self.assertTrue(questions["code_agent"]["when"])
        for key in ("target_platform", "solution_route", "code_agent"):
            self.assertTrue(questions[key]["ask_zh"])

    def test_capabilities_never_advertise_copilot_audio_or_verified_codex_audio(self):
        capabilities = self.guide["code_agent_support"]
        self.assertIsNone(capabilities["copilot"]["transcription_backend"])
        self.assertFalse(capabilities["copilot"]["verified_audio_transcription"])
        self.assertTrue(capabilities["copilot"]["needs_separate_transcriber"])
        self.assertEqual(capabilities["copilot"]["polish_engine"], "copilot")
        self.assertFalse(capabilities["codex"]["verified_audio_transcription"])
        self.assertTrue(capabilities["codex"]["experimental"])
        self.assertTrue(capabilities["codex"]["requires_explicit_opt_in"])
        self.assertIsNone(capabilities["codex"]["polish_engine"])
        for provider in ("claude_code", "other"):
            self.assertFalse(capabilities[provider]["supported_account_backend"])

    def test_copilot_local_recipes_match_platform_and_require_full(self):
        backend_by_platform = self.guide["intake"]["local_backend_by_platform"]
        self.assertEqual(backend_by_platform, {"windows": "faster-whisper",
                                             "macos_apple_silicon": "mlx", "macos_intel": "faster-whisper"})
        for recipe in self.guide["copilot_local_recipes"]:
            self.assertEqual(recipe["edition"], "full")
            self.assertEqual(recipe["config"]["backend"], backend_by_platform[recipe["platform"]])
            self.assertEqual(recipe["config"]["polish_engine"], "copilot")
            self.assertNotIn("azure", recipe["config"])
            if recipe["platform"] == "macos_intel":
                self.assertTrue(recipe["requires_compatible_build"])

    def test_all_install_config_examples_use_actual_schema_values(self):
        configs = [r["config"] for r in self.guide["copilot_local_recipes"]]
        for question in self.guide["intake"]["questions"]:
            configs.extend(o["config"] for o in question["options"] if "config" in o)
        for config in configs:
            for key, value in config.items():
                self.assertIn(key, self.schema)
                if "enum" in self.schema[key]:
                    self.assertIn(value, self.schema[key]["enum"])
        for key in self.guide["account_setup"]["copilot_profile_from_schema"]:
            self.assertIn(key, self.schema)
            self.assertFalse(self.schema[key]["secret"])

    def test_new_setup_guides_work_inside_an_installed_skill_folder(self):
        for relative in ("SKILL.md", "references/install.md", "references/accounts.md",
                         "references/config.md", "references/troubleshooting.md", "references/usage.md"):
            path = ROOT / relative
            text = path.read_text(encoding="utf-8")
            for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
                if urlparse(target).scheme or target.startswith("#"):
                    continue
                resolved = (path.parent / target.split("#", 1)[0]).resolve()
                with self.subTest(file=relative, link=target):
                    self.assertTrue(resolved.is_relative_to(ROOT.resolve()), "reference escaped the packaged skill")
                    self.assertTrue(resolved.exists(), "packaged reference is missing")

    def test_approved_index_source_guidance_is_opt_in_and_does_not_ship_code(self):
        source = self.guide["source_install"]
        self.assertTrue(source["requires_source_checkout"])
        self.assertFalse(source["helper_shipped_in_skill"])
        self.assertFalse(source["dependencies_only_is_ready_app"])
        self.assertTrue(source["normal_public_workflow_unchanged"])
        self.assertEqual(source["post_install_uv_run_flag"], "--no-sync")
        self.assertEqual(source["python_minimum"], "3.10")
        reference = REFS / source["approved_index_reference"]
        self.assertTrue(reference.is_file())
        text = reference.read_text(encoding="utf-8")
        for required in ("UV_DEFAULT_INDEX", "--sync-existing", "--dependencies-only", "--no-sync"):
            self.assertIn(required, text)
        for relative in ("install.md", "usage.md"):
            self.assertIn("approved-index.md", (REFS / relative).read_text(encoding="utf-8"))
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
            if not urlparse(target).scheme and not target.startswith("#"):
                resolved = (reference.parent / target.split("#", 1)[0]).resolve()
                self.assertTrue(resolved.is_relative_to(ROOT.resolve()))
                self.assertTrue(resolved.is_file())

    def test_azure_login_instructions_distinguish_qt_and_native_controls(self):
        setup = self.guide["azure_setup"]
        self.assertEqual(setup["signin_button"]["frontend"], "qt_desktop")
        native = setup["native_macos_account_control"]
        self.assertEqual(native["label_en"], "Account")
        self.assertTrue(native["requires_expanded_overlay"])
        self.assertTrue(native["hidden_when_no_provider_required"])
        self.assertTrue(native["remains_available_after_signin"])

    def test_error_catalog_routes_account_messages_by_provider(self):
        entries = json.loads((REFS / "error-catalog.json").read_text(encoding="utf-8"))["entries"]
        entries = {e["id"]: e for e in entries}
        self.assertEqual(set(entries["code-agent-auth"]["provider_scope"]), {"copilot", "codex"})
        self.assertEqual(entries["auth-failure"]["provider_scope"], ["azure"])
        messages = json.loads((REFS / "messages.json").read_text(encoding="utf-8"))["messages"]
        for entry in entries.values():
            self.assertTrue((REFS / entry["runbook"]).is_file())
            for key in entry.get("messages", []):
                self.assertIn(key, messages)
            for key in entry["related_config"]:
                self.assertIn(key, self.schema)


if __name__ == "__main__":
    unittest.main()

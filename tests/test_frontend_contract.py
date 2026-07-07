import unittest

from copilot_voice_shell.frontend_contract import (
    FrontendFeature,
    FrontendState,
    MAC_NATIVE_CAPABILITIES,
    QT_CAPABILITIES,
    Stage,
)
from copilot_voice_shell.frontend_bubble import BubbleAnchor, BubbleKind, make_bubble


class FrontendContractTest(unittest.TestCase):
    def test_state_normalizes_hotkey_session_updates(self) -> None:
        state = FrontendState(hotkey="f9")
        state.apply({
            "stage": "done",
            "plain_text": "raw",
            "rephrased_text": "polished",
            "pasted": True,
            "target_app": "Code",
        })

        self.assertEqual(state.stage, Stage.DONE)
        self.assertEqual(state.raw_text, "raw")
        self.assertEqual(state.polished_text, "polished")
        self.assertTrue(state.pasted)
        self.assertEqual(state.snapshot()["target_app"], "Code")

    def test_capabilities_express_window_engine_gap(self) -> None:
        self.assertTrue(MAC_NATIVE_CAPABILITIES.supports(FrontendFeature.FULLSCREEN_OVERLAY))
        self.assertFalse(QT_CAPABILITIES.supports(FrontendFeature.FULLSCREEN_OVERLAY))
        self.assertTrue(QT_CAPABILITIES.supports(FrontendFeature.SETTINGS))
        self.assertTrue(MAC_NATIVE_CAPABILITIES.supports(FrontendFeature.SETTINGS))
        self.assertTrue(MAC_NATIVE_CAPABILITIES.supports(FrontendFeature.AZURE_SIGN_IN))

    def test_bubble_contract_selects_anchor_and_accent(self) -> None:
        speech = make_bubble("hello", kind=BubbleKind.SPEECH, stage="done")
        context = make_bubble("Code", kind=BubbleKind.CONTEXT, stage="recording")

        self.assertEqual(speech.anchor, BubbleAnchor.PET)
        self.assertEqual(context.anchor, BubbleAnchor.APP_BADGE)
        self.assertEqual(speech.accent, "#39D98A")
        self.assertEqual(context.accent, "#FF4D67")


if __name__ == "__main__":
    unittest.main()

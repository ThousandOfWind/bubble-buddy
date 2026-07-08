# Frontend design contract

Bubble Buddy has multiple window engines, but it should feel like one product.
Qt is the richer cross-platform renderer; AppKit is required on macOS for
fullscreen Spaces. The visual language and behavior are shared.

## Engine boundary

- Shared: recording lifecycle, transcription, polish, copy/paste/submit,
  focus/context extraction, config, i18n, stage names, bubble semantics, and
  visual tokens.
- Engine-specific: native windowing, layout primitives, animation
  implementation, and fullscreen/topmost mechanics.
- Shared modules:
  - `frontend_contract.py`: stable stages, normalized frontend state, feature
    capabilities.
  - `frontend_style.py`: colors, glow intensities, button dimensions.
  - `frontend_bubble.py`: speech/context/greeting bubble semantics.

## Pet/orb

- The pet body color is stable periwinkle blue (`ORB_BODY`); state should not
  recolor the whole pet.
- Idle state is calm: breathing/blinking is OK, but glow should be subtle
  (`GLOW_ALPHA_IDLE`). A strong aura in idle reads as visually noisy.
- Recording/streaming is the only state with a strong pulsing glow and visible
  recording energy.
- Working/transcribing uses a softer violet pulse.
- Done/error may flash briefly, then settle back.
- Native and Qt may render the face differently, but the proportions should stay
  cute and colorful; avoid grey/desaturated pet visuals.

## Collapsed mode

- Desktop starts collapsed by default (`start_collapsed=true`).
- The collapsed window must reserve enough transparent bounds for the largest
  recording glow/ripple so shadows are never clipped.
- App badge/telephone cord appears only while recording/streaming.
- The app badge is an icon when available, otherwise a one-letter fallback.

## Bubbles

- Speech/greeting bubbles anchor to the pet.
- Context bubbles anchor to the app badge.
- Bubbles have a rounded body, tail, stage/category accent, and bounded lifetime.
- Context remains visible during recording/streaming and should not be hidden by
  speech/greeting timers.
- Speech bubbles update from live streaming preview when available, then show the
  final transcript/polished text.

## Expanded mode

- Primary button row should match across engines: start, stop, shrink, quit.
- Additional actions such as settings, Azure sign-in, relaunch, and history may
  exist, but should not crowd the primary action row unless Qt also exposes them
  there.
- Text panels should use the shared dark card/input colors.

## Platform safety

- macOS AppKit UI updates and topmost/window operations must stay on the main
  AppKit thread when possible.
- macOS paste/submit uses `osascript` rather than `pynput.keyboard.Controller`
  to avoid input-source crashes from background threads.
- Global hotkey callbacks must be short; they should dispatch work off the event
  tap thread.

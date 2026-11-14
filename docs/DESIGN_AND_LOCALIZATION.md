# Muse design and localization foundation

> Phase 1 architecture/evaluation retained for reference. Current implementation and deviations are documented in [PHASE_2_REPORT.md](PHASE_2_REPORT.md).
This document applies the existing Muse standards; it does not create additional
Muse-wide rules. No UI has been implemented or visually tested in Phase 1.

## One focused workflow

The main window shows one selected video, a preview and a clear **Convert** action,
followed by **Copy to SD card**. The user chooses the card's mounted folder. Device
connection/playback controls remain visible but disabled with an explanation until a
compatible handshake. Serial upload is a secondary explicit option. No timeline
tracks, editing projects, music recognition, effects library or account system.

Wide layout: preview/workflow beside a small device/playback panel. Narrow layout:
stack these sections, keep primary actions reachable and move infrequent diagnostics
into a collapsible section. Avoid a permanent navigation rail for this single workflow.
Transform controls are grouped into one expandable panel, with direct numeric values,
aspect lock, reset/center and simple quarter-turn buttons. Keep normal usage to select,
convert, copy, connect/control. No control is presented as working until implemented.

Always distinguish **Encoding FPS**, **Target FPS**, **Actual FPS**. The requested
transform can appear pending, but the main device preview uses the confirmed/applied
state. A disconnected preview is labeled with last-known state and freshness.
Unsupported sensors show an unavailable label/reason. Optional experimental features
are clearly marked and disabled until the device advertises them.

## Existing tokens and components to adopt

Inspected `../muse/src/styles/tokens.css`,
`../musej/src/lib/design/tokens.css`, and the Muse Downloader manifest/architecture.
Use the established **Material 3 Expressive** roles and Manrope Variable typography
with local Latin/Latin Extended font assets (Turkish glyphs). Adopt the core token
values into one local `src/lib/design/tokens.css` in Phase 2; document only purposeful
desktop density/layout adjustments, not a new brand palette.

| Token family | Established examples / use |
| --- | --- |
| Color | Light primary `#006a60`, dark primary `#90d5c4`; onPrimary/container, surface, outline and error roles from the existing themes |
| Type | Manrope Variable, Segoe UI/sans-serif fallback; body/title/label hierarchy and centralized line heights |
| Space | 4, 8, 12, 16, 20, 24, 32 px-derived scale; consistent panel/control gaps |
| Shape | Existing 8/16/24/32 px-derived corner roles; full radius only where appropriate |
| Motion | Existing short 160 ms / standard 240 ms easing; reduced-motion alternatives |
| Size | Existing ≥44 px control target and visible 3 px focus token |

Use filled buttons for the one primary action, tonal/outlined buttons for secondary
actions, consistent sliders/text inputs, small status surfaces and a single outlined
icon family. Check existing sibling components before adapting suitable controls;
do not import downloader-specific queue/navigation workflows. Keep labels for
ambiguous icons, real disabled/loading/error states and native file/folder dialogs.

Theme choices: system, light, dark; stored independently from language. All semantic
roles have light/dark values. Never communicate connection/error/performance through
color alone. Text scales without clipped controls; use responsive reflow rather than
fixed overflow. Animation is feedback only, with no continuous decorative loops.

## Localization contract

**Turkish (`tr`) is the first-run default regardless of OS locale.** A valid saved
preference wins; English (`en`) is always selectable. Language/theme settings persist
independently and switching preserves selected video, jobs, playback state and focus.
Technical reports, questions and documentation are English. Translated application
catalog contents are the intended exception to English-only documentation.

`localization/tr.json` and `en.json` are **seed catalogs**, not a completed runtime
localization implementation. Their flat stable keys establish the foundation; Phase
2 integrates a typed localization service and expands them with every actual UI string.
Generate/infer the TypeScript message-key type from the default catalog. A single
`t(key, parameters)` service handles labels, accessible names, tooltips, dialogs,
status, notifications and translated errors. Validate matching interpolation keys
and complete key parity. Use explicit plural entries/Intl.PluralRules rather than
concatenated translated fragments. Dates/numbers/units use `Intl` with `tr-TR` or
`en-US`. Keep technical identifiers, filenames and user titles unchanged/escaped.

Missing keys fail development/build checks; production uses a known catalog fallback
and logs a bounded diagnostic, never a fake completed translation. The frontend must
not expose English Rust error text as the main error. Map stable code plus arguments
to localized messages; optional raw FFmpeg diagnostics live in a separate details
view. Store no translated strings in the wire protocol or container.

Firmware screen copy also uses a compact keyed tr/en catalog if screen status text
is implemented. First boot Turkish; runtime language selection can be volatile.
There is no external translation API or font request.

## Accessibility and state acceptance

Test both languages and themes at narrow/wide windows, 200% text and high-DPI scale.
Every input has a visible label, keyboard operation and focus; preview drag is also
possible through numeric X/Y controls. Sliders have accessible values/units and do not
require pointer-only operation. Do not announce every 10 Hz frame update to assistive
technology; announce state changes/errors and throttle meaningful progress.

Required states include no video, probing/converting, cancellation, invalid/unsupported
input, no FFmpeg, no device, unsupported firmware, stale telemetry, no/removed/full/
read-only card, transfer retry, seek pending, insufficient heap and unavailable sensors.
Explain the next useful action in localized text. Do not show synthetic telemetry in
the main product UI; any simulator is explicitly labeled and uses test-only data.

Performance, reduced motion, text overflow, keyboard tab order, visible focus and
screen-reader names need actual Phase 2/3 review. No contrast, GUI accessibility or
responsive behavior is claimed as tested by documentation alone.

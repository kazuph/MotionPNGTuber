# Dokochan Tomari Guruguru Agent Rules

This directory contains character-facing Dokochan/Tomari guruguru assets. Treat the character image as the product, not as disposable test material.

## What This Directory Is For

This directory is for building a browser-based "guruguru" avatar: a character who appears to look toward the pointer by switching among pre-rendered direction images.

The core visual asset is a set of 5x5 character sheets:

- Each sheet contains 25 drawings of the same character.
- The 25 drawings represent gaze/head-direction variations: up/down/left/right/center and intermediate directions.
- The browser runtime selects one cell from the 5x5 sheet to make the character appear to follow the pointer.
- Mouth movement is represented by separate sheets for different mouth states.
- Blinking is represented by separate sheets where only the eyes are closed.

Upstream `rotejin/tomari-guruguru` generates and manages these as complete 5x5 sheets. Do not generate 25 separate images and try to assemble them into a sheet.

The complete runtime needs six 5x5 sheets:

1. Eyes open, mouth closed.
2. Eyes open, mouth half-open.
3. Eyes open, mouth open.
4. Eyes closed, mouth closed.
5. Eyes closed, mouth half-open.
6. Eyes closed, mouth open.

These are sometimes named with short letters in scripts and filenames:

- `A_目開け_口とじ.png` means "eyes open, mouth closed".
- `B_目開け_口中間.png` means "eyes open, mouth half-open".
- `C_目開け_口開け.png` means "eyes open, mouth open".
- `D_目閉じ_口とじ.png` means "eyes closed, mouth closed".
- `E_目閉じ_口中間.png` means "eyes closed, mouth half-open".
- `F_目閉じ_口開け.png` means "eyes closed, mouth open".

Do not write new instructions that only say "A/B/C" or "D/E/F". Always include the human meaning of those sheets, because future agents will not share the current conversation context.

## Prime Directive

Do not degrade, parody, simplify, redraw, or approximate the character. If the result does not preserve the character identity and visual dignity, it is a failure even when numeric checks pass.

Never present an artifact as complete unless it has been opened and visually inspected by the agent in the same workflow.

## Failures That Must Not Repeat

- Do not claim completion from coordinate metrics alone.
- Do not call an open-eye sheet "approved" while green background residue remains on the character edge.
- Do not report success before opening the actual generated image files.
- Do not open a Vite/React deliverable through `file://` and call it checked. Run a local server and open the HTTP URL.
- Do not create blink frames by erasing open eyes and drawing replacement lines.
- Do not hand-draw, procedurally draw, Pillow-draw, SVG-draw, canvas-draw, inpaint-scribble, or patch fake eyes.
- Do not treat alpha, bounding box, or centroid equality as proof of visual quality.
- Do not prioritize zero movement over character preservation.
- Do not use broad RGB transfer from a different generated face if it causes identity drift, hair drift, eye drift, or expression drift.
- Do not use green chroma-key backgrounds for still character sheets when transparent or masked image-to-image is available.
- Do not leave generated backgrounds in the character sheet. The character sheet must be transparent.
- Do not create GIFs or derived demos from unapproved still assets.
- Do not send outputs to Slack or other external destinations until the user has approved the visual artifact.
- Do not keep broken rejected assets in the deliverable tree.
- Do not call deleted or rejected outputs "成果物".
- Do not continue generating variants after the user points out character damage.
- Do not argue with the user's visual judgment. If the user says it is broken, treat it as broken.

## Required Recovery Posture

When a visual issue is reported:

1. Answer directly and acknowledge the issue.
2. Stop producing new derived artifacts.
3. Open the exact file the user is talking about.
4. Identify whether the file is source, intermediate, or final output.
5. Remove rejected final outputs when the user asks.
6. Do not regenerate until the intended pipeline is agreed.

## Guruguru Generation Pipeline To Agree Before Work

No further Dokochan guruguru generation may start until the user agrees to the pipeline. The intended pipeline is:

0. Resolve the base-sheet edge problem before any blink work:
   - The current open-eye sheets were produced from green-background images and may contain green residue around hair and character edges.
   - A sheet with visible green residue is not an approved base sheet.
   - First create or obtain clean open-eye 5x5 sheets with no green edge contamination.
   - Follow upstream first: generate a complete 5x5 sheet on a simple gray background, then perform background transparency processing.
   - Do not use green chroma-key generation for still character sheets.
   - Do not "fix" character edges with crude chroma-key erosion if it damages hair, bangs, line art, or the character silhouette.
   - Open the cleaned open-eye sheets and a warm-background edge-check contact sheet before calling them usable.
1. After the green-edge problem is resolved, start from the three approved open-eye 5x5 direction sheets:
   - `A_目開け_口とじ.png`: eyes open, mouth closed.
   - `B_目開け_口中間.png`: eyes open, mouth half-open.
   - `C_目開け_口開け.png`: eyes open, mouth open.
2. Create the three blink sheets by reference image-to-image generation only:
   - `D_目閉じ_口とじ.png`: generated from `A_目開け_口とじ.png` by closing only the eyes.
   - `E_目閉じ_口中間.png`: generated from `B_目開け_口中間.png` by closing only the eyes.
   - `F_目閉じ_口開け.png`: generated from `C_目開け_口開け.png` by closing only the eyes.
3. The image-to-image mask must cover only the eye areas. It must not cover hair, bangs, face outline, cheeks, mouth, hoodie, body, or the transparent background.
4. Preserve the original character identity, hair, face outline, head pose, mouth expression, hoodie, body, canvas size, and transparent background.
5. Do not use local fake-eye construction, drawn lines, procedural patches, or manual eye replacement.
6. Prefer transparent input/output. If a temporary flat background is unavoidable, use it only as an internal generation aid and remove it before any deliverable review.
7. Keep every request prompt, mask, reference image, and output image next to the generated result for reproducibility.
8. Before slicing into runtime images, create a contact sheet that compares:
   - the three open-eye sheets: eyes open with mouth closed, half-open, and open.
   - the three blink sheets: eyes closed with mouth closed, half-open, and open.
9. Open the contact sheet and the center cell of each blink sheet for visual inspection.
10. Only after visual approval, slice the sheets into runtime assets.
11. Verify coordinates after visual approval:
    - canvas size unchanged
    - alpha silhouette unchanged unless explicitly approved
    - no face or hair drift
    - no background residue
12. Run the browser deliverable through the local dev server, not `file://`.
13. Open the running HTTP deliverable in Chrome.
14. Report only the artifacts that were actually opened and checked.

## Acceptance Criteria

A blink generation attempt is acceptable only when all of the following are true:

- The character still looks like the approved Dokochan/Tomari asset.
- The eyes are naturally closed in the same art style.
- Hair, bangs, accessories, face outline, hoodie, and pose are not regenerated or distorted.
- The transparent background remains clean.
- The open-eye sheets and blink sheets are visually compared before runtime integration.
- The runtime is opened through HTTP and checked as a working deliverable.
- The user has approved the visual result.

## Current State Warning

Previous fake blink outputs were rejected and deleted. Do not recreate the `eye-lines` blink method or any equivalent local approximation. The next attempt must start from agreed reference image-to-image generation.

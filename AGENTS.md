# MotionPNGTuber Agent Notes

## Language
- Think and report in Japanese.

## Dokochan / VTuber Mouth Asset Workflow
- For Dokochan-style VTuber mouth sprites, do not hand-draw mouths with Pillow, SVG, canvas, or simple geometric code. That approach produced the wrong taste and is NG for this project.
- Use the image generation tool to create an `n x m` mouth sprite sheet first, then cut the sheet into sprite PNGs.
- Required current layout for Dokochan VTuber mouths:
  - Rows: `joy`, `anger`, `sad`, `surprise`
  - Columns: `closed`, `small`, `half`, `open`, `wide`, `e`, `u`
  - Output folders: `assets/dokochan_vtuber/mouth/{joy,anger,sad,surprise}/`
- Prompt the image tool for a strict grid, isolated mouth sprites only, no face, no labels, no grid lines, and a flat chroma-key background such as `#00ff00`.
- After generation:
  1. Copy the generated sheet into `assets/dokochan_vtuber/generated_mouth_sheet/`.
  2. Remove the chroma-key background with the imagegen `remove_chroma_key.py` helper.
  3. Cut the transparent sheet by row and column.
  4. Save each cell as a `128x128` RGBA PNG under the emotion folder.
  5. Do not leave legacy mouth PNGs directly under `assets/dokochan_vtuber/mouth/`, because that creates an unwanted `Default` mouth set.

## Motion Source Rule
- MotionPNGTuber motion must come from generated loop videos. Do not invent local pseudo-motion with OpenCV, Pillow, CSS, canvas, sprite transforms, procedural background animation, manual frame warping, or other code-side animation tricks.
- If the user asks for motion, use the requested video generation model or the already generated video asset, then run the normal MotionPNGTuber mouth tracking and mouthless-video pipeline on that video.
- Do not split a scene into locally animated background and character layers unless the user explicitly requests that exact implementation and approves the generated visual assets first.
- Do not compensate for poor generated video by adding code-side "creative" animation. Regenerate the video instead.
- The agent must assume it has weak visual/art direction taste for this project. When visual quality matters, show the generated still/video to the user for approval before building more pipeline on top of it.
- Past failure to avoid: locally fabricated `integrated_layers/` and `split_layers/` outputs with hand-made background motion were rejected and must not be recreated.

## Seedance 2.0 Layered Video Workflow
- When the requested output is a layered Dokochan VTuber sample, create motion with Seedance 2.0 video generation, not with local animation code.
- Required layered flow:
  1. Generate a background-only loop video with Seedance 2.0.
  2. Generate character-only loop videos with Seedance 2.0 on a flat chroma-key green background, one per emotion: `joy`, `anger`, `sad`, `surprise`.
  3. Composite the generated character videos over the generated background video only as a chroma-key merge. Do not add new motion during compositing.
  4. Run mouth tracking on the composited final videos.
  5. Generate mouthless videos from those final composited videos.
  6. Launch the runtime with Mac microphone input and emotion buttons ordered `joy`, `anger`, `sad`, `surprise`.
- Store generated-layer artifacts separately from old single-shot videos, for example under `assets/dokochan_vtuber/seedance_layers/`.
- Keep request/result JSON next to each Seedance output so the generation can be reproduced.
- If Seedance API is unavailable, quota-limited, or payment-blocked, stop and report that exact blocker. Do not substitute local pseudo-motion or a different video model.

## Dokochan Surprise Mouth Erase Rule
- The surprise Seedance clip has a baked-in open mouth. The normal patch-normalized clean-plate erase and the `skin_*` solid/skin-color fill candidates were rejected. Do not use them again for this case.
- The accepted fix is full-frame inpaint on every frame using a tightened mouth-only track:
  1. Start from the detector output `mouth_track_surprise.npz`, or from a freshly copied calibrated track before it has been replaced by the tightened version.
  2. Tighten the detector quad to the mouth only so it does not include the nose or jaw.
  3. Shrink only the bottom edge upward by 5% to avoid touching the chin line.
  4. Use the `ff_b_ellipse` style mask: ellipse, `sx=0.96`, `sy=0.90`, `yoff=-0.02`, dilate `5`, inpaint radius `6.0`.
  5. Save the tightened track as `mouth_track_surprise_mouth_only_bottom95_calibrated.npz`.
  6. Copy that tightened track to `mouth_track_surprise_calibrated.npz` for runtime lip placement.
  7. Save the full-frame inpainted video as `loop_surprise_mouthless.mp4`.
- The reproducible command is:
  - `uv run python tools/erase_surprise_mouth_fullframe.py --video assets/dokochan_vtuber/seedance_layers/composited/loop_surprise.mp4 --track assets/dokochan_vtuber/seedance_layers/composited/mouth_track_surprise.npz --out assets/dokochan_vtuber/seedance_layers/composited/loop_surprise_mouthless.mp4 --out-track assets/dokochan_vtuber/seedance_layers/composited/mouth_track_surprise_mouth_only_bottom95_calibrated.npz`
- `tools/dokochan_seedance_layers.py analyze --emotions surprise` must use the same full-frame inpaint path. Do not regress it back to `erase_mouth_offline.py` for surprise.

## Mouth Asset Verification
- Do not judge mouth sprites on a black/transparent contact sheet alone.
- Always create and open a face-overlay contact sheet using the actual Dokochan video frame, mouth track, and warped mouth sprites.
- The accepted verification style is like:
  - rows: `joy / anger / sad / surprise`
  - columns: `closed / small / half / open / wide / e / u`
  - each cell shows the mouth composited onto Dokochan's actual face.
- The current known-good reference output is:
  - `assets/dokochan_vtuber/mouth_emotion_overlay_contact_imagegen_cut.jpg`

## Runtime Requirements For Emotion Mouths
- Emotion switching must switch the background loop video, mouth track, and mouth sprite set together.
- The Dokochan VTuber runtime uses:
  - `assets/dokochan_vtuber/loop_<emotion>_mouthless.mp4`
  - `assets/dokochan_vtuber/mouth_track_<emotion>_calibrated.npz`
  - `assets/dokochan_vtuber/mouth/<emotion>/`
- The runtime mouth state set includes `closed`, `small`, `half`, `open`, `wide`, `e`, and `u`; do not regress it back to only five states.

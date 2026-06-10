from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path

import cv2
import numpy as np
from loop_lipsync_runtime_patched_emotion_auto import (
    classify_mouth_level_with_hysteresis,
    load_wav_mono_float32,
    resolve_emotion_auto_target,
    soften_mouth_shape_for_emotion,
    stabilize_mouth_shape,
)
from motionpngtuber.lipsync_core import BgVideo


class MouthLevelHysteresisTests(unittest.TestCase):
    def test_closed_state_waits_for_deadband_before_opening(self):
        self.assertEqual(
            classify_mouth_level_with_hysteresis(0.33, 0.30, 0.52, "closed"),
            "closed",
        )
        self.assertEqual(
            classify_mouth_level_with_hysteresis(0.34, 0.30, 0.52, "closed"),
            "half",
        )

    def test_half_state_waits_for_open_deadband(self):
        self.assertEqual(
            classify_mouth_level_with_hysteresis(0.55, 0.30, 0.52, "half"),
            "half",
        )
        self.assertEqual(
            classify_mouth_level_with_hysteresis(0.56, 0.30, 0.52, "half"),
            "open",
        )

    def test_open_state_needs_margin_before_falling_back(self):
        self.assertEqual(
            classify_mouth_level_with_hysteresis(0.49, 0.30, 0.52, "open"),
            "open",
        )
        self.assertEqual(
            classify_mouth_level_with_hysteresis(0.47, 0.30, 0.52, "open"),
            "half",
        )

    def test_joy_softens_large_vowel_shapes_until_strong_open(self):
        mouth = {"small": object(), "half": object(), "open": object()}
        self.assertEqual(
            soften_mouth_shape_for_emotion("joy", "u", 0.60, 0.30, 0.52, mouth),
            "half",
        )
        self.assertEqual(
            soften_mouth_shape_for_emotion("joy", "wide", 0.78, 0.30, 0.52, mouth),
            "open",
        )

    def test_non_joy_keeps_original_shape(self):
        self.assertEqual(
            soften_mouth_shape_for_emotion("surprise", "u", 0.60, 0.30, 0.52, {}),
            "u",
        )

    def test_stabilize_mouth_shape_keeps_speech_edges_immediate(self):
        shape, pending, since = stabilize_mouth_shape("half", "closed", None, 0.0, 1.0, 0.08)
        self.assertEqual((shape, pending, since), ("half", None, 1.0))

        shape, pending, since = stabilize_mouth_shape("closed", "half", None, 0.0, 1.0, 0.08)
        self.assertEqual((shape, pending, since), ("closed", None, 1.0))

    def test_stabilize_mouth_shape_waits_only_between_speaking_shapes(self):
        shape, pending, since = stabilize_mouth_shape("half", "small", None, 0.0, 1.0, 0.08)
        self.assertEqual((shape, pending, since), ("small", "half", 1.0))

        shape, pending, since = stabilize_mouth_shape("half", shape, pending, since, 1.04, 0.08)
        self.assertEqual((shape, pending, since), ("small", "half", 1.0))

        shape, pending, since = stabilize_mouth_shape("half", shape, pending, since, 1.09, 0.08)
        self.assertEqual((shape, pending, since), ("half", None, 1.09))


class EmotionAutoTargetResolutionTests(unittest.TestCase):
    def test_silence_has_priority_over_confidence_and_voicing(self):
        label, target, reason = resolve_emotion_auto_target(
            "happy",
            {"rms_db": -80.0, "confidence": 0.99, "voiced": 1.0},
            ["Neutral", "Happy"],
            "Neutral",
            silence_db=-65.0,
            min_conf=0.45,
        )
        self.assertEqual((label, target, reason), ("neutral", "Neutral", "silence"))

    def test_unvoiced_holds_current_even_when_label_exists(self):
        label, target, reason = resolve_emotion_auto_target(
            "happy",
            {"rms_db": -20.0, "confidence": 0.99, "voiced": 0.0},
            ["Neutral", "Happy"],
            "Neutral",
            silence_db=-65.0,
            min_conf=0.45,
        )
        self.assertEqual((label, target, reason), (None, None, "unvoiced"))

    def test_low_confidence_holds_current(self):
        label, target, reason = resolve_emotion_auto_target(
            "happy",
            {"rms_db": -20.0, "confidence": 0.20, "voiced": 1.0},
            ["Neutral", "Happy"],
            "Neutral",
            silence_db=-65.0,
            min_conf=0.45,
        )
        self.assertEqual((label, target, reason), (None, None, "low_conf"))

    def test_confident_voiced_label_maps_to_matching_set(self):
        label, target, reason = resolve_emotion_auto_target(
            "happy",
            {"rms_db": -20.0, "confidence": 0.80, "voiced": 1.0},
            ["Neutral", "Happy"],
            "Neutral",
            silence_db=-65.0,
            min_conf=0.45,
        )
        self.assertEqual((label, target, reason), ("happy", "Happy", "label"))


class WavAudioInputTests(unittest.TestCase):
    def test_load_wav_mono_float32_mixes_stereo_pcm16(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "voice.wav"
            left = np.array([0, 16384, -16384, 32767], dtype="<i2")
            right = np.array([0, 0, 0, -32768], dtype="<i2")
            stereo = np.stack([left, right], axis=1)
            with wave.open(str(path), "wb") as wf:
                wf.setnchannels(2)
                wf.setsampwidth(2)
                wf.setframerate(48000)
                wf.writeframes(stereo.tobytes())

            audio, samplerate = load_wav_mono_float32(str(path))

        self.assertEqual(samplerate, 48000)
        self.assertEqual(audio.dtype, np.float32)
        self.assertEqual(audio.shape, (4,))
        self.assertAlmostEqual(float(audio[1]), 0.25, places=4)


class BgVideoPhaseTests(unittest.TestCase):
    def test_seek_to_phase_does_not_restart_from_first_frame(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "loop.mp4"
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (16, 16))
            self.assertTrue(writer.isOpened())
            for i in range(10):
                frame = np.full((16, 16, 3), i * 20, dtype=np.uint8)
                writer.write(frame)
            writer.release()

            video = BgVideo(str(path), 16, 16)
            try:
                video.seek_to_phase(0.5)
                self.assertEqual(video.frame_idx, 5)
            finally:
                video.close()


if __name__ == "__main__":
    unittest.main()

"""Tests for helper script resolution in mouth_sprite_extractor."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from motionpngtuber.mouth_sprite_extractor import resolve_adjacent_or_repo_script


class ResolveAdjacentOrRepoScriptTests(unittest.TestCase):
    def test_prefers_adjacent_script_when_present(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            pkg = repo / "motionpngtuber"
            pkg.mkdir(parents=True)
            anchor = pkg / "mouth_sprite_extractor.py"
            anchor.write_text("# anchor\n", encoding="utf-8")
            adjacent = pkg / "face_track_anime_detector.py"
            adjacent.write_text("# adjacent\n", encoding="utf-8")
            repo_level = repo / "face_track_anime_detector.py"
            repo_level.write_text("# repo\n", encoding="utf-8")

            resolved = resolve_adjacent_or_repo_script(
                "face_track_anime_detector.py",
                anchor_file=str(anchor),
            )

            self.assertEqual(resolved, str(adjacent))

    def test_falls_back_to_repo_root_script(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            pkg = repo / "motionpngtuber"
            pkg.mkdir(parents=True)
            anchor = pkg / "mouth_sprite_extractor.py"
            anchor.write_text("# anchor\n", encoding="utf-8")
            repo_level = repo / "face_track_anime_detector.py"
            repo_level.write_text("# repo\n", encoding="utf-8")

            resolved = resolve_adjacent_or_repo_script(
                "face_track_anime_detector.py",
                anchor_file=str(anchor),
            )

            self.assertEqual(resolved, str(repo_level))

    def test_raises_with_searched_paths_when_missing(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            pkg = repo / "motionpngtuber"
            pkg.mkdir(parents=True)
            anchor = pkg / "mouth_sprite_extractor.py"
            anchor.write_text("# anchor\n", encoding="utf-8")

            with self.assertRaises(FileNotFoundError) as ctx:
                resolve_adjacent_or_repo_script(
                    "face_track_anime_detector.py",
                    anchor_file=str(anchor),
                )

            msg = str(ctx.exception)
            self.assertIn("face_track_anime_detector.py", msg)
            self.assertIn(str(pkg / "face_track_anime_detector.py"), msg)
            self.assertIn(str(repo / "face_track_anime_detector.py"), msg)


if __name__ == "__main__":
    unittest.main()

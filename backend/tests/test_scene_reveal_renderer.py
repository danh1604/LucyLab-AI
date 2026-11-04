import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from scene_reveal_renderer import (  # noqa: E402
    MASK_HEIGHT,
    MASK_WIDTH,
    SETTLE_FRAMES,
    normalize_scene_reveal,
    normalize_scene_reveal_override,
    prepare_scene_reveal_assets,
    resolve_scene_reveal,
    scene_frame_counts,
    split_reveal_frames,
)
from scene_image_service import _scene_clip_command  # noqa: E402


class SceneRevealRendererTests(unittest.TestCase):
    def test_custom_override_is_complete_and_enabled(self):
        global_config = normalize_scene_reveal({"enabled": False})
        scene = {
            "reveal_override": normalize_scene_reveal_override({
                "mode": "custom",
                "effect": "pencil",
                "duration_sec": 1.6,
                "pattern": "diagonal",
                "actor": "hand",
            })
        }
        resolved = resolve_scene_reveal(global_config, scene)
        self.assertTrue(resolved["enabled"])
        self.assertEqual("pencil", resolved["effect"])
        self.assertEqual("hand", resolved["actor"])

    def test_off_override_wins_over_global_effect(self):
        global_config = normalize_scene_reveal({"enabled": True, "effect": "brush"})
        resolved = resolve_scene_reveal(global_config, {"reveal_override": {"mode": "off"}})
        self.assertFalse(resolved["enabled"])

    def test_frame_counts_cover_the_full_audio_duration(self):
        scenes = [
            {"end_sec": 1.01, "duration_sec": 1.01},
            {"end_sec": 2.02, "duration_sec": 1.01},
            {"end_sec": 3.0, "duration_sec": 0.98},
        ]
        counts = scene_frame_counts(scenes, 3.0)
        self.assertEqual(90, sum(counts))
        self.assertTrue(all(count > 0 for count in counts))

    def test_reveal_timing_reserves_three_full_colour_frames(self):
        self.assertEqual((9, SETTLE_FRAMES), split_reveal_frames(12))
        self.assertEqual((0, 4), split_reveal_frames(4))

    def test_brush_assets_use_compact_mask_resolution(self):
        root = Path(tempfile.mkdtemp(prefix="scene_reveal_test_"))
        try:
            image_path = root / "source.png"
            Image.new("RGB", (320, 180), (80, 120, 160)).save(image_path)
            config = normalize_scene_reveal({"enabled": True, "effect": "brush", "duration_sec": 0.4})
            assets = prepare_scene_reveal_assets(
                root / "assets",
                project="test",
                scene_id="scene_001",
                image_path=image_path,
                config=config,
                animation_frames=9,
                settle_frames=SETTLE_FRAMES,
            )
            self.assertIsNotNone(assets.mask_pattern)
            mask_files = sorted((root / "assets").rglob("mask_*.png"))
            mask_path = mask_files[0]
            with Image.open(mask_path) as mask:
                self.assertEqual((MASK_WIDTH, MASK_HEIGHT), mask.size)
            for mask_path in mask_files[-2:]:
                with Image.open(mask_path) as final_mask:
                    extrema = final_mask.getextrema()
                    self.assertEqual((255, 255), extrema)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is required for reveal render regression coverage")
    def test_settle_and_hold_frames_are_full_colour_for_both_effects(self):
        root = Path(tempfile.mkdtemp(prefix="scene_reveal_render_test_"))
        ffmpeg_path = shutil.which("ffmpeg")
        assert ffmpeg_path is not None
        try:
            image_path = root / "source.png"
            Image.new("RGB", (320, 180), (36, 82, 210)).save(image_path)
            for effect in ("brush", "pencil"):
                with self.subTest(effect=effect):
                    config = normalize_scene_reveal({"enabled": True, "effect": effect, "duration_sec": 0.4})
                    animation_frames, settle_frames = split_reveal_frames(12)
                    assets = prepare_scene_reveal_assets(
                        root / effect / "assets",
                        project="test",
                        scene_id=effect,
                        image_path=image_path,
                        config=config,
                        animation_frames=animation_frames,
                        settle_frames=settle_frames,
                    )
                    clip_path = root / f"{effect}.mp4"
                    command = _scene_clip_command(
                        ffmpeg_path=ffmpeg_path,
                        image_path=image_path,
                        clip_path=clip_path,
                        scene_frames=48,
                        config=config,
                        assets=assets,
                        video_encoder="cpu_x264",
                        quality="high",
                    )
                    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
                    self.assertEqual(0, result.returncode, result.stderr[-2000:])
                    for frame_index, label in ((animation_frames, "settle"), (animation_frames + settle_frames, "hold")):
                        frame_path = root / f"{effect}_{label}.png"
                        extract = [
                            ffmpeg_path, "-y", "-v", "error", "-i", str(clip_path),
                            "-vf", f"select=eq(n\\,{frame_index})", "-frames:v", "1", str(frame_path),
                        ]
                        extracted = subprocess.run(extract, capture_output=True, text=True, encoding="utf-8", errors="replace")
                        self.assertEqual(0, extracted.returncode, extracted.stderr[-1000:])
                        with Image.open(frame_path) as frame:
                            rgb = frame.convert("RGB")
                            samples = [
                                rgb.getpixel((frame.width // 2, frame.height * 3 // 4)),
                                rgb.getpixel((frame.width // 2, frame.height - 4)),
                                rgb.getpixel((4, frame.height - 4)),
                                rgb.getpixel((frame.width - 5, frame.height - 4)),
                            ]
                        for red, green, blue in samples:
                            self.assertLess(red, 80)
                            self.assertLess(green, 130)
                            self.assertGreater(blue, 160)
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

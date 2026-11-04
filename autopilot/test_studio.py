import tempfile
from pathlib import Path

import studio
from studio import check_packaging, missing, project_video_name, safe_stem, use_channel

# Names the UI produced for the real "Brasilien" project.
assert safe_stem("Ngôi nhà này không nằm ở Đức") == "Ng_i_nh_n_y_kh_ng_n_m_c"
assert project_video_name("Ngôi nhà này không nằm ở Đức - de-DE") == "Ngôi_nhà_này_không_nằm_ở_Đức_-_de-DE.mp4"
assert missing({"scenes": [{"id": "a", "image_file": "x.png"}, {"id": "b"}, {"id": "c", "image_file": "y", "status": "failed"}]}, "scenes") == ["b", "c"]
ok = {"title": "Warum?", "description": "d", "tags": ["a b", "c"], "localizations": {"en": {"title": "Why?", "description": "d"}}}
check_packaging(ok)
for bad in ({**ok, "title": "x" * 101}, {**ok, "tags": ["x" * 400, "y" * 100]}, {**ok, "localizations": {"en": {"title": "<b>", "description": ""}}},
            {**ok, "description": "ä" * 2600},  # 2600 chars but 5200 bytes
            {**ok, "localizations": {"en": {"title": "", "description": "d"}}}):
    try:
        check_packaging(bad)
        raise AssertionError(f"accepted {bad}")
    except RuntimeError:
        pass
# Per-channel token swap: the backend reports whichever channel token.json belongs to.
tokens = Path(tempfile.mkdtemp())
studio.youtube_status = lambda: {"channel": {"id": (tokens / "token.json").read_text()}}
nemo, tonton = ({"channel_name": n, "youtube": {"channel_id": n}} for n in ("NEMO", "TONTON"))
(tokens / "token.json").write_text("NEMO")
use_channel(nemo, tokens)  # connected one is saved
(tokens / "token.json").write_text("TONTON")  # owner connects the second channel in the UI
use_channel(tonton, tokens)
use_channel(nemo, tokens)  # swapped back from the saved copy
assert (tokens / "token.json").read_text() == "NEMO"
(tokens / "token.TONTON.json").unlink()
(tokens / "token.json").write_text("TONTON")  # owner connects Tonton, but a Nemo upload runs first
use_channel(nemo, tokens)
assert (tokens / "token.TONTON.json").read_text() == "TONTON"  # not lost by the swap
(tokens / "token.TONTON.json").unlink()
try:
    use_channel(tonton, tokens)
    raise AssertionError("uploaded to the wrong channel")
except RuntimeError:
    pass
# style.txt overrides the extracted style and the style reference, other references untouched.
run_dir = Path(tempfile.mkdtemp()) / "ch" / "runs" / "r"
run_dir.mkdir(parents=True)
(run_dir.parents[1] / "profile.json").write_text("{}")
(run_dir / "state.json").write_text('{"blueprint": {"visual_style": "blue", "references": [{"type": "style", "prompt": "blue"}, {"type": "prop", "prompt": "knob"}]}}')
studio.apply_style(studio.Run(run_dir))  # no style.txt: unchanged
assert studio.Run(run_dir).state["blueprint"]["visual_style"] == "blue"
(run_dir / "style.txt").write_text("picture book\n", encoding="utf-8")
studio.apply_style(studio.Run(run_dir))
bp = studio.Run(run_dir).state["blueprint"]
assert bp["visual_style"] == bp["references"][0]["prompt"] == "picture book" and bp["references"][1]["prompt"] == "knob"
print("ok")

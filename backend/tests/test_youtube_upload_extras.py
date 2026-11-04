import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import youtube_publish_service as yps  # noqa: E402


class UploadExtrasTests(unittest.TestCase):
    def upload(self, item, tracks=(), mode="private"):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "captions").mkdir()
        (tmp / "captions" / "v.de.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHallo\n", encoding="utf-8")
        video = tmp / "v.mp4"
        video.write_bytes(b"x")
        yt = mock.MagicMock()
        yt.captions().list().execute.return_value = {"items": list(tracks)}
        yt.videos().list().execute.return_value = {"items": [{"status": {"embeddable": True, "publicStatsViewable": True, "privacyStatus": "public"}}]}
        with mock.patch.object(yps, "_youtube_service", return_value=yt), \
                mock.patch.object(yps, "_youtube_deps", return_value=(None, None, None, mock.MagicMock())), \
                mock.patch.object(yps, "_project_dir", return_value=tmp):
            base = {"kind": "long", "source_path": str(video), "title": "T", "youtube_video_id": "vid"}
            events = list(yps._upload_item({"project": "p"}, {**base, **item}, mode))
        return yt, events

    def test_update_sets_language_category_localizations_and_new_caption(self):
        yt, events = self.upload({
            "language": "de", "category_id": "27", "caption_file": "v.de.srt",
            "localizations": {"en": {"title": "Why", "description": "d"}},
        })
        kwargs = yt.videos().update.call_args.kwargs
        self.assertEqual(kwargs["part"], "snippet,status,localizations")
        self.assertEqual(kwargs["body"]["id"], "vid")
        self.assertEqual(kwargs["body"]["snippet"]["defaultLanguage"], "de")
        self.assertEqual(kwargs["body"]["snippet"]["defaultAudioLanguage"], "de")
        self.assertEqual(kwargs["body"]["snippet"]["categoryId"], "27")
        snippet = yt.captions().insert.call_args.kwargs["body"]["snippet"]
        self.assertEqual((snippet["videoId"], snippet["language"]), ("vid", "de"))
        self.assertEqual(events[-1]["type"], "done")

    def test_existing_own_track_is_replaced_not_duplicated(self):
        yt, _ = self.upload({"language": "de", "caption_file": "v.de.srt"},
                            tracks=[{"id": "c1", "snippet": {"language": "de", "trackKind": "standard"}}])
        self.assertEqual(yt.captions().update.call_args.kwargs["body"]["id"], "c1")
        yt.captions().insert.assert_not_called()

    def test_unsafe_caption_name_is_reported_and_upload_still_finishes(self):
        yt, events = self.upload({"caption_file": "../secret.srt"})
        self.assertTrue(any(e.get("message", "").startswith("Captions not set") for e in events))
        yt.captions().insert.assert_not_called()
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(yt.videos().update.call_args.kwargs["body"]["snippet"]["categoryId"], "24")
        self.assertTrue(events[-1]["warnings"])

    def test_publishing_reviewed_video_keeps_studio_edits(self):
        yt, events = self.upload({"language": "de", "caption_file": "v.de.srt", "scheduled_at": "2026-10-20T18:00:00",
                                  "timezone": "Europe/Berlin"}, mode="scheduled")
        status = yt.videos().update.call_args.kwargs["body"]["status"]
        self.assertEqual(status["publishAt"], "2026-10-20T16:00:00Z")
        self.assertEqual((status["embeddable"], status["publicStatsViewable"], status["privacyStatus"]), (True, True, "private"))
        yt.captions().insert.assert_not_called()
        yt.captions().update.assert_not_called()
        yt.thumbnails().set.assert_not_called()
        self.assertEqual(events[-1]["type"], "done")

    def test_localizations_need_language(self):
        yt, events = self.upload({"localizations": {"en": {"title": "Why", "description": "d"}}})
        self.assertNotIn("localizations", yt.videos().update.call_args.kwargs["body"])
        self.assertIn("Localizations skipped: set the item language first", events[-1]["warnings"])


if __name__ == "__main__":
    unittest.main()

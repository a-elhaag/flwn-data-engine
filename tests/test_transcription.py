"""Audio, video and recordings: transcribed with speaker labels, stored, searchable when allowed."""

import unittest
from unittest.mock import patch

import env  # noqa: F401  (sets the environment the app needs to import)
import httpx
from test_indexing import IndexingBase
from test_inference import FakeFoundry, ok

from app.clients import inference
from app.clients.inference import Phrase, Transcription
from app.clients.resilience import RateLimiter
from app.storage import indexer, media
from app.storage.errors import Unindexable

CALL = Transcription(
    [
        Phrase(1, 70, 4000, "We decided to keep the legacy endpoint."),
        Phrase(1, 4100, 5000, "It is used by old clients."),
        Phrase(2, 5200, 9000, "Sara will write the migration plan by Friday."),
    ],
    9100,
    "en-US",
)


class MediaTests(unittest.TestCase):
    def test_what_is_audio_and_what_is_video(self):
        for content_type, name, expected in (
            ("audio/ogg", "note.opus", "audio"),
            ("audio/mpeg", "a.bin", "audio"),
            ("application/octet-stream", "talk.M4A", "audio"),
            ("video/webm", "call.webm", "audio"),  # Azure Speech reads the WebM container
            ("video/mp4", "call.mp4", "video"),
            ("application/octet-stream", "clip.mov", "video"),
            ("application/pdf", "a.pdf", None),
        ):
            self.assertEqual(media.media_kind(content_type, name), expected, (content_type, name))

    MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 20

    def test_video_without_ffmpeg_is_refused_with_a_reason(self):
        with (
            patch("app.storage.media.shutil.which", return_value=None),
            self.assertRaisesRegex(Unindexable, "ffmpeg"),
        ):
            media.audio_track(self.MP4, "a.mp4")

    def test_a_playlist_disguised_as_video_never_reaches_ffmpeg(self):
        playlist = b"#EXTM3U\n#EXT-X-VERSION:3\nhttp://169.254.169.254/latest/meta-data\n"
        with (
            patch("app.storage.media.subprocess.run") as run,
            patch("app.storage.media.shutil.which", return_value="/usr/bin/ffmpeg"),
            self.assertRaisesRegex(Unindexable, "unsupported video format"),
        ):
            media.audio_track(playlist, "evil.mp4")
        run.assert_not_called()

    def test_ffmpeg_gets_a_forced_demuxer_no_network_and_a_name_it_chose(self):
        calls = []

        def fake_run(command, **_):
            calls.append(command)
            open(command[-1], "wb").write(b"ogg")
            return type("Done", (), {"returncode": 0})()

        with (
            patch("app.storage.media.subprocess.run", side_effect=fake_run),
            patch("app.storage.media.shutil.which", return_value="/usr/bin/ffmpeg"),
        ):
            result = media.audio_track(self.MP4, "evil.m3u8")
        self.assertEqual(result, (b"ogg", "audio.ogg", "audio/ogg"))
        (command,) = calls
        self.assertEqual(command[command.index("-protocol_whitelist") + 1], "file")
        self.assertEqual(command[command.index("-f") + 1], "mov,mp4,m4a,3gp,3g2,mj2")
        self.assertTrue(command[command.index("-i") + 1].endswith("input.bin"))


class SpeechClientTests(unittest.TestCase):
    def test_request_shape_and_reply_parsing(self):
        server = FakeFoundry(
            ok(
                {
                    "durationMilliseconds": 5226,
                    "phrases": [
                        {
                            "speaker": 1,
                            "offsetMilliseconds": 70,
                            "durationMilliseconds": 5000,
                            "text": " Hello team. ",
                            "locale": "en-US",
                        },
                        {"offsetMilliseconds": 5100, "durationMilliseconds": 100, "text": " "},
                    ],
                }
            )
        ).install(self)
        with patch("app.clients.inference._speech_limiter", RateLimiter(10_000, 10_000)):
            result = inference.transcribe(b"RIFF....", "a.wav", "audio/wav")
        (request,) = server.requests
        self.assertEqual(request.url.path, "/speechtotext/transcriptions:transcribe")
        self.assertEqual(
            request.headers["ocp-apim-subscription-key"], inference.settings.AZURE_FOUNDRY_KEY
        )
        body = request.content
        self.assertIn(b'"diarization"', body)
        self.assertIn(b"RIFF....", body)
        self.assertEqual(result, Transcription([Phrase(1, 70, 5070, "Hello team.")], 5226, "en-US"))

    def test_quota_errors_are_retried(self):
        server = FakeFoundry(httpx.Response(429), ok({"phrases": []})).install(self)
        with patch("app.clients.inference._speech_limiter", RateLimiter(10_000, 10_000)):
            result = inference.transcribe(b"x", "a.wav", "audio/wav")
        self.assertEqual((len(server.requests), result.phrases), (2, []))


class TranscriptionTests(IndexingBase):
    def transcribe_with(self, result=CALL):
        return patch("app.clients.inference.transcribe", return_value=result)

    def transcripts(self, file_id):
        return [
            dict(r) for r in self.sql("select * from transcripts where file_id = :i", i=file_id)
        ]

    def segments(self, transcript_id):
        return [
            dict(r)
            for r in self.sql(
                "select * from transcript_segments where transcript_id = :i order by sequence",
                i=transcript_id,
            )
        ]

    def test_workspace_audio_is_transcribed_and_searchable(self):
        record = self.add_file("standup.ogg", "audio/ogg", b"audio", kind="audio")
        self.assertEqual(record["index_status"], "pending")
        with self.transcribe_with():
            self.index_all()
        self.assertEqual(self.file_row(record["id"])["index_status"], "done")
        (transcript,) = self.transcripts(record["id"])
        self.assertEqual((transcript["language"], transcript["duration_ms"]), ("en-US", 9100))
        self.assertEqual(
            transcript["full_text"],
            "Speaker 1: We decided to keep the legacy endpoint. It is used by old clients.\n"
            "Speaker 2: Sara will write the migration plan by Friday.",
        )
        segments = self.segments(transcript["id"])
        self.assertEqual([s["speaker_label"] for s in segments], ["Speaker 1"] * 2 + ["Speaker 2"])
        self.assertEqual((segments[0]["start_ms"], segments[2]["end_ms"]), (70, 9000))
        hits = self.search("legacy endpoint").json()
        self.assertEqual(hits[0]["file_id"], record["id"])

    def test_chat_voice_notes_are_transcribed_but_never_searchable(self):
        record = self.add_file("v.ogg", "audio/ogg", b"audio", kind="voice_note", source="chat")
        self.assertEqual(record["index_status"], "pending")
        with self.transcribe_with():
            self.index_all()
        row = self.file_row(record["id"])
        self.assertEqual(row["index_status"], "skipped")
        self.assertIn("transcribed", row["index_error"])
        self.assertEqual(len(self.transcripts(record["id"])), 1)
        self.assertEqual(self.chunks(record["id"]), [])
        self.assertEqual(self.search("legacy endpoint").json(), [])

    def test_a_meeting_recording_fills_its_meeting_and_becomes_ready(self):
        (meeting,) = self.sql(
            "insert into meetings (workspace_id, title) values (:w, 'Sync') returning id",
            w=self.team_a,
        )
        record = self.add_file("m.ogg", "audio/ogg", b"audio", kind="recording", source="meeting")
        (recording,) = self.sql(
            "insert into recordings (workspace_id, meeting_id, file_id) values (:w, :m, :f)"
            " returning id",
            w=self.team_a,
            m=meeting["id"],
            f=record["id"],
        )
        with self.transcribe_with():
            self.index_all()
        (transcript,) = self.transcripts(record["id"])
        self.assertEqual(transcript["meeting_id"], meeting["id"])
        (state,) = self.sql(
            "select status, duration_ms from recordings where id = :i", i=recording["id"]
        )
        self.assertEqual((state["status"], state["duration_ms"]), ("ready", 9100))
        self.assertEqual(self.search("legacy endpoint").json(), [])

    def test_silence_is_skipped_and_a_failed_service_is_recorded(self):
        silent = self.add_file("s.wav", "audio/wav", b"audio", kind="audio")
        broken = self.add_file("b.wav", "audio/wav", b"audio", kind="audio")
        with self.transcribe_with(Transcription([], 1000, None)):
            indexer.run_once(self.blobs)
        self.assertEqual(self.file_row(silent["id"])["index_error"], "no speech found")
        with patch("app.clients.inference.transcribe", side_effect=RuntimeError("quota")):
            indexer.run_once(self.blobs)
        row = self.file_row(broken["id"])
        self.assertEqual(row["index_status"], "failed")
        self.assertIn("quota", row["index_error"])

    def test_video_goes_through_ffmpeg_first(self):
        record = self.add_file("call.mp4", "video/mp4", b"video", kind="video")
        seen = []

        def fake_transcribe(data, name, mime):
            seen.append((data, name, mime))
            return CALL

        with (
            patch("app.storage.media.audio_track", return_value=(b"ogg", "audio.ogg", "audio/ogg")),
            patch("app.clients.inference.transcribe", side_effect=fake_transcribe),
        ):
            self.index_all()
        self.assertEqual(seen, [(b"ogg", "audio.ogg", "audio/ogg")])
        self.assertEqual(self.file_row(record["id"])["index_status"], "done")

    def test_deleting_a_file_removes_its_transcript(self):
        record = self.add_file("standup.ogg", "audio/ogg", b"audio", kind="audio")
        with self.transcribe_with():
            self.index_all()
        self.client.delete(f"/workspaces/{self.team_a}/files/{record['id']}")
        self.assertEqual(self.search("legacy endpoint").json(), [])


if __name__ == "__main__":
    unittest.main()

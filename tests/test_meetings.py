"""Meetings: participation decides who can see what; recording needs everyone's consent."""

import json
import unittest
from unittest.mock import patch

import env  # noqa: F401  (sets the environment the app needs to import)
from fakes import FakeBlobs
from harness import MemoryHarness

from app.api import tokens
from app.clients.inference import Phrase, Transcription
from app.storage import indexer
from app.storage.blobs import get_storage

NO_SERVICE_KEY = {"X-Data-API-Key": ""}


class MeetingTests(MemoryHarness):
    def setUp(self):
        super().setUp()
        self.blobs = FakeBlobs()
        self.client.app.dependency_overrides[get_storage] = lambda: self.blobs
        self.ana = self.member(self.team_a)
        self.omar = self.member(self.team_a)
        self.lena = self.member(self.team_a)
        self.bot = self.member(self.team_a, kind="AI")

    def url(self, path="", workspace=None):
        return f"/workspaces/{workspace or self.team_a}/meetings{path}"

    def as_member(self, member, workspace=None, scopes=None):
        token, _ = tokens.mint(
            workspace or self.team_a, scopes or tokens.AGENT_SCOPES, "user", 600, member
        )
        return {"Authorization": f"Bearer {token}", **NO_SERVICE_KEY}

    def create(self, host=None, participants=(), **extra):
        host = host or self.ana
        response = self.client.post(
            self.url(),
            json={"title": "Sprint sync", "participants": list(participants), **extra},
            headers=self.as_member(host),
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def segments(self):
        return {
            "language": "en-US",
            "segments": [
                {
                    "start_ms": 0,
                    "end_ms": 2000,
                    "text": "Let us ship Friday.",
                    "speaker_member_id": self.ana,
                },
                {"start_ms": 2100, "end_ms": 3000, "text": "Agreed.", "speaker_label": "Speaker 2"},
            ],
        }

    # -- who sees what ------------------------------------------------------------------------------

    def test_host_is_added_and_participants_see_the_meeting_but_outsiders_do_not(self):
        meeting = self.create(participants=[self.omar])
        self.assertEqual(meeting["host_id"], self.ana)
        self.assertEqual({p["member_id"] for p in meeting["participants"]}, {self.ana, self.omar})
        path = self.url(f"/{meeting['id']}")
        self.assertEqual(self.client.get(path, headers=self.as_member(self.omar)).status_code, 200)
        self.assertEqual(self.client.get(path, headers=self.as_member(self.lena)).status_code, 404)
        self.assertEqual(self.client.get(path).status_code, 200)  # the trusted backend
        listed = lambda who: [  # noqa: E731
            m["id"]
            for m in self.client.get(self.url(), headers=self.as_member(who)).json()["items"]
        ]
        self.assertEqual((listed(self.omar), listed(self.lena)), ([meeting["id"]], []))

    def test_another_workspace_never_sees_it(self):
        meeting = self.create()
        self.assertEqual(
            self.client.get(self.url(f"/{meeting['id']}", self.team_b)).status_code, 404
        )

    def test_only_the_host_changes_the_meeting_and_status_moves_forward(self):
        meeting = self.create(participants=[self.omar])
        path = self.url(f"/{meeting['id']}")
        denied = self.client.patch(path, json={"status": "live"}, headers=self.as_member(self.omar))
        self.assertEqual(denied.status_code, 403)
        live = self.client.patch(path, json={"status": "live"}, headers=self.as_member(self.ana))
        self.assertEqual(live.json()["status"], "live")
        self.assertIsNotNone(live.json()["started_at"])
        ended = self.client.patch(path, json={"status": "ended"}, headers=self.as_member(self.ana))
        self.assertIsNotNone(ended.json()["ended_at"])
        back = self.client.patch(path, json={"status": "live"}, headers=self.as_member(self.ana))
        self.assertEqual(back.status_code, 409)

    def test_inviting_adds_participants_once(self):
        meeting = self.create()
        path = self.url(f"/{meeting['id']}/participants")
        for _ in range(2):
            response = self.client.post(
                path, json={"member_ids": [self.lena]}, headers=self.as_member(self.ana)
            )
            self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["participants"]), 2)
        stranger = self.client.post(
            path, json={"member_ids": [self.lena]}, headers=self.as_member(self.lena)
        )
        self.assertEqual(stranger.status_code, 403)

    def test_unknown_member_is_a_422(self):
        response = self.client.post(
            self.url(),
            json={"title": "x", "participants": ["00000000-0000-4000-8000-000000000000"]},
            headers=self.as_member(self.ana),
        )
        self.assertEqual(response.status_code, 422)

    # -- consent and recordings ----------------------------------------------------------------------

    def consent(self, meeting, who, status="granted"):
        return self.client.put(
            self.url(f"/{meeting['id']}/consent"),
            json={"status": status},
            headers=self.as_member(who),
        )

    def everyone_consents(self, meeting):
        for member in [p["member_id"] for p in meeting["participants"]]:
            self.consent(meeting, member)

    def go_live(self, meeting):
        self.client.patch(
            self.url(f"/{meeting['id']}"), json={"status": "live"}, headers=self.as_member(self.ana)
        )

    def record(self, meeting, headers=None):
        return self.client.post(
            self.url(f"/{meeting['id']}/recordings"),
            json={"name": "sync.ogg", "content_type": "audio/ogg", "size_bytes": 5},
            headers=headers,
        )

    def test_recording_needs_every_participants_consent(self):
        meeting = self.create(participants=[self.omar])
        self.go_live(meeting)
        refused = self.record(meeting)
        self.assertEqual(refused.status_code, 409)
        self.assertIn(self.omar, refused.json()["detail"])
        self.consent(meeting, self.ana)
        self.consent(meeting, self.omar, "declined")
        self.assertEqual(self.record(meeting).status_code, 409)
        self.consent(meeting, self.omar)
        self.assertEqual(self.record(meeting).status_code, 201)

    def test_only_the_meeting_service_starts_recordings_and_only_for_yourself_you_consent(self):
        meeting = self.create(participants=[self.omar])
        self.go_live(meeting)
        self.consent(meeting, self.ana)
        self.consent(meeting, self.omar)
        as_host = self.record(meeting, headers=self.as_member(self.ana))
        self.assertEqual(as_host.status_code, 403)
        outsider = self.consent(meeting, self.lena)
        self.assertEqual(outsider.status_code, 404)
        self.assertEqual(
            self.client.put(
                self.url(f"/{meeting['id']}/consent"), json={"status": "granted"}
            ).status_code,
            403,  # the trusted backend acting for no one cannot consent for people
        )

    def test_a_recording_is_transcribed_into_its_meeting_and_kept_from_search(self):
        meeting = self.create(participants=[self.omar])
        self.go_live(meeting)
        self.consent(meeting, self.ana)
        self.consent(meeting, self.omar)
        started = self.record(meeting).json()
        ticket = started["upload"]
        self.blobs.upload(ticket, data=b"audio", content_type="audio/ogg")
        done = self.client.post(f"/workspaces/{self.team_a}/files/{ticket['file_id']}/complete")
        self.assertEqual(done.status_code, 200, done.text)
        result = Transcription(
            [Phrase(1, 0, 2000, "Ship it Friday."), Phrase(2, 2100, 3000, "Agreed.")], 3000, "en-US"
        )
        with patch("app.clients.inference.transcribe", return_value=result):
            while indexer.run_once(self.blobs):
                pass
        (state,) = self.sql(
            "select status, consent_snapshot from recordings where id = :i",
            i=started["recording_id"],
        )
        self.assertEqual(state["status"], "ready")
        self.assertEqual(set(state["consent_snapshot"]), {self.ana, self.omar})
        transcript = self.client.get(
            self.url(f"/{meeting['id']}/transcript"), headers=self.as_member(self.omar)
        )
        self.assertEqual(transcript.status_code, 200, transcript.text)
        self.assertEqual(
            [s["text"] for s in transcript.json()["segments"]], ["Ship it Friday.", "Agreed."]
        )
        outsider = self.client.get(
            self.url(f"/{meeting['id']}/transcript"), headers=self.as_member(self.lena)
        )
        self.assertEqual(outsider.status_code, 404)
        # a supplied transcript never overrides what the recording actually said
        self.client.put(
            self.url(f"/{meeting['id']}/transcript"),
            json={"segments": [{"start_ms": 0, "end_ms": 1, "text": "forged"}]},
        )
        again = self.client.get(self.url(f"/{meeting['id']}/transcript")).json()
        self.assertEqual(again["segments"][0]["text"], "Ship it Friday.")
        found = self.client.post(
            f"/workspaces/{self.team_a}/files/search", json={"query": "Ship it Friday"}
        )
        self.assertEqual(found.json(), [])

    # -- transcripts -----------------------------------------------------------------------------------

    def test_supplied_transcript_is_stored_named_and_replaced_on_resend(self):
        meeting = self.create(participants=[self.omar])
        path = self.url(f"/{meeting['id']}/transcript")
        self.assertEqual(
            self.client.put(
                path, json=self.segments(), headers=self.as_member(self.ana)
            ).status_code,
            409,  # nobody has consented yet
        )
        self.go_live(meeting)
        self.everyone_consents(meeting)
        by_participant = self.client.put(
            path, json=self.segments(), headers=self.as_member(self.omar)
        )
        self.assertEqual(by_participant.status_code, 403)  # only the host or the meeting service
        put = self.client.put(path, json=self.segments(), headers=self.as_member(self.ana))
        self.assertEqual(put.status_code, 200, put.text)
        got = self.client.get(path, headers=self.as_member(self.ana)).json()
        self.assertEqual(got["total_segments"], 2)
        self.assertEqual(got["segments"][0]["speaker_name"], "Test User")
        self.assertEqual(got["full_text"], "Test User: Let us ship Friday.\nSpeaker 2: Agreed.")
        again = self.segments() | {"segments": self.segments()["segments"][:1]}
        self.client.put(path, json=again, headers=self.as_member(self.ana))
        self.assertEqual(
            self.client.get(path, headers=self.as_member(self.ana)).json()["total_segments"], 1
        )

    def test_outsiders_cannot_supply_a_transcript_and_bad_segments_are_rejected(self):
        meeting = self.create()
        self.go_live(meeting)
        self.everyone_consents(meeting)
        path = self.url(f"/{meeting['id']}/transcript")
        self.assertEqual(
            self.client.put(
                path, json=self.segments(), headers=self.as_member(self.lena)
            ).status_code,
            404,
        )
        backwards = {"segments": [{"start_ms": 5, "end_ms": 1, "text": "x"}]}
        self.assertEqual(self.client.put(path, json=backwards).status_code, 422)
        unknown = {
            "segments": [
                {
                    "start_ms": 0,
                    "end_ms": 1,
                    "text": "x",
                    "speaker_member_id": "00000000-0000-4000-8000-000000000000",
                }
            ]
        }
        self.assertEqual(self.client.put(path, json=unknown).status_code, 422)

    def test_nobody_can_be_added_once_a_recording_has_started(self):
        meeting = self.create()
        self.go_live(meeting)
        self.everyone_consents(meeting)
        self.assertEqual(self.record(meeting).status_code, 201)
        late = self.client.post(
            self.url(f"/{meeting['id']}/participants"),
            json={"member_ids": [self.lena]},
            headers=self.as_member(self.ana),
        )
        self.assertEqual(late.status_code, 409)

    def test_a_meeting_that_has_not_happened_has_no_transcript(self):
        meeting = self.create()
        self.everyone_consents(meeting)
        path = self.url(f"/{meeting['id']}/transcript")
        self.assertEqual(self.client.put(path, json=self.segments()).status_code, 409)

    def test_a_speaker_must_be_a_participant(self):
        meeting = self.create()
        self.go_live(meeting)
        self.everyone_consents(meeting)
        stranger = {
            "segments": [{"start_ms": 0, "end_ms": 1, "text": "x", "speaker_member_id": self.lena}]
        }
        response = self.client.put(self.url(f"/{meeting['id']}/transcript"), json=stranger)
        self.assertEqual(response.status_code, 422)

    def test_no_transcript_yet_is_a_404(self):
        meeting = self.create()
        self.assertEqual(self.client.get(self.url(f"/{meeting['id']}/transcript")).status_code, 404)

    def test_an_agent_in_the_meeting_reads_it_over_mcp_and_others_cannot(self):
        meeting = self.create(participants=[self.bot])
        self.go_live(meeting)
        self.everyone_consents(meeting)
        self.client.put(self.url(f"/{meeting['id']}/transcript"), json=self.segments())
        self.assertEqual(
            self.client.get(
                self.url(f"/{meeting['id']}/transcript"), headers=self.as_member(self.bot)
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                self.url(f"/{meeting['id']}/transcript"), headers=self.as_member(self.lena)
            ).status_code,
            404,
        )

    def mcp_transcript(self, meeting, who):
        response = self.client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "meeting_transcript",
                    "arguments": {"meeting_id": meeting["id"]},
                },
            },
            headers={"Accept": "application/json, text/event-stream", **self.as_member(who)},
        )
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()["result"]
        if not result.get("isError") and "structuredContent" not in result:
            result["structuredContent"] = json.loads(result["content"][0]["text"])
        return result

    def test_the_mcp_tool_serves_participants_only(self):
        meeting = self.create(participants=[self.bot])
        self.go_live(meeting)
        self.everyone_consents(meeting)
        self.client.put(self.url(f"/{meeting['id']}/transcript"), json=self.segments())
        mine = self.mcp_transcript(meeting, self.bot)
        self.assertFalse(mine.get("isError", False), mine)
        spoken = [(x["speaker"], x["text"]) for x in mine["structuredContent"]["segments"]]
        self.assertEqual(spoken, [("Test User", "Let us ship Friday."), ("Speaker 2", "Agreed.")])
        self.assertTrue(self.mcp_transcript(meeting, self.lena)["isError"])

    def test_writes_are_attributed_in_the_audit_log(self):
        meeting = self.create()
        events = [e for e in self.events(self.team_a, "meeting") if e["action"] == "created"]
        self.assertEqual(str(events[0]["actor_id"]), self.ana)
        self.assertEqual(str(events[0]["entity_id"]), meeting["id"])

    def test_read_only_tokens_cannot_create_or_change(self):
        read_only = self.as_member(self.ana, scopes=[tokens.SCOPE_MEETINGS_READ])
        self.assertEqual(
            self.client.post(self.url(), json={"title": "x"}, headers=read_only).status_code, 403
        )
        self.assertEqual(self.client.get(self.url(), headers=read_only).status_code, 200)


if __name__ == "__main__":
    unittest.main()

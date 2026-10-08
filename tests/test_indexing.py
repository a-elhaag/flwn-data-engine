"""File processing: chunking, text extraction, the indexing worker, and search with citations."""

import json
import threading
import time
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, patch

import env  # noqa: F401  (sets the environment the app needs to import)
from fakes import FakeBlobs
from harness import MemoryHarness, unit
from pdfs import make_pdf

from app.api import tokens
from app.config import settings
from app.storage import extract, indexer
from app.storage.blobs import get_storage
from app.storage.chunking import HARD_MAX, TARGET, Section, chunk_sections
from app.storage.errors import Unindexable

# -- chunking: pure functions -------------------------------------------------------------------


class ChunkingTests(unittest.TestCase):
    def test_nothing_in_nothing_out(self):
        self.assertEqual(chunk_sections([], 100), ([], False))
        self.assertEqual(chunk_sections([Section("  \n\n  ")], 100), ([], False))

    def test_every_heading_starts_a_chunk_with_its_exact_path(self):
        text = "# Design\n\nWe use Postgres.\n\n## Auth\n\nTokens are JWTs.\n\n## Storage\n\nBlobs."
        pieces, cut = chunk_sections([Section(text)], 100)
        self.assertFalse(cut)
        self.assertEqual(
            [p.heading_path for p in pieces], ["Design", "Design > Auth", "Design > Storage"]
        )
        self.assertIn("Tokens are JWTs.", pieces[1].text)
        self.assertNotIn("Blobs", pieces[1].text)

    def test_a_shallower_heading_pops_the_deeper_path(self):
        text = "# A\n\n## B\n\n### C\n\ntext\n\n# D\n\nmore"
        paths = [p.heading_path for p in chunk_sections([Section(text)], 100)[0]]
        # headings with no text of their own join the text below them, labelled by where it sits
        self.assertEqual(paths, ["A > B > C", "D"])

    def test_chunks_stay_near_the_target_size_and_keep_paragraphs_whole(self):
        paragraphs = [(f"Paragraph {i}. " + "word " * 100).strip() for i in range(20)]
        pieces, _ = chunk_sections([Section("\n\n".join(paragraphs))], 1000)
        self.assertGreater(len(pieces), 5)
        for piece in pieces:
            self.assertLessEqual(len(piece.text), TARGET + 50)
            for part in piece.text.split("\n\n"):
                self.assertIn(part, paragraphs)  # never cut mid-paragraph

    def test_a_chunk_repeats_the_tail_of_the_previous_one(self):
        paragraphs = [
            f"Unique marker {i}. " + "filler " * 12 for i in range(60)
        ]  # shorter than the overlap
        pieces, _ = chunk_sections([Section("\n\n".join(paragraphs))], 1000)
        for before, after in zip(pieces, pieces[1:], strict=False):
            last_paragraph = before.text.split("\n\n")[-1]
            self.assertIn(last_paragraph, after.text)  # so an answer on a boundary is not lost

    def test_an_oversized_paragraph_is_split_at_sentences(self):
        sentence = "This is one complete sentence about the topic. "
        pieces, _ = chunk_sections([Section(sentence * 120)], 100)
        self.assertGreater(len(pieces), 1)
        for piece in pieces:
            self.assertLessEqual(len(piece.text), HARD_MAX)
            self.assertTrue(piece.text.endswith("topic."), piece.text[-30:])

    def test_one_endless_sentence_is_cut_by_length(self):
        pieces, _ = chunk_sections([Section("x" * 10_000)], 100)
        self.assertGreater(len(pieces), 3)
        self.assertTrue(all(len(p.text) <= TARGET for p in pieces))

    def test_the_chunk_cap_is_reported(self):
        text = "\n\n".join(f"## Section {i}\n\ntext" for i in range(30))
        pieces, cut = chunk_sections([Section(text)], 5)
        self.assertEqual((len(pieces), cut), (5, True))

    def test_pages_and_offsets_are_kept(self):
        sections = [Section("First page text.", 1), Section("Second page text.", 2)]
        pieces, _ = chunk_sections(sections, 100)
        self.assertEqual(
            [p.page for p in pieces], [1, 2]
        )  # a chunk never spans pages: exact citations
        long_pages = [Section("a " * 600, 1), Section("b " * 600, 2)]
        pieces, _ = chunk_sections(long_pages, 100)
        self.assertEqual(pieces[0].page, 1)
        self.assertEqual(pieces[-1].page, 2)
        self.assertEqual([p.start for p in pieces], sorted(p.start for p in pieces))

    def test_hash_and_embedding_text(self):
        (a,), _ = chunk_sections([Section("# Title\n\nbody")], 10)
        (b,), _ = chunk_sections([Section("# Title\n\nbody")], 10)
        (c,), _ = chunk_sections([Section("# Title\n\nother body")], 10)
        self.assertEqual(a.content_hash, b.content_hash)
        self.assertNotEqual(a.content_hash, c.content_hash)
        self.assertTrue(a.embedding_text.startswith("Title\n"))  # the path travels with the text


# -- extraction ------------------------------------------------------------------------------------


class ExtractionTests(unittest.TestCase):
    def test_what_each_file_type_is_read_as(self):
        for content_type, name, expected in (
            ("application/pdf", "a.bin", "pdf"),
            ("application/octet-stream", "Report.PDF", "pdf"),
            ("image/png", "a.png", "image"),
            ("image/jpeg; charset=binary", "a.jpg", "image"),
            ("text/markdown", "a.md", "text"),
            ("application/json", "a.json", "text"),
            ("application/octet-stream", "main.py", "text"),
            ("image/svg+xml", "a.svg", None),
            ("application/zip", "a.zip", None),
            (None, "noext", None),
        ):
            self.assertEqual(extract.file_type(content_type, name), expected, (content_type, name))

    def test_text_is_decoded_and_binary_or_empty_is_refused(self):
        self.assertEqual(
            extract.extract("héllo\nworld".encode(), "text/plain", "a.txt").sections[0].text,
            "héllo\nworld",
        )
        with self.assertRaises(Unindexable):
            extract.extract(b"   \n ", "text/plain", "a.txt")
        with self.assertRaises(Unindexable):
            extract.extract(bytes(range(256)) * 40, "text/plain", "a.txt")

    def test_a_pdf_with_text_is_read_page_by_page(self):
        pdf = make_pdf(["Quarterly plan for the team", "Budget and hiring notes"])
        result = extract.extract(pdf, "application/pdf", "plan.pdf")
        self.assertEqual(
            [(s.page, s.text) for s in result.sections],
            [(1, "Quarterly plan for the team"), (2, "Budget and hiring notes")],
        )
        self.assertEqual(result.notes, [])

    def test_a_scanned_pdf_is_rendered_and_read_by_the_parse_model(self):
        pdf = make_pdf(["", "", ""])  # no text layer, like a scan
        parse = Mock(side_effect=lambda data, mime: f"scanned text {len(data) > 100}")
        with patch("app.clients.inference.parse_image", parse):
            result = extract.extract(pdf, "application/pdf", "scan.pdf")
        self.assertEqual([s.page for s in result.sections], [1, 2, 3])
        self.assertEqual(parse.call_count, 3)
        self.assertTrue(all(call.args[1] == "image/png" for call in parse.call_args_list))

    def test_a_long_scan_is_read_only_up_to_the_page_cap_and_says_so(self):
        pdf = make_pdf([""] * 5)
        with (
            patch.object(settings, "INDEX_MAX_SCANNED_PAGES", 2),
            patch("app.clients.inference.parse_image", return_value="page text") as parse,
        ):
            result = extract.extract(pdf, "application/pdf", "scan.pdf")
        self.assertEqual((len(result.sections), parse.call_count), (2, 2))
        self.assertEqual(result.notes, ["read the first 2 of 5 scanned pages"])

    def test_a_scan_with_nothing_readable_is_skipped_not_empty(self):
        with (
            patch("app.clients.inference.parse_image", return_value=""),
            self.assertRaises(Unindexable),
        ):
            extract.extract(make_pdf(["", ""]), "application/pdf", "blank.pdf")

    def test_an_image_is_read_by_the_parse_model(self):
        with patch("app.clients.inference.parse_image", return_value="# Invoice 4821") as parse:
            result = extract.extract(b"\x89PNG...", "image/png", "scan.png")
        self.assertEqual(result.sections[0].text, "# Invoice 4821")
        self.assertEqual(parse.call_args.args[1], "image/png")
        with (
            patch("app.clients.inference.parse_image", return_value=""),
            self.assertRaises(Unindexable),
        ):
            extract.extract(b"x", "image/png", "blank.png")

    def test_broken_and_oversized_pdfs_are_refused_with_a_reason(self):
        with self.assertRaisesRegex(Unindexable, "not a readable PDF"):
            extract.extract(b"%PDF-1.4 this is not really a pdf", "application/pdf", "bad.pdf")
        with (
            patch.object(settings, "INDEX_MAX_PAGES", 2),
            self.assertRaisesRegex(Unindexable, "page limit"),
        ):
            extract.extract(make_pdf(["a", "b", "c"]), "application/pdf", "long.pdf")

    def test_unknown_types_say_so(self):
        with self.assertRaisesRegex(Unindexable, "no text extractor"):
            extract.extract(b"PK", "application/zip", "a.zip")


# -- the worker and search --------------------------------------------------------------------------

GUIDE = """# Deployment guide

Releases ship every second Tuesday after the review.

## Database

We use PostgreSQL with pgvector. The connection role must not bypass row-level security.

## Storage

Uploads go straight to Azure Blob Storage through signed links, so the API never carries the bytes.
"""


class IndexingBase(MemoryHarness):
    def setUp(self):
        super().setUp()
        self.blobs = FakeBlobs()
        self.client.app.dependency_overrides[get_storage] = lambda: self.blobs

    def add_file(
        self, name, content_type, data, kind="document", source="workspace", workspace=None
    ):
        """Upload and verify a file through the API, as a client would."""
        workspace = workspace or self.team_a
        body = {
            "kind": kind,
            "name": name,
            "content_type": content_type,
            "size_bytes": len(data),
            "source": source,
        }
        ticket = self.client.post(f"/workspaces/{workspace}/files", json=body).json()
        self.blobs.upload(ticket, data=data, content_type=content_type)
        done = self.client.post(f"/workspaces/{workspace}/files/{ticket['file_id']}/complete")
        self.assertEqual(done.status_code, 200, done.text)
        return done.json()

    def index_all(self):
        count = 0
        while indexer.run_once(self.blobs):
            count += 1
        return count

    def file_row(self, file_id):
        (row,) = self.sql("select * from files where id = :i", i=file_id)
        return dict(row)

    def chunks(self, file_id):
        return [
            dict(r)
            for r in self.sql(
                "select * from chunks where source_id = :i order by chunk_index", i=file_id
            )
        ]

    def search(self, query, workspace=None, **body):
        return self.client.post(
            f"/workspaces/{workspace or self.team_a}/files/search",
            json={"query": query, **body},
        )


class IndexingTests(IndexingBase):
    def test_a_markdown_file_becomes_searchable_chunks_with_citations(self):
        record = self.add_file("guide.md", "text/markdown", GUIDE.encode())
        self.assertEqual(record["index_status"], "pending")
        self.assertEqual(self.index_all(), 1)

        done = self.client.get(f"/workspaces/{self.team_a}/files/{record['id']}").json()
        self.assertEqual(
            (done["index_status"], done["chunk_count"], done["index_error"]), ("done", 3, None)
        )
        chunks = self.chunks(record["id"])
        self.assertEqual(
            [c["heading_path"] for c in chunks],
            ["Deployment guide", "Deployment guide > Database", "Deployment guide > Storage"],
        )
        self.assertTrue(all(c["embedding"] is not None for c in chunks))
        events = [e["action"] for e in self.events(self.team_a, "file")]
        self.assertEqual(events, ["created", "uploaded", "indexed"])

        hits = self.search("pgvector").json()
        self.assertEqual(hits[0]["file_name"], "guide.md")
        self.assertEqual(hits[0]["heading_path"], "Deployment guide > Database")
        self.assertIn("row-level security", hits[0]["text"])
        self.assertEqual(hits[0]["file_id"], record["id"])

    def test_a_pdf_is_cited_by_page(self):
        pdf = make_pdf(
            ["Intro to the platform", "Refund policy: customers may cancel within 30 days"]
        )
        self.add_file("handbook.pdf", "application/pdf", pdf, kind="pdf")
        self.index_all()
        hit = self.search("refund policy").json()[0]
        self.assertEqual((hit["file_name"], hit["page"]), ("handbook.pdf", 2))

    def test_a_scanned_pdf_and_an_image_are_read_by_the_parse_model(self):
        scan = self.add_file("scan.pdf", "application/pdf", make_pdf(["", ""]), kind="pdf")
        photo = self.add_file("board.png", "image/png", b"\x89PNG fake", kind="image")
        with patch(
            "app.clients.inference.parse_image",
            side_effect=lambda d, m: "Whiteboard: ship the beta on Friday",
        ):
            self.index_all()
        self.assertEqual(self.file_row(scan["id"])["index_status"], "done")
        self.assertEqual(self.file_row(photo["id"])["index_status"], "done")
        self.assertIn("beta on Friday", self.search("beta").json()[0]["text"])

    def test_chat_and_meeting_files_are_never_indexed_because_they_may_be_private(self):
        for source, kind in (("chat", "document"), ("meeting", "document")):
            record = self.add_file(
                "secret.txt", "text/plain", b"private channel notes", kind=kind, source=source
            )
            self.assertEqual(record["index_status"], "skipped")
            self.assertIn("may be private", record["index_error"])
        self.assertEqual(self.index_all(), 0)
        self.assertEqual(self.search("private channel notes").json(), [])

    def test_a_file_nothing_can_read_is_skipped_with_a_reason_and_never_queued(self):
        record = self.add_file("data.zip", "application/zip", b"PK\x03\x04", kind="other")
        self.assertEqual(
            (record["index_status"], "no text extractor" in record["index_error"]),
            ("skipped", True),
        )
        self.assertEqual(self.index_all(), 0)

    def test_an_unreadable_file_is_skipped_by_the_worker_with_its_reason(self):
        record = self.add_file("broken.pdf", "application/pdf", b"%PDF-1.4 garbage", kind="pdf")
        self.index_all()
        row = self.file_row(record["id"])
        self.assertEqual(row["index_status"], "skipped")
        self.assertIn("not a readable PDF", row["index_error"])

    def test_a_failure_is_recorded_and_reindexing_recovers_without_duplicating_chunks(self):
        record = self.add_file("guide.md", "text/markdown", GUIDE.encode())
        with patch(
            "app.clients.inference.embed_many", side_effect=RuntimeError("model unavailable")
        ):
            self.index_all()
        row = self.file_row(record["id"])
        self.assertEqual(row["index_status"], "failed")
        self.assertIn("model unavailable", row["index_error"])
        self.assertEqual(self.chunks(record["id"]), [])

        again = self.client.post(f"/workspaces/{self.team_a}/files/{record['id']}/reindex")
        self.assertEqual(again.json()["index_status"], "pending")
        self.index_all()
        self.assertEqual(len(self.chunks(record["id"])), 3)
        self.client.post(f"/workspaces/{self.team_a}/files/{record['id']}/reindex")
        self.index_all()
        self.assertEqual(len(self.chunks(record["id"])), 3)  # replaced, not appended

    def test_a_long_document_is_cut_at_the_chunk_cap_and_says_so(self):
        long_text = "\n\n".join(f"## Part {i}\n\nbody {i}" for i in range(20))
        record = self.add_file("long.md", "text/markdown", long_text.encode())
        with patch.object(settings, "INDEX_MAX_CHUNKS", 4):
            self.index_all()
        row = self.file_row(record["id"])
        self.assertEqual((row["index_status"], row["chunk_count"]), ("done", 4))
        self.assertEqual(row["index_error"], "indexed the first 4 sections only")

    def test_a_file_over_the_size_limit_is_skipped(self):
        record = self.add_file("big.txt", "text/plain", b"x" * 5000)
        with patch.object(settings, "INDEX_MAX_BYTES", 1000):
            self.index_all()
        self.assertEqual(self.file_row(record["id"])["index_status"], "skipped")

    def test_deleting_a_file_removes_its_chunks_from_search(self):
        record = self.add_file("guide.md", "text/markdown", GUIDE.encode())
        self.index_all()
        self.assertTrue(self.search("pgvector").json())
        self.client.delete(f"/workspaces/{self.team_a}/files/{record['id']}")
        self.assertEqual(self.chunks(record["id"]), [])
        self.assertEqual(self.search("pgvector").json(), [])

    def test_a_file_deleted_while_it_is_being_indexed_leaves_nothing_behind(self):
        record = self.add_file("guide.md", "text/markdown", GUIDE.encode())
        original = self.blobs.download

        def delete_then_read(container, path, max_bytes):
            self.sql("update files set deleted_at = now() where id = :i", i=record["id"])
            return original(container, path, max_bytes)

        self.blobs.download = delete_then_read
        self.index_all()
        self.assertEqual(self.chunks(record["id"]), [])

    def test_only_finished_files_are_searched(self):
        done = self.add_file("done.md", "text/markdown", b"# Done\n\nshared keyword zebra")
        waiting = self.add_file("waiting.md", "text/markdown", b"# Waiting\n\nshared keyword zebra")
        indexer.run_once(self.blobs)  # indexes one of them (the oldest)
        names = {h["file_name"] for h in self.search("zebra").json()}
        self.assertEqual(len(names), 1)
        self.assertNotEqual(
            self.file_row(done["id"])["index_status"], self.file_row(waiting["id"])["index_status"]
        )

    # -- search ---------------------------------------------------------------------------------

    def test_search_never_crosses_workspaces(self):
        self.add_file("a.md", "text/markdown", b"# A\n\nteam alpha roadmap secret")
        b = self.add_file(
            "b.md", "text/markdown", b"# B\n\nteam beta roadmap", workspace=self.team_b
        )
        self.index_all()
        self.assertEqual([h["file_name"] for h in self.search("roadmap").json()], ["a.md"])
        self.assertEqual(
            [h["file_id"] for h in self.search("roadmap", workspace=self.team_b).json()], [b["id"]]
        )

    def test_search_filters_by_file_kind(self):
        self.add_file("notes.md", "text/markdown", b"# Notes\n\nquarterly figures", kind="document")
        self.add_file(
            "report.md",
            "text/markdown",
            b"# Report\n\nquarterly figures",
            kind="report",
            source="agent",
        )
        self.index_all()
        self.assertEqual(
            {h["file_name"] for h in self.search("quarterly").json()}, {"notes.md", "report.md"}
        )
        self.assertEqual(
            [h["file_name"] for h in self.search("quarterly", kind="report").json()], ["report.md"]
        )

    def test_search_finds_exact_terms_the_embedding_cannot_and_the_reranker_orders_results(self):
        self.add_file("a.md", "text/markdown", b"# Tickets\n\nBug PROJ-4821 crashes the app")
        self.add_file(
            "b.md", "text/markdown", b"# Tickets\n\nBug PROJ-4822 is about push notifications"
        )
        self.index_all()
        self.assertEqual(
            self.search("PROJ-4821").json()[0]["file_name"], "a.md"
        )  # words, no reranker

        def fake_rerank(query, documents, top_n=None):
            scores = [(i, 0.9 if "4822" in d else 0.1) for i, d in enumerate(documents)]
            return sorted(scores, key=lambda pair: pair[1], reverse=True)

        with (
            patch.object(settings, "MEMORY_RERANK", True),
            patch("app.clients.inference.rerank", side_effect=fake_rerank),
        ):
            hits = self.search("PROJ-4821").json()
        self.assertEqual([h["file_name"] for h in hits], ["b.md", "a.md"])  # the reranker decides
        self.assertEqual(hits[0]["score"], 0.9)

    def test_search_survives_a_reranker_failure(self):
        self.add_file("a.md", "text/markdown", b"# One\n\nalpha topic")
        self.add_file("b.md", "text/markdown", b"# Two\n\nalpha subject")
        self.index_all()
        with (
            patch.object(settings, "MEMORY_RERANK", True),
            patch("app.clients.inference.rerank", side_effect=RuntimeError("down")),
        ):
            self.assertEqual(len(self.search("alpha").json()), 2)

    def test_search_input_is_validated(self):
        for body in (
            {"query": " "},
            {"query": "x", "limit": 0},
            {"query": "x", "limit": 51},
            {"query": "x", "kind": "exe"},
        ):
            self.assertEqual(
                self.client.post(f"/workspaces/{self.team_a}/files/search", json=body).status_code,
                422,
                body,
            )

    # -- who may search -------------------------------------------------------------------------

    def test_searching_needs_the_files_read_scope_and_the_right_workspace(self):
        def agent(workspace, scopes):
            token, _ = tokens.mint(workspace, scopes, "agent", 600)
            return {"Authorization": f"Bearer {token}", "X-Data-API-Key": ""}

        path = f"/workspaces/{self.team_a}/files/search"
        body = {"query": "anything"}
        self.assertEqual(
            self.client.post(
                path, json=body, headers=agent(self.team_a, {tokens.SCOPE_FILES_READ})
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                path, json=body, headers=agent(self.team_a, {tokens.SCOPE_READ})
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                path, json=body, headers=agent(self.team_b, {tokens.SCOPE_FILES_READ})
            ).status_code,
            403,
        )

    def test_the_mcp_tool_searches_files_for_the_token_workspace_only(self):
        self.add_file("guide.md", "text/markdown", GUIDE.encode())
        self.add_file(
            "b.md", "text/markdown", b"# B\n\npgvector in the other team", workspace=self.team_b
        )
        self.index_all()

        def call(workspace, scopes):
            token, _ = tokens.mint(workspace, scopes, "agent", 600)
            response = self.client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "files_search", "arguments": {"query": "pgvector"}},
                },
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Data-API-Key": "",
                    "Accept": "application/json, text/event-stream",
                },
            )
            return response.json()["result"]

        result = call(self.team_a, {tokens.SCOPE_FILES_READ})
        results = json.loads(result["content"][0]["text"])["results"]
        self.assertEqual({r["file_name"] for r in results}, {"guide.md"})
        self.assertTrue(call(self.team_a, {tokens.SCOPE_READ})["isError"])  # wrong scope

    # -- the queue ------------------------------------------------------------------------------

    def test_a_stale_claim_is_taken_over_but_a_fresh_one_is_left_alone(self):
        fresh = self.add_file("fresh.md", "text/markdown", b"# Fresh\n\ntext")
        stale = self.add_file("stale.md", "text/markdown", b"# Stale\n\ntext")
        for record, minutes in ((fresh, 1), (stale, settings.INDEX_STUCK_MINUTES + 5)):
            started = datetime.now(UTC) - timedelta(minutes=minutes)
            self.sql(
                "update files set index_status = 'indexing', index_started_at = :t where id = :i",
                t=started,
                i=record["id"],
            )
        claim = indexer.claim_next()
        self.assertEqual(str(claim.file_id), stale["id"])  # the crashed worker's file
        self.assertIsNone(indexer.claim_next())  # the fresh claim belongs to a live worker

    def test_workers_never_claim_the_same_file(self):
        ids = {
            self.add_file(f"f{i}.md", "text/markdown", f"# F{i}\n\ntext".encode())["id"]
            for i in range(6)
        }
        claimed, barrier = [], threading.Barrier(6)

        def grab():
            barrier.wait()
            claim = indexer.claim_next()
            if claim:
                claimed.append(str(claim.file_id))

        threads = [threading.Thread(target=grab) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(sorted(claimed), sorted(ids))  # six claims, six different files

    def test_the_background_worker_indexes_a_file_as_soon_as_it_is_completed(self):
        worker = indexer.start_worker(self.blobs)
        self.addCleanup(indexer.stop_worker)
        record = self.add_file("guide.md", "text/markdown", GUIDE.encode())
        deadline = time.time() + 10
        while time.time() < deadline and self.file_row(record["id"])["index_status"] != "done":
            time.sleep(0.05)
        self.assertEqual(self.file_row(record["id"])["index_status"], "done")
        self.assertTrue(worker.is_alive())
        indexer.stop_worker()
        worker.join(5)
        self.assertFalse(worker.is_alive())

    def test_unit_vectors_make_meaning_search_visible(self):
        """Different embeddings per chunk: meaning alone (no shared words) still finds the chunk."""
        self.add_file("a.md", "text/markdown", b"# Cats\n\nfelines purr")
        self.add_file("b.md", "text/markdown", b"# Dogs\n\ncanines bark")
        axes = {"Cats": 1, "Dogs": 2}

        def by_text(texts):
            return [
                unit(next((axis for key, axis in axes.items() if key in text), 3)) for text in texts
            ]

        with patch("app.clients.inference.embed_many", side_effect=by_text):
            self.index_all()
        with patch("app.clients.inference.embed", return_value=unit(2)):
            hits = self.search("man's best friend").json()  # shares no words with the file
        self.assertEqual(hits[0]["file_name"], "b.md")


if __name__ == "__main__":
    unittest.main()

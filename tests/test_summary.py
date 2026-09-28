import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from meeting_scribe.storage import atomic_json, read_json
from meeting_scribe.summary import chunks, generate, summarize, normalize_citations, error_message, split_headline, session_summary, summary_headline


class SummaryTests(unittest.TestCase):
    def test_legacy_list_uses_explicit_topic_not_discussion_or_old_excerpt(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            atomic_json(folder/'summary.json',{'headline':'人才评估时间预计为10月中旬至12月'})
            topic='人才评估更新及系统迁移；动态分析团队组建计划'
            for heading in [f'会议主题：{topic}',f'## 会议主题\n- {topic} [00:00:01]',f'**会议主题：** {topic}']:
                (folder/'summary.txt').write_text(heading+'\n讨论要点：\n- 人才评估时间预计为10月中旬至12月',encoding='utf-8')
                self.assertEqual(summary_headline(folder),topic)
            (folder/'summary.txt').write_text('## 会议主题\n\n## 讨论要点\n- 人才评估时间预计为10月中旬至12月',encoding='utf-8')
            self.assertEqual(summary_headline(folder),'')
            self.assertEqual(split_headline('讨论要点：\n- 热点事项')[0],'')

    def test_headline_export_indexes_meeting_and_preserves_body(self):
        with tempfile.TemporaryDirectory() as temp:
            session=Path(temp)
            folder=session/'speaker_transcripts'/'version'
            folder.mkdir(parents=True)
            atomic_json(folder/'transcript.json',{'segments':[{'start':0,'text':'安排下周测试'}]})
            tags=io.BytesIO(b'{"models":[{"name":"qwen3.5:4b"}]}')
            with patch('meeting_scribe.summary.request',return_value=tags), patch('meeting_scribe.summary.generate',return_value='一句话总结：安排下周测试\n\n## 待办\n- 下周测试 [00:00:00]'):
                result=summarize(folder,'qwen3.5:4b')
            self.assertEqual(session_summary(session),result)
            self.assertEqual(summary_headline(result),'安排下周测试')
            self.assertTrue((result/'summary.md').read_text(encoding='utf-8').startswith('# 安排下周测试'))
            self.assertIn('下周测试 [00:00:00]',(result/'summary.md').read_text(encoding='utf-8'))

    def test_headline_is_short_single_line_without_citations(self):
        headline,body=split_headline('一句话总结：'+('安排会议测试'*10)+' [00:00:01]\n\n正文')
        self.assertLessEqual(len(headline),28)
        self.assertNotIn('[',headline)
        self.assertEqual(body,'正文')

    def test_missing_citations_saves_flagged_draft_without_fake_timestamps(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            atomic_json(folder / "transcript.json", {"segments": [{"start": 75, "text": "测试"}]})
            tags = io.BytesIO(b'{"models":[{"name":"qwen3.5:4b"}]}')
            with patch("meeting_scribe.summary.request", return_value=tags), patch(
                    "meeting_scribe.summary.generate", side_effect=["无时间", "错误 [00:00:00]"]):
                result = summarize(folder, "qwen3.5:4b")
            self.assertTrue((folder / "latest_summary.json").exists())
            text = (result / "summary.md").read_text(encoding="utf-8")
            self.assertIn("引用待核对", text)
            self.assertIn("[时间待核对]", text)
            self.assertNotIn("[00:00:00]", text)
            self.assertEqual(read_json(result / "summary.json")["citation_status"], "needs_review")

    def test_equivalent_timestamp_formats_and_error_classification(self):
        self.assertEqual(normalize_citations("事项【1：15】 与 [0:01:15]"), "事项[00:01:15] 与 [00:01:15]")
        self.assertEqual(normalize_citations("[99:99]"), "[99:99]")
        self.assertNotIn("Ollama", error_message(ValueError("稿件为空")))
        self.assertIn("Ollama", error_message(ConnectionRefusedError("refused")))

    def test_long_transcript_uses_all_chunks(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            atomic_json(folder / "transcript.json", {"segments": [{"start": 75, "text": "内容"*2000}]})
            tags = io.BytesIO(b'{"models":[{"name":"qwen3.5:4b"}]}')
            with patch("meeting_scribe.summary.request", return_value=tags), patch(
                    "meeting_scribe.summary.generate", return_value="要点 [00:01:15]") as generate_mock:
                result = summarize(folder, "qwen3.5:4b")
            self.assertEqual(read_json(result / "summary.json")["parts"], 3)
            self.assertEqual(generate_mock.call_count, 4)

    def test_chunking_keeps_all_text_and_source_times(self):
        text = "数据完整性" * 1000
        parts = chunks([{"start": 61, "source": "会议声音", "text": text}])
        self.assertTrue(all(len(p) <= 1800 for p in parts))
        self.assertEqual("".join(p.removeprefix("[00:01:01] 会议声音：").rstrip("\n") for p in parts), text)

    def test_cancel_and_truncated_response_do_not_return_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            flag = Path(temp) / "cancel"
            flag.touch()
            with self.assertRaises(InterruptedError):
                generate("qwen3.5:4b", "text", flag)
        response = io.BytesIO(json.dumps({"done": True, "done_reason": "length",
                                         "message": {"content": "incomplete"}}).encode()+b"\n")
        with patch("meeting_scribe.summary.request", return_value=response):
            with self.assertRaises(RuntimeError):
                generate("qwen3.5:4b", "text")

    def test_exports_versions_and_keeps_source(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source = {"segments": [{"start": 0, "source": "会议声音", "text": "Alice will test."}],
                      "metadata": {"mode": "live"}}
            atomic_json(folder / "transcript.json", source)
            def tags(*args, **kwargs):
                return io.BytesIO(b'{"models":[{"name":"qwen3.5:4b"},{"name":"qwen3.5:9b"}]}')
            with patch("meeting_scribe.summary.request", side_effect=tags), patch(
                    "meeting_scribe.summary.generate", return_value="测试摘要 [00:00:00]"):
                first = summarize(folder, "qwen3.5:4b")
                second = summarize(folder, "qwen3.5:9b")
            self.assertNotEqual(first, second)
            self.assertTrue((first / "summary.txt").exists())
            self.assertEqual(read_json(folder / "latest_summary.json")["model"], "qwen3.5:9b")
            self.assertEqual(read_json(folder / "transcript.json"), source)
            self.assertIn("实时草稿", (second / "summary.md").read_text(encoding="utf-8"))

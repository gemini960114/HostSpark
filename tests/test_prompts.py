import unittest
from pathlib import Path

from hostspark.constants import (
    AUDIO_EXTENSIONS,
    DEFAULT_CIRCUIT_BREAKER_MAX_FAILURES,
    DEFAULT_JOB_QUEUE_MAXSIZE,
    DEFAULT_SCHEDULE_CLEANUP_MAX_AGE_DAYS,
    DOC_EXTENSIONS,
    IMAGE_EXTENSIONS,
    LONG_MESSAGE_FILE_THRESHOLD_CHARS,
    NO_REPORT_SENTINEL,
    PENDING_CONTEXT_TTL_SECONDS,
    PHOTO_EXTENSIONS,
    SAFE_EXTENSIONS,
    SCHEDULE_CLAIM_BATCH_LIMIT,
    SCHEDULE_MAX_EXPANSION_PROMPT_CHARS,
    SCHEDULE_MAX_ORIGINAL_PROMPT_CHARS,
    STATUS_EDIT_DEBOUNCE_SECONDS,
    TELEGRAM_BOT_FILE_MAX_BYTES,
    TELEGRAM_MESSAGE_MAX_CHUNK_SIZE,
    TYPING_HEARTBEAT_INTERVAL_SECONDS,
    VIDEO_EXTENSIONS,
    DEFAULT_SUBPROCESS_TIMEOUT_SECONDS,
)
from hostspark.prompts import (
    COMPACT_CONTEXT_PROMPT,
    DEFAULT_AUDIO_PROMPT,
    DEFAULT_DOCUMENT_PROMPT,
    DEFAULT_IMAGE_PROMPT,
    DEFAULT_VIDEO_PROMPT,
    LEARN_DEFAULT_PROMPT,
    MULTIMODAL_AUDIO_ASR_HINT,
    MULTIMODAL_IMAGE_HINT,
    MULTIMODAL_VIDEO_HINT,
    build_attachment_prompt,
    build_learn_prompt,
    build_prompt_expansion_request,
    compose_agy_prompt,
    compose_followup_prompt,
)


class PromptsAndConstantsTests(unittest.TestCase):
    def test_constants_integrity(self) -> None:
        self.assertIn(".m4a", AUDIO_EXTENSIONS)
        self.assertIn(".mp4", VIDEO_EXTENSIONS)
        self.assertIn(".jpg", IMAGE_EXTENSIONS)
        self.assertIn(".jpg", PHOTO_EXTENSIONS)
        self.assertIn(".svg", IMAGE_EXTENSIONS)
        self.assertNotIn(".svg", PHOTO_EXTENSIONS)
        self.assertIn(".py", DOC_EXTENSIONS)
        self.assertTrue(AUDIO_EXTENSIONS.issubset(SAFE_EXTENSIONS))
        self.assertTrue(VIDEO_EXTENSIONS.issubset(SAFE_EXTENSIONS))
        self.assertTrue(IMAGE_EXTENSIONS.issubset(SAFE_EXTENSIONS))
        self.assertTrue(DOC_EXTENSIONS.issubset(SAFE_EXTENSIONS))

        self.assertEqual(NO_REPORT_SENTINEL, "[NO_REPORT]")
        self.assertEqual(TELEGRAM_MESSAGE_MAX_CHUNK_SIZE, 3500)
        self.assertEqual(LONG_MESSAGE_FILE_THRESHOLD_CHARS, 7000)
        self.assertEqual(TELEGRAM_BOT_FILE_MAX_BYTES, 50 * 1024 * 1024)
        self.assertEqual(DEFAULT_SUBPROCESS_TIMEOUT_SECONDS, 30.0)
        self.assertEqual(PENDING_CONTEXT_TTL_SECONDS, 600.0)
        self.assertGreater(STATUS_EDIT_DEBOUNCE_SECONDS, 0)
        self.assertGreater(TYPING_HEARTBEAT_INTERVAL_SECONDS, 0)
        self.assertEqual(DEFAULT_CIRCUIT_BREAKER_MAX_FAILURES, 3)
        self.assertEqual(DEFAULT_SCHEDULE_CLEANUP_MAX_AGE_DAYS, 30)
        self.assertEqual(DEFAULT_JOB_QUEUE_MAXSIZE, 50)
        self.assertEqual(SCHEDULE_CLAIM_BATCH_LIMIT, 10)
        self.assertEqual(SCHEDULE_MAX_ORIGINAL_PROMPT_CHARS, 4000)
        self.assertEqual(SCHEDULE_MAX_EXPANSION_PROMPT_CHARS, 8000)

    def test_build_attachment_prompt(self) -> None:
        path = Path("/tmp/audio.m4a")
        # With caption
        prompt_with_cap = build_attachment_prompt(
            kind="語音/音訊",
            target_path=path,
            caption="翻譯為日語",
            default_prompt=DEFAULT_AUDIO_PROMPT,
            hint=MULTIMODAL_AUDIO_ASR_HINT,
        )
        self.assertIn("使用者上傳了語音/音訊：`/tmp/audio.m4a`", prompt_with_cap)
        self.assertIn("說明：翻譯為日語", prompt_with_cap)
        self.assertIn("view_file", prompt_with_cap)
        self.assertIn("Gemini 具備原生音訊辨識", prompt_with_cap)

        # Without caption (uses default)
        prompt_no_cap = build_attachment_prompt(
            kind="影片",
            target_path="/tmp/video.mp4",
            caption=None,
            default_prompt=DEFAULT_VIDEO_PROMPT,
            hint=MULTIMODAL_VIDEO_HINT,
        )
        self.assertIn("說明：請分析此影片內容並提供摘要。", prompt_no_cap)
        self.assertIn("view_file", prompt_no_cap)

        # Image and Document prompts
        prompt_img = build_attachment_prompt(
            kind="圖片",
            target_path="/tmp/img.png",
            caption=None,
            default_prompt=DEFAULT_IMAGE_PROMPT,
            hint=MULTIMODAL_IMAGE_HINT,
        )
        self.assertIn("說明：請分析此圖片並說明其內容。", prompt_img)

        prompt_doc = build_attachment_prompt(
            kind="文件",
            target_path="/tmp/code.py",
            caption=None,
            default_prompt=DEFAULT_DOCUMENT_PROMPT,
            hint="",
        )
        self.assertIn("說明：請分析此附件並提供摘要。", prompt_doc)

    def test_build_learn_prompt(self) -> None:
        self.assertEqual(build_learn_prompt(None), LEARN_DEFAULT_PROMPT)
        self.assertEqual(build_learn_prompt(""), LEARN_DEFAULT_PROMPT)
        prompt_custom = build_learn_prompt("請記住這條規則")
        self.assertIn("請記住這條規則", prompt_custom)

    def test_compact_context_prompt(self) -> None:
        self.assertIn("請壓縮目前對話的上下文", COMPACT_CONTEXT_PROMPT)

    def test_compose_followup_prompt(self) -> None:
        combined = compose_followup_prompt(["First request", "Second request"], "Third request")
        self.assertIn("First request\n\nSecond request\n\n[Update / Follow-up]:\nThird request", combined)

    def test_compose_agy_prompt(self) -> None:
        self.assertEqual(compose_agy_prompt("User query", ""), "User query")
        combined = compose_agy_prompt("User query", "System rule")
        self.assertEqual(combined, "System rule\n\n使用者請求：\nUser query")

    def test_build_prompt_expansion_request(self) -> None:
        res = build_prompt_expansion_request(
            original_prompt="備份資料庫",
            cron_expr="0 3 * * *",
            timezone_name="Asia/Taipei",
        )
        self.assertIn("cron：0 3 * * *", res)
        self.assertIn("時區：Asia/Taipei", res)
        self.assertIn("使用者要求：\n備份資料庫", res)
        self.assertIn("[NO_REPORT]", res)


if __name__ == "__main__":
    unittest.main()

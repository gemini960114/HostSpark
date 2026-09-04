import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import hostspark.state as state
from hostspark.config import BotConfig
from hostspark.telegram.dispatcher import handle_attachment


class AttachmentHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.orig_config = state.CONFIG
        self.orig_job_queue = state.JOB_QUEUE
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workdir = Path(self.temp_dir.name)
        self.uploads_dir = self.workdir / "uploads"
        self.uploads_dir.mkdir(parents=True, exist_ok=True)

        self.config = BotConfig(
            bot_token=f"{987654321}:{'A' * 25}",
            agy_bin=Path("/bin/echo"),
            agy_workdir=self.workdir,
            workspace_root=self.workdir,
            permission_mode="full",
            rule_prompt="",
            timeout_seconds=60,
            max_output_bytes=100000,
            schedule_db_path=self.workdir / "schedules.db",
            schedule_timezone="Asia/Taipei",
            schedule_min_interval_minutes=15,
            schedule_max_tasks=20,
            allowed_user_ids=frozenset({12345}),
            private_only=False,
        )
        state.CONFIG = self.config

    async def asyncTearDown(self) -> None:
        state.CONFIG = self.orig_config
        state.JOB_QUEUE = self.orig_job_queue
        self.temp_dir.cleanup()

    def _make_update(self, user_id=12345, chat_id=10001, **message_kwargs):
        user = SimpleNamespace(id=user_id, is_bot=False)
        chat = SimpleNamespace(id=chat_id, type="private")
        replies = []

        async def reply_text(text, **kwargs):
            replies.append(text)
            return SimpleNamespace(message_id=999, edit_text=AsyncMock())

        msg_dict = {
            "from_user": user,
            "chat": chat,
            "reply_text": AsyncMock(side_effect=reply_text),
            "caption": None,
            "photo": None,
            "audio": None,
            "voice": None,
            "video": None,
            "video_note": None,
            "document": None,
        }
        msg_dict.update(message_kwargs)
        message = SimpleNamespace(**msg_dict)
        update = SimpleNamespace(
            effective_user=user,
            effective_chat=chat,
            message=message,
        )
        context = SimpleNamespace()
        return update, context, replies

    async def test_unauthorized_attachment_rejected(self) -> None:
        update, context, replies = self._make_update(user_id=99999)
        await handle_attachment(update, context)
        self.assertEqual(len(replies), 1)
        self.assertIn("沒有權限", replies[0])

    async def test_unsupported_extension_rejected(self) -> None:
        file_obj = SimpleNamespace(
            file_unique_id="uniq1234",
            download_to_drive=AsyncMock(),
        )
        doc = SimpleNamespace(
            file_name="malware.exe",
            get_file=AsyncMock(return_value=file_obj),
        )
        update, context, replies = self._make_update(document=doc)
        await handle_attachment(update, context)
        self.assertEqual(len(replies), 1)
        self.assertIn("不支援此副檔名", replies[0])
        file_obj.download_to_drive.assert_not_called()

    @patch("hostspark.telegram.dispatcher._enqueue_and_handle_prompt", new_callable=AsyncMock)
    async def test_audio_attachment_injects_asr_hint(self, mock_enqueue) -> None:
        file_obj = SimpleNamespace(
            file_unique_id="audio999",
            download_to_drive=AsyncMock(),
        )
        audio = SimpleNamespace(
            file_name="voice_note.m4a",
            mime_type="audio/mp4",
            get_file=AsyncMock(return_value=file_obj),
        )
        update, context, replies = self._make_update(audio=audio, caption="請幫我翻譯為英文")

        await handle_attachment(update, context)
        file_obj.download_to_drive.assert_called_once()
        self.assertTrue(any("已儲存語音/音訊" in r for r in replies))

        mock_enqueue.assert_called_once()
        prompt = mock_enqueue.call_args[0][2]
        self.assertIn("使用者上傳了語音/音訊", prompt)
        self.assertIn("說明：請幫我翻譯為英文", prompt)
        self.assertIn("Gemini 具備原生音訊辨識", prompt)
        self.assertIn("view_file", prompt)

    @patch("hostspark.telegram.dispatcher._enqueue_and_handle_prompt", new_callable=AsyncMock)
    async def test_video_attachment_injects_video_hint(self, mock_enqueue) -> None:
        file_obj = SimpleNamespace(
            file_unique_id="vid888",
            download_to_drive=AsyncMock(),
        )
        video = SimpleNamespace(
            file_name="clip.mp4",
            get_file=AsyncMock(return_value=file_obj),
        )
        update, context, replies = self._make_update(video=video)

        await handle_attachment(update, context)
        file_obj.download_to_drive.assert_called_once()
        self.assertTrue(any("已儲存影片" in r for r in replies))

        mock_enqueue.assert_called_once()
        prompt = mock_enqueue.call_args[0][2]
        self.assertIn("使用者上傳了影片", prompt)
        self.assertIn("說明：請分析此影片內容並提供摘要。", prompt)
        self.assertIn("view_file", prompt)

    @patch("hostspark.telegram.dispatcher._enqueue_and_handle_prompt", new_callable=AsyncMock)
    async def test_photo_attachment_injects_image_hint(self, mock_enqueue) -> None:
        file_obj = SimpleNamespace(
            file_unique_id="photo777",
            download_to_drive=AsyncMock(),
        )
        photo_size = SimpleNamespace(
            file_unique_id="photo777",
            get_file=AsyncMock(return_value=file_obj),
        )
        update, context, replies = self._make_update(photo=[photo_size])

        await handle_attachment(update, context)
        file_obj.download_to_drive.assert_called_once()
        self.assertTrue(any("已儲存圖片" in r for r in replies))

        mock_enqueue.assert_called_once()
        prompt = mock_enqueue.call_args[0][2]
        self.assertIn("使用者上傳了圖片", prompt)
        self.assertIn("說明：請分析此圖片並說明其內容。", prompt)
        self.assertIn("view_file", prompt)

    @patch("hostspark.telegram.dispatcher._enqueue_and_handle_prompt", new_callable=AsyncMock)
    async def test_voice_attachment_assigns_ogg(self, mock_enqueue) -> None:
        file_obj = SimpleNamespace(
            file_unique_id="voice666",
            download_to_drive=AsyncMock(),
        )
        voice = SimpleNamespace(
            file_unique_id="voice666",
            get_file=AsyncMock(return_value=file_obj),
        )
        update, context, replies = self._make_update(voice=voice)

        await handle_attachment(update, context)
        file_obj.download_to_drive.assert_called_once()
        self.assertTrue(any("已儲存語音/音訊" in r for r in replies))

        mock_enqueue.assert_called_once()
        prompt = mock_enqueue.call_args[0][2]
        self.assertIn(".ogg", prompt)
        self.assertIn("Gemini 具備原生音訊辨識", prompt)


if __name__ == "__main__":
    unittest.main()

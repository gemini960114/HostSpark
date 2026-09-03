import asyncio
import unittest

from hostspark.runtime.job_queue import Job, JobQueue


class JobQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_enqueue_and_process(self) -> None:
        queue = JobQueue()
        processed = []

        async def handler(job: Job) -> str:
            processed.append(job.prompt)
            await asyncio.sleep(0.01)
            return f"Processed: {job.prompt}"

        queue.start(handler)

        job1, _ = queue.enqueue(100, 1, "task 1", auto_interrupt=False)
        job2, _ = queue.enqueue(100, 1, "task 2", auto_interrupt=False)

        await job1.done_event.wait()
        await job2.done_event.wait()

        self.assertEqual(processed, ["task 1", "task 2"])
        self.assertEqual(job1.result, "Processed: task 1")
        self.assertEqual(job2.result, "Processed: task 2")

        await queue.stop()

    async def test_auto_interrupt_merge(self) -> None:
        queue = JobQueue()
        started_event = asyncio.Event()

        async def slow_handler(job: Job) -> str:
            started_event.set()
            await asyncio.sleep(0.5)
            return "done"

        queue.start(slow_handler)

        job1, merged1 = queue.enqueue(100, 1, "task 1", auto_interrupt=True)
        self.assertFalse(merged1)
        await started_event.wait()

        # Enqueue follow-up while job1 is running
        job2, merged2 = queue.enqueue(100, 1, "task 2", auto_interrupt=True)
        self.assertTrue(merged2)
        self.assertIn("task 1", job2.prompt)
        self.assertIn("[Update / Follow-up]:\ntask 2", job2.prompt)
        self.assertTrue(job1.cancelled)

        await queue.stop()

    async def test_cancel_for_chat(self) -> None:
        queue = JobQueue()
        started_event = asyncio.Event()

        async def slow_handler(job: Job) -> str:
            started_event.set()
            await asyncio.sleep(1.0)
            return "done"

        queue.start(slow_handler)

        job1, _ = queue.enqueue(100, 1, "task 1", auto_interrupt=False)
        job2, _ = queue.enqueue(100, 1, "task 2", auto_interrupt=False)
        await started_event.wait()

        cancelled = queue.cancel_for_chat(100)
        self.assertTrue(cancelled)
        self.assertTrue(job1.cancelled)
        self.assertTrue(job2.cancelled)

        await queue.stop()


if __name__ == "__main__":
    unittest.main()

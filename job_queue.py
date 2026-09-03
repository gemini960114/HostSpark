"""Backward compatibility adapter for job_queue."""
from hostspark.runtime.job_queue import Job, JobQueue

__all__ = ["Job", "JobQueue"]

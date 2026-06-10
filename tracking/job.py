"""TrackingJob — à implémenter."""
from notion.reader import NotionReader, TrackedGame
from notion.writer import NotionWriter


class TrackingJob:
    async def run(self): pass
    def close(self): pass

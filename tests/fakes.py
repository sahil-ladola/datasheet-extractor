"""Test doubles shared across test modules.

A "fake" is a real implementation of an interface with a simplified
behaviour, as opposed to a "mock" that records calls and returns whatever
it was told. Because the extractor depends on the ``LLMClient`` interface
rather than on a concrete provider, a fake is all the tests need: no
network, no key, no mocking library.
"""

from __future__ import annotations

from datasheet_extractor.extractor import LLMClient


class FakeLLMClient(LLMClient):
    """Returns canned replies in order and records every prompt it was sent.

    Args:
        replies: Strings to return from successive ``complete`` calls. Using
            a list lets a test script a sequence such as "garbage, then a
            good answer" to exercise the retry path.
    """

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    @property
    def calls(self) -> int:
        """How many times ``complete`` has been called."""
        return len(self.prompts)

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.replies:
            raise AssertionError("FakeLLMClient was called more times than it has replies")
        return self.replies.pop(0)

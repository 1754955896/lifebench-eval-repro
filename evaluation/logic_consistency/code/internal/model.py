"""This module connects the evaluation model."""

from __future__ import annotations

from tools.llm import chat_completion

SYSTEM_PROMPT = (
    "You are a rigorous, conscientious and careful text reviewer. "
    "You are not only good at extracting text content, but also can use "
    "your knowledge to locate and analyze errors in AI-generated texts."
)


class Deepseek:


    def __init__(self, model: str = "deepseek-chat", completion_fn=None):
        self.model = model
        self._completion = completion_fn or chat_completion

    def ask(self, prompt):
        result = self._completion(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"{prompt}"},
            ],
            model=self.model,
        )
        return result.content


__all__ = ["Deepseek", "SYSTEM_PROMPT"]

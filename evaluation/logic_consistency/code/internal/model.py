"""旧时空校验的模型适配：保留原 ``Deepseek.ask(prompt)`` 调用形状。

底层只经 ``tools.llm.chat_completion``；凭据与 client 生命周期由共享
工具管理，不在本方法内创建 OpenAI 客户端或硬编码密钥。
"""
from __future__ import annotations

from tools.llm import chat_completion

SYSTEM_PROMPT = (
    "You are a rigorous, conscientious and careful text reviewer. "
    "You are not only good at extracting text content, but also can use "
    "your knowledge to locate and analyze errors in AI-generated texts."
)


class Deepseek:
    """兼容旧调用方的文本模型适配器。"""

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

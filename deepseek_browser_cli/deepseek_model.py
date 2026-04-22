"""Backward-compatibility shim: all symbols re-exported from sub-modules.

The implementation has been split into focused modules:
- models        : data classes (ChatMode, Message, PageState, etc.)
- primitives    : A11yPrimitives (Layer 1 CDP bridge)
- semantics     : DeepSeekSemantics (Layer 2 page actions)
- chat          : DeepSeekChat, MultiRoundChat (Layer 3 workflows)
"""

from deepseek_browser_cli.chat import ChatTurn, DeepSeekChat, MultiRoundChat
from deepseek_browser_cli.models import (
    ChatMode,
    Conversation,
    Message,
    PageState,
    ResponseAction,
    ThinkingTrace,
)
from deepseek_browser_cli.primitives import A11yPrimitives
from deepseek_browser_cli.semantics import DeepSeekSemantics

__all__ = [
    "A11yPrimitives",
    "ChatMode",
    "ChatTurn",
    "Conversation",
    "DeepSeekChat",
    "DeepSeekSemantics",
    "Message",
    "MultiRoundChat",
    "PageState",
    "ResponseAction",
    "ThinkingTrace",
]

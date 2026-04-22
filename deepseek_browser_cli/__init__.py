"""DeepSeek Browser CLI - Semantic browser automation for chat.deepseek.com."""

__version__ = "0.1.0"

from deepseek_browser_cli.chat import ChatTurn, DeepSeekChat, MultiRoundChat
from deepseek_browser_cli.models import (
    ChatMode,
    Conversation,
    Message,
    PageState,
    ResponseAction,
    ThinkingTrace,
)
from deepseek_browser_cli.agent_bridge import (
    DeepSeekActor,
    DeepSeekAgentBridge,
    DeepSeekObserver,
)
from deepseek_browser_cli.primitives import A11yPrimitives
from deepseek_browser_cli.semantics import DeepSeekSemantics

__all__ = [
    "__version__",
    "A11yPrimitives",
    "ChatMode",
    "ChatTurn",
    "Conversation",
    "DeepSeekActor",
    "DeepSeekAgentBridge",
    "DeepSeekChat",
    "DeepSeekObserver",
    "DeepSeekSemantics",
    "Message",
    "MultiRoundChat",
    "PageState",
    "ResponseAction",
    "ThinkingTrace",
]

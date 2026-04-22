import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ChatMode(str, Enum):
    QUICK = "快速模式"
    EXPERT = "专家模式"


@dataclass
class Message:
    role: str
    content: str


@dataclass
class ThinkingTrace:
    content: str
    time: Optional[str]


@dataclass
class Conversation:
    """A conversation entry from the sidebar."""
    title: str
    ref: Optional[str]
    is_active: bool = False


@dataclass
class PageState:
    """Structured page state for state-machine transitions."""
    url: str
    is_initial_page: bool
    has_input: bool
    is_streaming: bool
    message_count: int
    mode: str
    deep_thinking_enabled: bool
    web_search_enabled: bool


@dataclass
class ResponseAction:
    """Action buttons attached to an assistant response."""
    copy_ref: Optional[str]
    regenerate_ref: Optional[str]
    like_ref: Optional[str]
    dislike_ref: Optional[str]
    share_ref: Optional[str]


@dataclass
class ChatTurn:
    """A single turn in a multi-round conversation."""
    user_message: str
    assistant_response: str
    thinking_trace: Optional[ThinkingTrace] = None
    timestamp: float = field(default_factory=time.time)

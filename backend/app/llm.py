from __future__ import annotations

from langchain_openai import ChatOpenAI
from langchain.chat_models import init_chat_model
from langchain.chat_models import BaseChatModel
from app.settings import Settings


def build_chat_model(settings: Settings, *, streaming: bool = False) -> BaseChatModel:
    chat_model = init_chat_model(model="openrouter:deepseek/deepseek-v4-flash")
    return chat_model
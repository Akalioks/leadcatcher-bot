"""Wires routers and shared objects into a Dispatcher."""
from aiogram import Dispatcher

from .config import Config
from .funnel import Funnel
from .handlers import admin, funnel_flow, sales
from .repo import Repo
from .webhooks import Webhooks


def build_dispatcher(cfg: Config, repo: Repo, funnel: Funnel, hooks: Webhooks) -> Dispatcher:
    dp = Dispatcher()  # questionnaire progress is kept in the database, not in memory
    dp["cfg"], dp["repo"], dp["funnel"], dp["hooks"] = cfg, repo, funnel, hooks
    dp.include_routers(admin.router, sales.router, funnel_flow.router)  # commands before the catch-all text handler
    return dp

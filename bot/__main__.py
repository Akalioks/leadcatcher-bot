"""Run with:  python -m bot   — starts the bot, the website form endpoint and the background checks together."""
import asyncio
import logging
import sys

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BotCommand, BotCommandScopeChat
from aiohttp import web

from . import notify
from .app import build_dispatcher
from .config import load_config
from .funnel import FunnelError, load_funnel
from .repo import Repo
from .watcher import watch
from .web import create_web_app
from .webhooks import Webhooks

log = logging.getLogger("leads")

PUBLIC = [BotCommand(command="start", description="Leave a request")]
TEAM = [BotCommand(command="stats", description="Leads statistics"),
        BotCommand(command="leads", description="Latest leads"),
        BotCommand(command="export", description="Download leads as CSV")]


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    cfg = load_config()
    if not cfg.bot_token:
        sys.exit("BOT_TOKEN is empty. Copy .env.example to .env and paste the token from @BotFather.")
    try:
        funnel = load_funnel(cfg.funnel_file)
    except (FunnelError, OSError) as e:
        sys.exit(f"Problem in {cfg.funnel_file}: {e}")
    if not cfg.sales_chats:
        log.warning("ADMIN_IDS and LEADS_CHAT_ID are empty: leads will be saved, but nobody will be notified.")

    repo = await Repo.create(cfg)
    hooks = Webhooks(cfg, repo)
    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await bot.set_my_commands(PUBLIC + TEAM if cfg.demo_mode else PUBLIC)
    for admin_id in cfg.admin_ids:
        try:
            await bot.set_my_commands(PUBLIC + TEAM, scope=BotCommandScopeChat(chat_id=admin_id))
        except TelegramAPIError:
            pass  # the admin hasn't opened the bot yet

    runner = web.AppRunner(create_web_app(cfg, repo, funnel, hooks, bot))
    await runner.setup()
    await web.TCPSite(runner, cfg.host, cfg.port).start()
    log.info("Website form endpoint: http://%s:%s/api/leads", "localhost" if cfg.host == "0.0.0.0" else cfg.host, cfg.port)

    tasks = [asyncio.create_task(watch(bot, cfg, repo, funnel)), asyncio.create_task(hooks.run())]
    try:
        await build_dispatcher(cfg, repo, funnel, hooks).start_polling(bot)
    finally:
        for t in tasks:
            t.cancel()
        await runner.cleanup()
        await notify.drain()
        await hooks.close()
        await repo.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

from aiogram import Dispatcher

from app.bot.handlers import router
from app.bot.middleware import ContainerMiddleware, DatabaseSessionMiddleware, RateLimitMiddleware
from app.core.container import AppContainer
from app.core.security import FloodControl


def create_dispatcher(container: AppContainer) -> Dispatcher:
    dp = Dispatcher()
    flood_control = FloodControl(container.redis, requests_per_minute=container.settings.rate_limit_per_minute)

    dp.update.middleware(ContainerMiddleware(container))
    dp.update.middleware(DatabaseSessionMiddleware(container.session_factory))
    dp.message.middleware(RateLimitMiddleware(flood_control))

    dp.include_router(router)
    return dp

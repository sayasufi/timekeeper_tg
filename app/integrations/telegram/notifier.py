from __future__ import annotations

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class TelegramNotifier:
    def __init__(self, bot_token: str) -> None:
        self._bot_token = bot_token
        self._bot: Bot | None = None

    @property
    def bot(self) -> Bot:
        if self._bot is None:
            self._bot = Bot(
                token=self._bot_token,
                default=DefaultBotProperties(parse_mode=ParseMode.HTML),
            )
        return self._bot

    async def send_message(
        self,
        telegram_id: int,
        text: str,
        buttons: list[tuple[str, str]] | None = None,
    ) -> None:
        reply_markup = None
        if buttons:
            reply_markup = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=title, callback_data=data)] for title, data in buttons]
            )
        await self.bot.send_message(chat_id=telegram_id, text=text, reply_markup=reply_markup)

    async def close(self) -> None:
        if self._bot is not None:
            await self._bot.session.close()
            self._bot = None

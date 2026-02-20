from __future__ import annotations

from io import BytesIO
from uuid import UUID

import structlog
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InaccessibleMessage,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.contextvars import bound_contextvars

from app.core.container import AppContainer
from app.repositories.due_notification_repository import DueNotificationRepository
from app.repositories.event_repository import EventRepository
from app.repositories.note_repository import NoteRepository
from app.repositories.payment_transaction_repository import PaymentTransactionRepository
from app.repositories.student_repository import StudentRepository
from app.repositories.user_repository import UserRepository
from app.services.ambiguity_store import AmbiguityStore
from app.services.assistant_response import AssistantResponse
from app.services.command_parser_service import CommandParserService
from app.services.confirmation_store import ConfirmationStore
from app.services.due_index_service import DueIndexService
from app.services.event_service import EventService
from app.services.idempotency_store import IdempotencyStore
from app.services.pending_action_store import PendingAction, PendingActionStore
from app.services.quick_action_store import QuickActionStore
from app.services.smart_agents import UserMemoryAgent

logger = structlog.get_logger(__name__)
router = Router()


@router.message(Command("start"))
async def start_handler(message: Message, container: AppContainer, session: AsyncSession) -> None:
    is_new = False
    if message.from_user is not None:
        user_repo = UserRepository(session)
        _user, is_new = await user_repo.get_or_create_with_status(
            telegram_id=message.from_user.id,
            language=message.from_user.language_code or "ru",
        )

    raw_text = (
        "TimeKeeper готов.\n"
        "Работает в свободном формате: текст и голос.\n"
        "Примеры:\n"
        "- покажи мое расписание на неделю\n"
        "- перенеси Машу со среды 18:00 на пятницу 19:00 только на этой неделе\n"
        "- отметь оплату у Маши 2500\n"
        "- кто пропустил\n"
        "- поставь тихие часы с 22:00 до 08:00"
    )
    text = await _render_for_message_user(
        container=container,
        session=session,
        message=message,
        raw_text=raw_text,
        response_kind="welcome",
    )
    await message.answer(text)
    if is_new:
        hint = await _render_timezone_hint_for_message_user(
            container=container,
            session=session,
            message=message,
        )
        if hint is not None:
            await message.answer(hint)
    await session.commit()


@router.callback_query(F.data.startswith("resolve:"))
async def resolve_callback(callback: CallbackQuery, container: AppContainer, session: AsyncSession) -> None:
    if callback.from_user is None or callback.data is None:
        return
    if not await _register_callback_once(callback, container):
        return

    parts = callback.data.split(":")
    if len(parts) != 3:
        await _answer_callback_notice(
            callback=callback,
            container=container,
            session=session,
            raw_text="Некорректные данные выбора",
            response_kind="callback_error",
            show_alert=True,
        )
        return

    token = parts[1]
    try:
        selected_event_id = UUID(parts[2])
    except ValueError:
        await _answer_callback_notice(
            callback=callback,
            container=container,
            session=session,
            raw_text="Некорректный идентификатор события",
            response_kind="callback_error",
            show_alert=True,
        )
        return

    store = AmbiguityStore(container.redis)
    resolved = await store.get(token)
    if resolved is None:
        await _answer_callback_notice(
            callback=callback,
            container=container,
            session=session,
            raw_text="Время выбора истекло. Повторите запрос.",
            response_kind="callback_expired",
            show_alert=True,
        )
        return

    telegram_id, request = resolved
    if telegram_id != callback.from_user.id:
        await _answer_callback_notice(
            callback=callback,
            container=container,
            session=session,
            raw_text="Эта кнопка не для вас.",
            response_kind="callback_error",
            show_alert=True,
        )
        return

    callback_message = callback.message
    if callback_message is None or isinstance(callback_message, InaccessibleMessage):
        await _answer_callback_notice(
            callback=callback,
            container=container,
            session=session,
            raw_text="Сообщение недоступно",
            response_kind="callback_error",
            show_alert=True,
        )
        return

    assistant = container.create_assistant_service(session)
    response = await assistant.handle_resolution(
        telegram_id=callback.from_user.id,
        language=callback.from_user.language_code or "ru",
        command_payload=request.command_payload,
        selected_event_id=selected_event_id,
    )
    await store.delete(token)

    await _send_assistant_response(
        message=callback_message,
        container=container,
        session=session,
        telegram_id=callback.from_user.id,
        response=response,
    )
    await _answer_callback_notice(
        callback=callback,
        container=container,
        session=session,
        raw_text="Выбор применен",
        response_kind="callback_ok",
        show_alert=False,
    )


@router.callback_query(F.data.startswith("confirm:"))
async def confirm_callback(callback: CallbackQuery, container: AppContainer, session: AsyncSession) -> None:
    if callback.from_user is None or callback.data is None:
        return
    if not await _register_callback_once(callback, container):
        return

    parts = callback.data.split(":")
    if len(parts) != 3:
        await _answer_callback_notice(
            callback=callback,
            container=container,
            session=session,
            raw_text="Некорректные данные подтверждения",
            response_kind="callback_error",
            show_alert=True,
        )
        return

    token = parts[1]
    action = parts[2]
    if action not in {"yes", "no"}:
        await _answer_callback_notice(
            callback=callback,
            container=container,
            session=session,
            raw_text="Некорректное действие",
            response_kind="callback_error",
            show_alert=True,
        )
        return

    store = ConfirmationStore(container.redis)
    resolved = await store.get(token)
    if resolved is None:
        await _answer_callback_notice(
            callback=callback,
            container=container,
            session=session,
            raw_text="Время подтверждения истекло. Повторите запрос.",
            response_kind="callback_expired",
            show_alert=True,
        )
        return

    telegram_id, request = resolved
    if telegram_id != callback.from_user.id:
        await _answer_callback_notice(
            callback=callback,
            container=container,
            session=session,
            raw_text="Эта кнопка не для вас.",
            response_kind="callback_error",
            show_alert=True,
        )
        return

    callback_message = callback.message
    if callback_message is None or isinstance(callback_message, InaccessibleMessage):
        await _answer_callback_notice(
            callback=callback,
            container=container,
            session=session,
            raw_text="Сообщение недоступно",
            response_kind="callback_error",
            show_alert=True,
        )
        return

    assistant = container.create_assistant_service(session)
    response = await assistant.handle_confirmation(
        telegram_id=callback.from_user.id,
        language=callback.from_user.language_code or "ru",
        command_payload=request.command_payload,
        event_id=request.event_id,
        confirmed=(action == "yes"),
    )
    await store.delete(token)

    await _send_assistant_response(
        message=callback_message,
        container=container,
        session=session,
        telegram_id=callback.from_user.id,
        response=response,
    )
    await _answer_callback_notice(
        callback=callback,
        container=container,
        session=session,
        raw_text="Подтверждение принято",
        response_kind="callback_ok",
        show_alert=False,
    )


@router.callback_query(F.data.startswith("snooze:"))
async def snooze_callback(callback: CallbackQuery, container: AppContainer, session: AsyncSession) -> None:
    if callback.from_user is None or callback.data is None:
        return
    if not await _register_callback_once(callback, container):
        return

    parts = callback.data.split(":")
    if len(parts) != 3:
        await _answer_callback_notice(
            callback=callback,
            container=container,
            session=session,
            raw_text="Некорректные данные snooze",
            response_kind="callback_error",
            show_alert=True,
        )
        return

    try:
        minutes = int(parts[1])
        event_id = UUID(parts[2])
    except ValueError:
        await _answer_callback_notice(
            callback=callback,
            container=container,
            session=session,
            raw_text="Некорректные данные snooze",
            response_kind="callback_error",
            show_alert=True,
        )
        return

    user_repo = UserRepository(session)
    user = await user_repo.get_or_create(callback.from_user.id, language=callback.from_user.language_code or "ru")
    event_service = _build_event_service(session, container=container)
    text = await event_service.snooze_event(user=user, event_id=event_id, minutes=minutes)
    text = await container.create_bot_response_service().render_for_user(
        user=user,
        raw_text=text,
        response_kind="snooze_result",
    )
    await session.commit()
    await _answer_callback_notice(
        callback=callback,
        container=container,
        session=session,
        raw_text="Snooze поставлен",
        response_kind="callback_ok",
        show_alert=False,
    )
    callback_message = callback.message
    if callback_message is not None and not isinstance(callback_message, InaccessibleMessage):
        await callback_message.answer(text)


@router.callback_query(F.data.startswith("qa:"))
async def quick_action_callback(callback: CallbackQuery, container: AppContainer, session: AsyncSession) -> None:
    if callback.from_user is None or callback.data is None:
        return
    if not await _register_callback_once(callback, container):
        return
    parts = callback.data.split(":")
    if len(parts) != 3:
        await _answer_callback_notice(
            callback=callback,
            container=container,
            session=session,
            raw_text="Некорректные данные действия",
            response_kind="callback_error",
            show_alert=True,
        )
        return
    token = parts[1]
    try:
        index = int(parts[2])
    except ValueError:
        await _answer_callback_notice(
            callback=callback,
            container=container,
            session=session,
            raw_text="Некорректный выбор действия",
            response_kind="callback_error",
            show_alert=True,
        )
        return
    store = QuickActionStore(container.redis)
    resolved = await store.get(token)
    if resolved is None:
        await _answer_callback_notice(
            callback=callback,
            container=container,
            session=session,
            raw_text="Срок выбора истек. Повторите запрос.",
            response_kind="callback_expired",
            show_alert=True,
        )
        return
    telegram_id, actions = resolved
    if telegram_id != callback.from_user.id or index < 0 or index >= len(actions):
        await _answer_callback_notice(
            callback=callback,
            container=container,
            session=session,
            raw_text="Выбор недоступен.",
            response_kind="callback_error",
            show_alert=True,
        )
        return
    action = actions[index]
    assistant = container.create_assistant_service(session)
    response = await assistant.handle_quick_action(
        telegram_id=callback.from_user.id,
        language=callback.from_user.language_code or "ru",
        action=action.action,
        payload=action.payload,
    )
    await store.delete(token)
    pending_store = PendingActionStore(container.redis)
    await pending_store.clear(callback.from_user.id)
    callback_message = callback.message
    if callback_message is not None and not isinstance(callback_message, InaccessibleMessage):
        await _send_assistant_response(
            message=callback_message,
            container=container,
            session=session,
            telegram_id=callback.from_user.id,
            response=response,
        )
    await _answer_callback_notice(
        callback=callback,
        container=container,
        session=session,
        raw_text="Выполнено",
        response_kind="callback_ok",
        show_alert=False,
    )


@router.callback_query(F.data.startswith("lesson:"))
async def lesson_action_callback(callback: CallbackQuery, container: AppContainer, session: AsyncSession) -> None:
    if callback.from_user is None or callback.data is None:
        return
    if not await _register_callback_once(callback, container):
        return

    parts = callback.data.split(":")
    if len(parts) != 3:
        await _answer_callback_notice(
            callback=callback,
            container=container,
            session=session,
            raw_text="Некорректные данные урока",
            response_kind="callback_error",
            show_alert=True,
        )
        return

    action = parts[1]
    try:
        event_id = UUID(parts[2])
    except ValueError:
        await _answer_callback_notice(
            callback=callback,
            container=container,
            session=session,
            raw_text="Некорректный идентификатор урока",
            response_kind="callback_error",
            show_alert=True,
        )
        return

    user_repo = UserRepository(session)
    user = await user_repo.get_or_create(callback.from_user.id, language=callback.from_user.language_code or "ru")
    event_service = _build_event_service(session, container=container)

    if action == "reschedule":
        pending_store = PendingActionStore(container.redis)
        await pending_store.put(
            callback.from_user.id,
            PendingAction(action="reschedule_lesson", event_id=event_id),
        )
        text = (
            "Куда перенести занятие? Напишите свободно, например:\n"
            "- на пятницу 19:00\n"
            "- на 14 марта 18:30\n"
            "- всегда на среду 18:00"
        )
    elif action == "cancel":
        text = await event_service.cancel_lesson(user=user, event_id=event_id)
    elif action == "paid":
        text = await event_service.mark_lesson_paid(user=user, event_id=event_id)
    elif action == "missed":
        text = await event_service.mark_lesson_missed(user=user, event_id=event_id)
    elif action == "note":
        text = await event_service.add_note_to_lesson(user=user, event_id=event_id)
    else:
        await _answer_callback_notice(
            callback=callback,
            container=container,
            session=session,
            raw_text="Неизвестное действие",
            response_kind="callback_error",
            show_alert=True,
        )
        return

    text = await container.create_bot_response_service().render_for_user(
        user=user,
        raw_text=text,
        response_kind="lesson_action_result",
    )

    await session.commit()
    callback_message = callback.message
    if callback_message is not None and not isinstance(callback_message, InaccessibleMessage):
        await callback_message.answer(text)
    await _answer_callback_notice(
        callback=callback,
        container=container,
        session=session,
        raw_text="Готово",
        response_kind="callback_ok",
        show_alert=False,
    )


@router.message(F.voice)
async def voice_handler(message: Message, container: AppContainer, session: AsyncSession) -> None:
    if message.from_user is None or message.voice is None:
        return
    if not await _register_message_once(message, container, kind="voice"):
        return
    bot = message.bot
    if bot is None:
        return

    with bound_contextvars(
        tg_user_id=message.from_user.id,
        tg_chat_id=message.chat.id,
        tg_message_id=message.message_id,
    ):
        if await _maybe_send_timezone_hint_for_new_user(
            container=container,
            session=session,
            message=message,
        ):
            await session.commit()

        file = await bot.get_file(message.voice.file_id)
        if file.file_path is None:
            text = await _render_for_message_user(
                container=container,
                session=session,
                message=message,
                raw_text="Не удалось получить голосовое сообщение.",
                response_kind="voice_error",
            )
            await message.answer(text)
            return

        buffer = BytesIO()
        await bot.download_file(file.file_path, buffer)
        audio_bytes = buffer.getvalue()

        try:
            text = await container.stt_client.transcribe(audio=audio_bytes, filename=f"{message.voice.file_id}.ogg")
        except Exception as exc:
            logger.exception("voice.transcribe_failed", error=str(exc))
            rendered = await _render_for_message_user(
                container=container,
                session=session,
                message=message,
                raw_text="Не удалось распознать голосовое сообщение.",
                response_kind="voice_error",
            )
            await message.answer(rendered)
            return

        if await _try_handle_pending(
            container=container,
            session=session,
            telegram_id=message.from_user.id,
            language=(message.from_user.language_code or "ru"),
            text=text,
            sink=message,
        ):
            return

        assistant = container.create_assistant_service(session)
        response = await assistant.handle_text(
            telegram_id=message.from_user.id,
            text=text,
            language=(message.from_user.language_code or "ru"),
        )

        voice_response = AssistantResponse(
            text=response.text,
            ambiguity=response.ambiguity,
            confirmation=response.confirmation,
        )
        await _send_assistant_response(
            message=message,
            container=container,
            session=session,
            telegram_id=message.from_user.id,
            response=voice_response,
        )


@router.message(F.text)
async def text_handler(message: Message, container: AppContainer, session: AsyncSession) -> None:
    if message.from_user is None or message.text is None:
        return
    if not await _register_message_once(message, container, kind="text"):
        return

    with bound_contextvars(
        tg_user_id=message.from_user.id,
        tg_chat_id=message.chat.id,
        tg_message_id=message.message_id,
    ):
        if await _maybe_send_timezone_hint_for_new_user(
            container=container,
            session=session,
            message=message,
        ):
            await session.commit()

        if await _try_handle_pending(
            container=container,
            session=session,
            telegram_id=message.from_user.id,
            language=(message.from_user.language_code or "ru"),
            text=message.text,
            sink=message,
        ):
            return

        assistant = container.create_assistant_service(session)
        response = await assistant.handle_text(
            telegram_id=message.from_user.id,
            text=message.text,
            language=(message.from_user.language_code or "ru"),
        )
        await _send_assistant_response(
            message=message,
            container=container,
            session=session,
            telegram_id=message.from_user.id,
            response=response,
        )


async def _try_handle_pending(
    container: AppContainer,
    session: AsyncSession,
    telegram_id: int,
    language: str,
    text: str,
    sink: Message | InaccessibleMessage,
) -> bool:
    pending_store = PendingActionStore(container.redis)
    pending = await pending_store.get(telegram_id)
    if pending is None:
        return False

    user_repo = UserRepository(session)
    await user_repo.get_or_create(telegram_id, language=language)

    if pending.action == "reschedule_lesson":
        assistant = container.create_assistant_service(session)
        response = await assistant.handle_pending_reschedule(
            telegram_id=telegram_id,
            language=language,
            event_id=pending.event_id,
            text=text,
        )
        if not bool(response.metadata.get("pending_keep", False)):
            await pending_store.clear(telegram_id)
        if isinstance(sink, Message):
            await _send_assistant_response(
                message=sink,
                container=container,
                session=session,
                telegram_id=telegram_id,
                response=response,
            )
        return True

    await pending_store.clear(telegram_id)
    return False


async def _send_assistant_response(
    message: Message,
    container: AppContainer,
    session: AsyncSession,
    telegram_id: int,
    response: AssistantResponse,
) -> None:
    if response.ambiguity is not None:
        store = AmbiguityStore(container.redis)
        token = await store.put(telegram_id=telegram_id, request=response.ambiguity)

        buttons = [
            [
                InlineKeyboardButton(
                    text=f"{item.title} ({item.subtitle})",
                    callback_data=f"resolve:{token}:{item.event_id}",
                )
            ]
            for item in response.ambiguity.options
        ]
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        await message.answer(response.text, reply_markup=keyboard)
        return

    if response.confirmation is not None:
        confirmation_store = ConfirmationStore(container.redis)
        token = await confirmation_store.put(telegram_id=telegram_id, request=response.confirmation)
        user_repo = UserRepository(session)
        language = message.from_user.language_code if message.from_user is not None else "ru"
        user = await user_repo.get_or_create(telegram_id=telegram_id, language=language or "ru")
        renderer = container.create_bot_response_service()
        confirm_label = await renderer.render_for_user(
            user=user,
            raw_text="Подтвердить",
            response_kind="button_label",
        )
        cancel_label = await renderer.render_for_user(
            user=user,
            raw_text="Отмена",
            response_kind="button_label",
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text=confirm_label, callback_data=f"confirm:{token}:yes"),
                    InlineKeyboardButton(text=cancel_label, callback_data=f"confirm:{token}:no"),
                ]
            ]
        )
        await message.answer(f"{response.text}\n\n{response.confirmation.summary}", reply_markup=keyboard)
        return

    if response.quick_actions:
        store = QuickActionStore(container.redis)
        token = await store.put(telegram_id=telegram_id, actions=response.quick_actions)
        buttons = [
            [InlineKeyboardButton(text=item.label, callback_data=f"qa:{token}:{idx}")]
            for idx, item in enumerate(response.quick_actions)
        ]
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        await message.answer(response.text, reply_markup=keyboard)
        return

    await message.answer(response.text)


async def _render_for_message_user(
    container: AppContainer,
    session: AsyncSession,
    message: Message,
    raw_text: str,
    response_kind: str,
) -> str:
    if message.from_user is None:
        return raw_text
    user_repo = UserRepository(session)
    user = await user_repo.get_or_create(
        telegram_id=message.from_user.id,
        language=message.from_user.language_code or "ru",
    )
    policy_text = await _render_policy_text_for_user(
        container=container,
        user=user,
        kind="handler_message",
        source_text=raw_text,
        reason=response_kind,
        fallback=raw_text,
    )
    renderer = container.create_bot_response_service()
    return await renderer.render_for_user(
        user=user,
        raw_text=policy_text,
        response_kind=response_kind,
    )


async def _maybe_send_timezone_hint_for_new_user(
    container: AppContainer,
    session: AsyncSession,
    message: Message,
) -> bool:
    if message.from_user is None:
        return False

    user_repo = UserRepository(session)
    user, is_new = await user_repo.get_or_create_with_status(
        telegram_id=message.from_user.id,
        language=message.from_user.language_code or "ru",
    )
    if not is_new:
        return False

    hint = await _render_timezone_hint(
        container=container,
        user=user,
        source_text=f"init_timezone_hint:{message.message_id}",
    )
    await message.answer(hint)
    return True


async def _render_timezone_hint_for_message_user(
    container: AppContainer,
    session: AsyncSession,
    message: Message,
) -> str | None:
    if message.from_user is None:
        return None
    user_repo = UserRepository(session)
    user = await user_repo.get_by_telegram_id(message.from_user.id)
    if user is None:
        return None
    return await _render_timezone_hint(
        container=container,
        user=user,
        source_text="/start",
    )


async def _render_timezone_hint(
    container: AppContainer,
    user: object,
    source_text: str,
) -> str:
    from app.db.models import User

    if not isinstance(user, User):
        return (
            "Сейчас используется часовой пояс [UTC]. "
            "Чтобы изменить, напишите, например: «поставь часовой пояс Алматы»."
        )
    raw_text = (
        f"Сейчас используется часовой пояс [{user.timezone}]. "
        "Чтобы изменить, напишите, например: «поставь часовой пояс Алматы»."
    )
    policy_text = await _render_policy_text_for_user(
        container=container,
        user=user,
        kind="timezone_hint",
        source_text=source_text,
        reason="first_contact_timezone_hint",
        fallback=raw_text,
    )
    renderer = container.create_bot_response_service()
    return await renderer.render_for_user(
        user=user,
        raw_text=policy_text,
        response_kind="timezone_hint",
    )


async def _answer_callback_notice(
    callback: CallbackQuery,
    container: AppContainer,
    session: AsyncSession,
    raw_text: str,
    response_kind: str,
    show_alert: bool,
) -> None:
    from_user = callback.from_user
    if from_user is None:
        await callback.answer(raw_text, show_alert=show_alert)
        return

    user_repo = UserRepository(session)
    user = await user_repo.get_or_create(
        telegram_id=from_user.id,
        language=from_user.language_code or "ru",
    )
    renderer = container.create_bot_response_service()
    policy_text = await _render_policy_text_for_user(
        container=container,
        user=user,
        kind="callback_notice",
        source_text=raw_text,
        reason=response_kind,
        fallback=raw_text,
    )
    text = await renderer.render_for_user(
        user=user,
        raw_text=policy_text,
        response_kind=response_kind,
    )
    await callback.answer(text, show_alert=show_alert)


def _build_event_service(session: AsyncSession, container: AppContainer | None = None) -> EventService:
    return EventService(
        EventRepository(session),
        due_index_service=DueIndexService(DueNotificationRepository(session)),
        note_repository=NoteRepository(session),
        student_repository=StudentRepository(session),
        payment_repository=PaymentTransactionRepository(session),
        redis=(container.redis if container is not None else None),
    )


async def _register_message_once(message: Message, container: AppContainer, kind: str) -> bool:
    if message.from_user is None:
        return True
    key = f"msg:{kind}:{message.from_user.id}:{message.chat.id}:{message.message_id}"
    store = IdempotencyStore(container.redis)
    return await store.register_once(key)


async def _register_callback_once(callback: CallbackQuery, container: AppContainer) -> bool:
    if callback.from_user is None:
        return True
    key = f"cb:{callback.from_user.id}:{callback.id}"
    store = IdempotencyStore(container.redis)
    return await store.register_once(key)


async def _render_policy_text_for_user(
    *,
    container: AppContainer,
    user: object,
    kind: str,
    source_text: str,
    reason: str,
    fallback: str,
) -> str:
    from app.db.models import User

    if not isinstance(user, User):
        return fallback

    parser = CommandParserService(container.llm_client)
    memory_agent = UserMemoryAgent()
    user_memory = memory_agent.build_profile(user)
    return await parser.render_policy_text(
        kind=kind,
        source_text=source_text,
        reason=reason,
        locale=user.language,
        timezone=user.timezone,
        fallback=fallback,
        user_memory=user_memory,
    )

from __future__ import annotations

import json
from typing import Any

__all__ = [
    "build_batch_commands_prompt",
    "build_bot_reply_prompt",
    "build_choice_options_prompt",
    "build_clarify_prompt",
    "build_command_prompt",
    "build_context_compressor_prompt",
    "build_conversation_manager_prompt",
    "build_execution_supervisor_prompt",
    "build_help_knowledge_prompt",
    "build_intent_prompt",
    "build_plan_repair_prompt",
    "build_primary_assistant_prompt",
    "build_recovery_prompt",
    "build_recurrence_prompt",
    "build_response_policy_prompt",
    "build_risk_policy_prompt",
    "build_task_chunking_prompt",
    "build_task_graph_prompt",
    "build_telegram_format_prompt",
    "default_clarify_question",
]


def _contract_header() -> str:
    return (
        "Ты работаешь как production-агент TimeKeeper. "
        "Отвечай только валидным JSON, без markdown и без рассуждений вслух. "
        "Единый формат ответа: "
        '{"result": {...}, "confidence": 0.0, "needs_clarification": false, '
        '"clarify_question": null, "reasons": []}. '
        "Если данных недостаточно или есть неоднозначность, не фантазируй: "
        "верни needs_clarification=true и один конкретный clarify_question. "
        "Все даты/время интерпретируй в timezone пользователя и нормализуй к UTC без исключений. "
        "Фразы 'сегодня', 'завтра', 'послезавтра' при известной timezone являются однозначными. "
        "Пиши по-русски, кроме технических значений (intent/enum/RRULE/timezone)."
    )


def _memory_block(user_memory: dict[str, Any] | None) -> str:
    if not user_memory:
        return ""
    return f"\nПамять пользователя (структурировано): {json.dumps(user_memory, ensure_ascii=False)}"


def _help_capabilities_block() -> str:
    return (
        "База знаний TimeKeeper:\n"
        "- Бот понимает свободный текст и голосовые сообщения.\n"
        "- Умеет: создать/изменить/удалить напоминания, уроки, дни рождения, заметки.\n"
        "- Умеет показывать расписание: на сегодня, конкретную дату, неделю, все активные события.\n"
        "- Для репетитора: ученики, отметка оплаты, отметка пропуска, дневные/завтрашние/пропущенные отчеты.\n"
        "- Поддерживает повторы (RRULE), таймзоны, quiet/work hours, переносы уроков.\n"
        "- Если запрос неоднозначный, бот задает точный уточняющий вопрос.\n"
        "- Для операций изменения данных бот может попросить подтверждение."
    )


def build_intent_prompt(
    text: str,
    locale: str,
    timezone: str,
    user_memory: dict[str, Any] | None = None,
) -> str:
    return (
        f"{_contract_header()} "
        "Классифицируй намерение пользователя. "
        "Допустимые intent: create_reminder, update_reminder, delete_reminder, list_events, "
        "create_note, update_note, delete_note, list_notes, "
        "create_schedule, update_schedule, mark_lesson_paid, mark_lesson_missed, create_student, delete_student, update_student, "
        "student_card, parse_bank_transfer, update_settings, tutor_report, create_birthday, clarify. "
        "result должен быть объектом: {\"intent\": \"...\"}. "
        "Сложные случаи:\n"
        "1) 'удали оплату' -> может быть несколько событий, обычно delete_reminder + needs_clarification=true.\n"
        "2) 'перенеси Машу с среды 18:00 на пятницу 19:00 только на этой неделе' -> update_schedule.\n"
        "3) 'отметь оплату у Маши 2500' -> mark_lesson_paid.\n"
        "4) 'отметь пропуск у Ивана' -> mark_lesson_missed.\n"
        "5) 'покажи расписание на неделю по Маше' -> list_events + student_name='Маша'.\n"
        "6) Многострочный текст-расписание -> create_schedule со slots.\n"
        "7) 'поставь тихие часы с 22:00 до 08:00' -> update_settings.\n"
        "8) 'кто пропустил' -> tutor_report(report_type='missed').\n"
        "9) 'финансы за неделю/месяц' -> tutor_report(report_type='finance_week|finance_month').\n"
        "10) 'журнал отмен за неделю/месяц' -> tutor_report(report_type='attendance_week|attendance_month').\n"
        "11) 'буфер между уроками 15 минут' -> update_settings(min_buffer_minutes=15).\n"
        "12) 'у Маши уже оплачено 6 занятий' -> mark_lesson_paid с search_text='Маша' и prepaid_lessons_set=6.\n"
        "13) 'добавь Пете еще 3 оплаченных занятия вперед' -> mark_lesson_paid с prepaid_lessons_add=3.\n"
        "14) 'Маша перевела 10000' -> mark_lesson_paid с search_text='Маша', payment_total=10000.\n"
        "15) Если в оплате не указан ученик, нужен clarify-вопрос: 'Кто оплатил?'.\n"
        "16) 'у Маши цена занятия 2500' -> update_student(student_name='Маша', lesson_price=2500).\n"
        "17) 'измени цену Ивана на 3000' -> update_student.\n"
        "18) 'покажи Машу'/'история Маши'/'баланс Маши' -> student_card(view='card|history|balance').\n"
        "19) Текст банка 'Перевод 10000 от Мария' -> parse_bank_transfer.\n"
        "20) 'все четверги сдвинь на 30 минут' -> update_schedule(apply_to_all=true, shift_weekday='TH', shift_minutes=30).\n"
        "21) 'отмени все уроки в четверг на следующей неделе' -> update_schedule(bulk_cancel_weekday='TH', bulk_cancel_scope='next_week').\n"
        "22) 'поставь Машу на паузу' -> update_student(status='paused').\n"
        "23) 'цель Маши ЕГЭ, уровень B1, 2 раза в неделю' -> update_student(goal, level, weekly_frequency).\n"
        "24) 'добавь ученика Маша, цена 2500' -> create_student.\n"
        "25) 'удали ученика Ивана и его будущие уроки' -> delete_student(delete_future_lessons=true).\n"
        "26) 'поставь часовой пояс Москва' / 'часовой пояс Europe/Moscow' / 'хочу московское время' -> update_settings(timezone).\n"
        "27) Не проси уточнение для относительных дат ('сегодня/завтра/послезавтра'), если запрос содержит время.\n"
        f"Локаль: {locale}. Таймзона: {timezone}."
        f"{_memory_block(user_memory)}\n"
        f"Текст пользователя: {text}"
    )


def build_command_prompt(
    text: str,
    locale: str,
    timezone: str,
    intent: str,
    schema: dict[str, Any],
    user_memory: dict[str, Any] | None = None,
) -> str:
    return (
        f"{_contract_header()} "
        "Сформируй строгую команду для backend. "
        "result должен быть JSON-объектом команды, строго соответствующим schema. "
        "Если не хватает полей для валидной команды, не выдумывай: needs_clarification=true. "
        "Сложные случаи:\n"
        "1) 'напомни каждый второй вторник до конца мая' -> recurrence с RRULE.\n"
        "2) Для расписания репетитора использовать student_name; subject/format/link не обязательны.\n"
        "3) Для update_schedule поле apply_scope обязательно:\n"
        "   - 'single_week' только если пользователь явно сказал разовый перенос (например: 'только на этой неделе', 'разово').\n"
        "   - 'series' только если пользователь явно сказал постоянное изменение (например: 'навсегда', 'каждую неделю теперь').\n"
        "   - если scope неясен, ставь apply_scope=null и needs_clarification=true с точным вопросом.\n"
        "4) 'перенеси на другую дату/время' -> заполняй new_date/new_time.\n"
        "5) 'измени напоминание про оплату' без даты -> проси уточнение, что менять.\n"
        "6) Поддерживай импорт расписания из свободного текста, например:\n"
        "   'Пн 17:00 Маша 60\\nСр 18:30 Иван 90' -> slots[]\n"
        "7) Для mark_lesson_paid поддерживай предоплату на любое число занятий:\n"
        "   - prepaid_lessons_add: добавить N занятий к остатку,\n"
        "   - prepaid_lessons_set: установить остаток M (например при переходе в бота уже был остаток),\n"
        "   - payment_total: общая сумма платежа при пополнении вперед.\n"
        "8) Если пользователь пишет только сумму перевода, передай payment_total и не выдумывай количество занятий.\n"
        "9) Если ученик не указан, needs_clarification=true и вопрос 'Какого ученика отметить по оплате?'.\n"
        "10) Для update_student обязательно student_name, lesson_price > 0.\n"
        "11) Для student_card обязательны student_name и view.\n"
        "12) Для parse_bank_transfer передай raw_text исходного банковского сообщения.\n"
        "13) Для массовых операций расписания используй apply_to_all/shift_* или bulk_cancel_*.\n"
        "14) Для update_student поддерживай CRM поля: status(active|paused|left), goal, level, weekly_frequency, preferred_slots.\n"
        "15) Для create_student и delete_student обязательно передавай student_name.\n"
        "16) Для update_settings при смене часового пояса передавай timezone в формате IANA (Europe/Moscow, Asia/Almaty и т.д.). Примеры: 'Москва' -> Europe/Moscow, 'Алматы' -> Asia/Almaty.\n"
        "17) Для create_reminder/update_reminder с указанием даты и времени всегда заполняй start_at.\n"
        "18) Для относительных дат ('сегодня/завтра/послезавтра') не задавай clarify-вопрос про день, если есть время.\n"
        "19) Когда можно, отдавай start_at в ISO UTC (например, 2026-02-21T07:00:00+00:00).\n"
        f"Локаль: {locale}. Таймзона: {timezone}. Intent: {intent}."
        f"{_memory_block(user_memory)}\n"
        f"Schema: {json.dumps(schema, ensure_ascii=False)}\n"
        f"Текст пользователя: {text}"
    )


def build_batch_commands_prompt(
    *,
    operations: list[str],
    locale: str,
    timezone: str,
    schema: dict[str, Any],
    user_memory: dict[str, Any] | None = None,
) -> str:
    return (
        f"{_contract_header()} "
        "Ты BatchCommandAgent для TimeKeeper. "
        "Построй команды backend сразу для всего списка операций за один проход. "
        "Верни result формата: "
        '{"commands":[{"index":0,"command":{...}}]}. '
        "index — индекс операции из входного списка. command — валидный объект команды по schema. "
        "Не пропускай операции без причины. Если по операции не хватает данных, верни command с intent=clarify и точным вопросом. "
        "Не добавляй новых операций и не меняй их порядок по смыслу. "
        f"Локаль: {locale}. Таймзона: {timezone}."
        f"{_memory_block(user_memory)}\n"
        f"Schema: {json.dumps(schema, ensure_ascii=False)}\n"
        f"Операции: {json.dumps(operations, ensure_ascii=False)}"
    )


def build_recovery_prompt(
    raw_command: str,
    locale: str,
    timezone: str,
    intent: str,
    schema: dict[str, Any],
    user_memory: dict[str, Any] | None = None,
) -> str:
    return (
        f"{_contract_header()} "
        "Восстанови невалидный JSON команды. "
        "result должен соответствовать schema. "
        "Если восстановить корректно нельзя, верни needs_clarification=true и точный вопрос. "
        f"Локаль: {locale}. Таймзона: {timezone}. Intent: {intent}."
        f"{_memory_block(user_memory)}\n"
        f"Schema: {json.dumps(schema, ensure_ascii=False)}\n"
        f"Невалидный ответ: {raw_command}"
    )


def build_clarify_prompt(
    text: str,
    locale: str,
    timezone: str,
    user_memory: dict[str, Any] | None = None,
) -> str:
    return (
        f"{_contract_header()} "
        "Сформируй один точный уточняющий вопрос. "
        "result должен быть объектом: {\"question\": \"...\", \"why\": \"...\"}. "
        "Запрещено задавать общий вопрос типа 'уточните запрос'. "
        "Вопрос должен быть проверяемым и конкретным. "
        f"Локаль: {locale}. Таймзона: {timezone}."
        f"{_memory_block(user_memory)}\n"
        f"Текст пользователя: {text}"
    )


def build_recurrence_prompt(
    text: str,
    locale: str,
    timezone: str,
    user_memory: dict[str, Any] | None = None,
) -> str:
    return (
        f"{_contract_header()} "
        "Извлеки правило повторения. "
        "result формат: {\"rrule\": \"строка| null\", \"until\": \"ISO-UTC | null\"}. "
        "Если повтора нет, rrule=null. "
        "Сложные случаи:\n"
        "1) 'каждый второй вторник' -> FREQ=MONTHLY;BYDAY=TU;BYSETPOS=2\n"
        "2) 'по будням' -> FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR\n"
        "3) 'до конца мая' -> until в UTC.\n"
        f"Локаль: {locale}. Таймзона: {timezone}."
        f"{_memory_block(user_memory)}\n"
        f"Текст пользователя: {text}"
    )


def build_primary_assistant_prompt(
    text: str,
    locale: str,
    timezone: str,
    user_memory: dict[str, Any] | None = None,
) -> str:
    return (
        f"{_contract_header()} "
        "Ты главный assistant-агент TimeKeeper. "
        "Твоя задача: решить, нужно ли просто ответить пользователю о функционале бота, "
        "или делегировать запрос в command-агентов для выполнения действия. "
        "Верни result формата: "
        '{"mode":"answer|delegate","answer":"строка или null"}. '
        "Правила:\n"
        "1) Если пользователь спрашивает 'как это работает', 'что умеет бот', 'как сделать ...' -> mode=answer.\n"
        "2) Если пользователь просит выполнить действие (создать/изменить/удалить/показать данные) -> mode=delegate.\n"
        "3) Не выдумывай действия сам, для операций всегда delegate.\n"
        f"Локаль: {locale}. Таймзона: {timezone}."
        f"{_memory_block(user_memory)}\n"
        f"Текст пользователя: {text}"
    )


def build_help_knowledge_prompt(
    text: str,
    locale: str,
    timezone: str,
    user_memory: dict[str, Any] | None = None,
) -> str:
    return (
        f"{_contract_header()} "
        "Ты HelpKnowledgeAgent для TimeKeeper. "
        "Отвечай кратко, предметно, на русском языке. "
        "Не выполняй операций и не обещай того, чего нет в базе знаний. "
        'Верни result формата: {"answer":"строка"}.\n'
        f"{_help_capabilities_block()}\n"
        f"Локаль: {locale}. Таймзона: {timezone}."
        f"{_memory_block(user_memory)}\n"
        f"Вопрос пользователя: {text}"
    )


def build_bot_reply_prompt(
    *,
    raw_text: str,
    user_text: str | None,
    locale: str,
    timezone: str,
    response_kind: str,
    user_memory: dict[str, Any] | None = None,
) -> str:
    return (
        f"{_contract_header()} "
        "Ты BotReplyAgent для TimeKeeper. "
        "Переформулируй ответ бота естественно и дружелюбно, но строго по смыслу raw_text. "
        "Нельзя менять факты, даты, суммы, идентификаторы и смысл операции. "
        "Даты и время в raw_text — результат работы системы, источник истины. Копируй их буквально (например 21.02.2026, 10:00), не подменяй по контексту. "
        'Верни result формата: {"text":"строка"}.\n'
        f"Тип ответа: {response_kind}.\n"
        f"raw_text: {raw_text}\n"
        f"user_text: {user_text or ''}\n"
        f"Локаль: {locale}. Таймзона: {timezone}."
        f"{_memory_block(user_memory)}"
    )


def build_conversation_manager_prompt(
    text: str,
    locale: str,
    timezone: str,
    user_memory: dict[str, Any] | None = None,
) -> str:
    return (
        f"{_contract_header()} "
        "Ты ConversationManagerAgent для TimeKeeper. "
        "Реши маршрут обработки одного сообщения: ответить справкой, запросить уточнение или запустить операции. "
        'Верни result формата: {"mode":"answer|commands|clarify","operations":["..."],"answer":"...|null","question":"...|null"}.\n'
        "Правила:\n"
        "1) commands: если в тексте есть любое исполнимое действие над данными.\n"
        "2) answer: только для справочных вопросов про функционал и подсказки по использованию.\n"
        "3) clarify: когда для безопасного действия критически не хватает данных.\n"
        "4) operations разделяй по смыслу, а не по символам; понимай свободную речь и скрытые мульти-действия.\n"
        "5) Не выдумывай факты. Если не уверен, mode=clarify и один точный вопрос.\n"
        "6) Не отправляй в clarify, если в запросе есть относительная дата ('сегодня/завтра/послезавтра') и конкретное время.\n"
        f"Локаль: {locale}. Таймзона: {timezone}."
        f"{_memory_block(user_memory)}\n"
        f"Текст пользователя: {text}"
    )


def build_task_chunking_prompt(
    *,
    text: str,
    locale: str,
    timezone: str,
    user_memory: dict[str, Any] | None = None,
) -> str:
    return (
        f"{_contract_header()} "
        "Ты TaskChunkingAgent для TimeKeeper. "
        "Разбей пользовательский запрос на минимальные исполнимые операции по смыслу. "
        'Верни result формата: {"operations":["..."]}.\n'
        "Правила:\n"
        "1) Разделяй только по действиям над данными, а не по знакам пунктуации.\n"
        "2) Сохраняй формулировки близкими к исходному тексту пользователя.\n"
        "3) Не добавляй операции, которых нет в запросе.\n"
        "4) Если действие одно, верни массив из одного элемента.\n"
        "5) Если это только справочный вопрос, верни пустой массив.\n"
        f"Локаль: {locale}. Таймзона: {timezone}."
        f"{_memory_block(user_memory)}\n"
        f"Текст пользователя: {text}"
    )


def build_task_graph_prompt(
    *,
    text: str,
    operations: list[str],
    locale: str,
    timezone: str,
    user_memory: dict[str, Any] | None = None,
) -> str:
    return (
        f"{_contract_header()} "
        "Ты TaskGraphAgent для TimeKeeper. "
        "Собери финальный исполнимый граф операций для backend: убери дубли, выставь безопасный порядок. "
        'Верни result формата: {"operations":["..."],"execution_mode":"continue_on_error|stop_on_error"}.\n'
        "Правила:\n"
        "1) Не добавляй новые факты и действия.\n"
        "2) Если шаги зависят друг от друга, ставь execution_mode=stop_on_error.\n"
        "3) Если шаги независимы, ставь execution_mode=continue_on_error.\n"
        "4) Сохраняй максимум исходного смысла, не переформулируй без необходимости.\n"
        "5) Для длинных сообщений с множеством задач верни полный порядок выполнения.\n"
        f"Локаль: {locale}. Таймзона: {timezone}."
        f"{_memory_block(user_memory)}\n"
        f"Текст пользователя: {text}\n"
        f"Черновые операции: {json.dumps(operations, ensure_ascii=False)}"
    )


def build_risk_policy_prompt(
    *,
    text: str,
    operations: list[str],
    locale: str,
    timezone: str,
    user_memory: dict[str, Any] | None = None,
) -> str:
    return (
        f"{_contract_header()} "
        "Ты RiskPolicyAgent для TimeKeeper. "
        "Оцени риск плана и сформируй краткий preview изменений для пользователя. "
        'Верни result формата: {"requires_confirmation":true|false,"risk_level":"low|medium|high","summary":"строка"}.\n'
        "Правила:\n"
        "1) high: массовые удаления/массовые сдвиги/необратимые операции.\n"
        "2) medium: изменение существующих данных без массового эффекта.\n"
        "3) low: безопасные чтения и точечные безвредные действия.\n"
        "4) summary должна коротко описывать, что именно изменится.\n"
        "5) Не выдумывай факты и не меняй смысл операций.\n"
        f"Локаль: {locale}. Таймзона: {timezone}."
        f"{_memory_block(user_memory)}\n"
        f"Текст пользователя: {text}\n"
        f"Операции: {json.dumps(operations, ensure_ascii=False)}"
    )


def build_plan_repair_prompt(
    text: str,
    failed_operation: str,
    reason: str,
    locale: str,
    timezone: str,
    user_memory: dict[str, Any] | None = None,
) -> str:
    return (
        f"{_contract_header()} "
        "Ты PlanRepairAgent для TimeKeeper. "
        "Нужно исправить один неудачный шаг плана. "
        'Верни result формата: {"mode":"retry|skip|clarify","operation":"...|null","question":"...|null"}.\n'
        "Правила:\n"
        "1) retry: если можно безопасно переформулировать шаг без выдумывания фактов.\n"
        "2) skip: если шаг лучше пропустить и продолжить оставшиеся.\n"
        "3) clarify: если без уточнения пользователя безопасно двигаться нельзя.\n"
        "4) operation заполняй только при mode=retry.\n"
        f"Локаль: {locale}. Таймзона: {timezone}."
        f"{_memory_block(user_memory)}\n"
        f"Исходный текст: {text}\n"
        f"Неудачный шаг: {failed_operation}\n"
        f"Причина: {reason}"
    )


def build_execution_supervisor_prompt(
    text: str,
    operations: list[str],
    execution_mode: str,
    locale: str,
    timezone: str,
    user_memory: dict[str, Any] | None = None,
) -> str:
    return (
        f"{_contract_header()} "
        "Ты ExecutionSupervisorAgent для TimeKeeper. "
        "Выбери транзакционную стратегию исполнения пакета операций. "
        'Верни result формата: {"strategy":"all_or_nothing|partial_commit","stop_on_error":true|false}. '
        "Правила:\n"
        "1) all_or_nothing: если операции логически зависят друг от друга.\n"
        "2) partial_commit: если операции независимы и можно выполнить частично.\n"
        "3) stop_on_error=true если последующие шаги теряют смысл после ошибки в раннем шаге.\n"
        f"Локаль: {locale}. Таймзона: {timezone}. execution_mode={execution_mode}."
        f"{_memory_block(user_memory)}\n"
        f"Исходный текст: {text}\n"
        f"Операции: {json.dumps(operations, ensure_ascii=False)}"
    )


def build_response_policy_prompt(
    kind: str,
    source_text: str,
    reason: str,
    locale: str,
    timezone: str,
    user_memory: dict[str, Any] | None = None,
) -> str:
    return (
        f"{_contract_header()} "
        "Ты ResponsePolicyAgent для TimeKeeper. "
        "Сформулируй короткий безопасный ответ бота на русском языке. "
        'Верни result формата: {"text":"строка"}.\n'
        "Правила:\n"
        "1) Без выдумывания фактов.\n"
        "2) Для clarification - один точный вопрос.\n"
        "3) Для error - кратко, понятно, с предложением следующего шага.\n"
        f"kind={kind}. Локаль: {locale}. Таймзона: {timezone}."
        f"{_memory_block(user_memory)}\n"
        f"source_text: {source_text}\n"
        f"reason: {reason}"
    )


def build_context_compressor_prompt(
    context: dict[str, Any],
    locale: str,
    timezone: str,
    user_memory: dict[str, Any] | None = None,
) -> str:
    return (
        f"{_contract_header()} "
        "Ты ContextCompressorAgent для TimeKeeper. "
        "Сожми контекст диалога в компактный структурный вид для других агентов. "
        'Верни result формата: {"summary":"строка","facts":["..."]}. '
        "Правила:\n"
        "1) Сохрани только факты, влияющие на выполнение команд.\n"
        "2) Не добавляй домыслы.\n"
        "3) Facts должны быть короткими и проверяемыми.\n"
        f"Локаль: {locale}. Таймзона: {timezone}."
        f"{_memory_block(user_memory)}\n"
        f"Контекст: {json.dumps(context, ensure_ascii=False)}"
    )


def build_telegram_format_prompt(
    *,
    text: str,
    response_kind: str,
    locale: str,
    timezone: str,
    user_memory: dict[str, Any] | None = None,
) -> str:
    return (
        f"{_contract_header()} "
        "Ты TelegramFormattingAgent для TimeKeeper. "
        "Преобразуй текст в красивый Telegram HTML-формат: чаще делай переносы строк, "
        "добавляй уместные эмодзи и делай структуру легко читаемой. "
        "Используй короткие абзацы, списки и акценты. "
        "(разрешены только теги <b>, <i>, <u>, <a>; теги <code> и <pre> запрещены). "
        "Не меняй факты, даты, суммы, идентификаторы и смысл. "
        "Если kind=button_label, верни короткий plain-text без HTML-тегов и без лишних символов. "
        "Не делай сплошной текст: визуально разделяй смысловые блоки пустой строкой. "
        'Верни result формата: {"text":"строка"}.\n'
        f"kind={response_kind}. Локаль: {locale}. Таймзона: {timezone}."
        f"{_memory_block(user_memory)}\n"
        f"text: {text}"
    )


def build_choice_options_prompt(
    *,
    reply_text: str,
    response_kind: str,
    locale: str,
    timezone: str,
    user_memory: dict[str, Any] | None = None,
) -> str:
    return (
        f"{_contract_header()} "
        "Ты ChoiceOptionsAgent для TimeKeeper. "
        "Сформируй варианты выбора, чтобы пользователь мог нажать кнопку вместо ввода текста. "
        "Возвращай 2 или 3 коротких взаимоисключающих варианта. "
        "Если вариантов меньше двух, верни пустой список. "
        'Верни result формата: {"options":["...","..."]}.\n'
        "Правила:\n"
        "1) Варианты должны быть короткими, понятными и отражать только смысл reply_text.\n"
        "2) Не выдумывай новые факты.\n"
        "3) Примеры: ['Да','Нет'], ['Только на этой неделе','Навсегда в расписании'].\n"
        "4) Для чисто информационных ответов возвращай options=[].\n"
        f"response_kind={response_kind}.\n"
        f"Локаль: {locale}. Таймзона: {timezone}."
        f"{_memory_block(user_memory)}\n"
        f"reply_text: {reply_text}"
    )


def default_clarify_question() -> str:
    return "Уточните, пожалуйста: какую именно операцию нужно выполнить и для какого события?"

from __future__ import annotations

from typing import cast

import pytest

from app.db.models import AgentRunTrace
from app.domain.enums import Intent
from app.repositories.agent_run_trace_repository import AgentRunTraceRepository
from app.services.parser.command_parser_service import CommandParserService


class SequenceLLM:
    def __init__(self, outputs: list[str]) -> None:
        self._outputs = outputs
        self._idx = 0

    async def complete(self, prompt: str) -> str:
        if self._idx >= len(self._outputs):
            msg = "No more prepared LLM outputs"
            raise RuntimeError(msg)
        value = self._outputs[self._idx]
        self._idx += 1
        return value


class MemoryTraceRepository:
    def __init__(self) -> None:
        self.items: list[AgentRunTrace] = []

    async def create(self, trace: AgentRunTrace) -> AgentRunTrace:
        self.items.append(trace)
        return trace


@pytest.mark.asyncio
async def test_parser_accepts_valid_json_from_agents() -> None:
    parser = CommandParserService(
        llm_client=SequenceLLM(
            [
                '{"intent":"create_reminder","needs_clarification":false,"question":null}',
                '{"intent":"create_reminder","title":"Оплата","start_at":"2026-03-01T10:00:00+03:00"}',
            ]
        )
    )

    result = await parser.parse(text="напомни", locale="ru", timezone="Europe/Moscow")

    assert result.intent == Intent.CREATE_REMINDER


@pytest.mark.asyncio
async def test_parser_recovers_json_from_markdown_block() -> None:
    parser = CommandParserService(
        llm_client=SequenceLLM(
            [
                '{"intent":"list_events","needs_clarification":false,"question":null}',
                '```json\n{"intent":"list_events","period":"today"}\n```',
            ]
        )
    )

    result = await parser.parse(text="что сегодня", locale="ru", timezone="UTC")

    assert result.intent == Intent.LIST_EVENTS


@pytest.mark.asyncio
async def test_parser_uses_recovery_agent_on_invalid_command_json() -> None:
    parser = CommandParserService(
        llm_client=SequenceLLM(
            [
                '{"intent":"create_reminder","needs_clarification":false,"question":null}',
                '{',
                '{"intent":"create_reminder","title":"Оплата","start_at":"2026-03-01T10:00:00+03:00"}',
            ]
        )
    )

    result = await parser.parse(
        text="напомни 2026-03-01T10:00:00+03:00 про оплату",
        locale="ru",
        timezone="Europe/Moscow",
    )

    assert result.intent == Intent.CREATE_REMINDER


@pytest.mark.asyncio
async def test_parser_returns_clarify_from_intent_agent() -> None:
    parser = CommandParserService(
        llm_client=SequenceLLM(
            [
                '{"intent":"clarify","needs_clarification":true,"question":"Уточните дату и время."}',
            ]
        )
    )

    result = await parser.parse(text="эээ", locale="ru", timezone="UTC")

    assert result.intent == Intent.CLARIFY


@pytest.mark.asyncio
async def test_parser_returns_clarify_after_failed_recovery() -> None:
    parser = CommandParserService(
        llm_client=SequenceLLM(
            [
                '{"intent":"update_reminder","needs_clarification":false,"question":null}',
                '{',
                '{',
                'Что именно нужно изменить: название, дату или повторы?',
            ]
        )
    )

    result = await parser.parse(text="измени", locale="ru", timezone="UTC")

    assert result.intent == Intent.CLARIFY
    assert len(result.question) > 0


@pytest.mark.asyncio
async def test_parser_maps_unknown_intent_to_clarify() -> None:
    parser = CommandParserService(
        llm_client=SequenceLLM(
            [
                '{"intent":"abracadabra","needs_clarification":false,"question":null}',
            ]
        )
    )

    result = await parser.parse(text="сделай магию", locale="ru", timezone="UTC")

    assert result.intent == Intent.CLARIFY


@pytest.mark.asyncio
async def test_parser_falls_back_to_default_clarify_if_intent_agent_broken() -> None:
    parser = CommandParserService(llm_client=SequenceLLM(["{"]))

    result = await parser.parse(text="что-то", locale="ru", timezone="UTC")

    assert result.intent == Intent.CLARIFY
    assert len(result.question) > 0


@pytest.mark.asyncio
async def test_parser_persists_agent_trace() -> None:
    traces = MemoryTraceRepository()
    parser = CommandParserService(
        llm_client=SequenceLLM(
            [
                '{"intent":"list_events","needs_clarification":false,"question":null}',
                '{"intent":"list_events","period":"today"}',
            ]
        ),
        trace_repository=cast(AgentRunTraceRepository, traces),
    )

    result = await parser.parse(text="что у меня сегодня", locale="ru", timezone="UTC", user_id=42)

    assert result.intent == Intent.LIST_EVENTS
    assert len(traces.items) == 1
    created = traces.items[0]
    assert created.result_intent == Intent.LIST_EVENTS.value
    assert created.user_id == 42


def test_parser_route_mode_is_deterministic_for_same_input() -> None:
    parser = CommandParserService(llm_client=SequenceLLM([]))

    mode_a = parser._select_route_mode(user_id=777, text="напомни завтра в 9")
    mode_b = parser._select_route_mode(user_id=777, text="напомни завтра в 9")

    assert mode_a in {"fast", "precise"}
    assert mode_a == mode_b


@pytest.mark.asyncio
async def test_plan_repair_returns_retry_mode() -> None:
    parser = CommandParserService(
        llm_client=SequenceLLM(
            [
                '{"result":{"mode":"retry","operation":"установи цену Маше 2500","question":null},"confidence":0.9,"needs_clarification":false,"clarify_question":null,"reasons":[]}',
            ]
        )
    )
    mode, operation, question = await parser.repair_operation(
        text="добавь Машу и цену",
        failed_operation="и цену",
        reason="недостаточно данных",
        locale="ru",
        timezone="UTC",
    )
    assert mode == "retry"
    assert operation == "установи цену Маше 2500"
    assert question is None


@pytest.mark.asyncio
async def test_primary_assistant_returns_help_answer_when_confident() -> None:
    parser = CommandParserService(
        llm_client=SequenceLLM(
            [
                '{"result":{"mode":"answer","answer":"Я умею создавать и редактировать напоминания."},"confidence":0.92,"needs_clarification":false,"clarify_question":null,"reasons":[]}',
            ]
        )
    )

    result = await parser.maybe_answer_help(
        text="что ты умеешь?",
        locale="ru",
        timezone="UTC",
    )

    assert result == "Я умею создавать и редактировать напоминания."


@pytest.mark.asyncio
async def test_primary_assistant_returns_none_for_delegate_mode() -> None:
    parser = CommandParserService(
        llm_client=SequenceLLM(
            [
                '{"result":{"mode":"delegate","answer":null},"confidence":0.99,"needs_clarification":false,"clarify_question":null,"reasons":[]}',
            ]
        )
    )

    result = await parser.maybe_answer_help(
        text="напомни завтра в 10 оплатить интернет",
        locale="ru",
        timezone="UTC",
    )

    assert result is None


@pytest.mark.asyncio
async def test_primary_assistant_returns_none_for_low_confidence() -> None:
    parser = CommandParserService(
        llm_client=SequenceLLM(
            [
                '{"result":{"mode":"answer","answer":"Возможно..."},"confidence":0.5,"needs_clarification":false,"clarify_question":null,"reasons":[]}',
            ]
        )
    )

    result = await parser.maybe_answer_help(
        text="как перенести урок?",
        locale="ru",
        timezone="UTC",
    )

    assert result is None


@pytest.mark.asyncio
async def test_primary_assistant_uses_help_knowledge_answer_when_available() -> None:
    parser = CommandParserService(
        llm_client=SequenceLLM(
            [
                '{"result":{"mode":"answer","answer":"Базовый ответ"},"confidence":0.9,"needs_clarification":false,"clarify_question":null,"reasons":[]}',
                '{"result":{"answer":"Покажу расписание на неделю, день, и помогу с переносами и оплатами."},"confidence":0.88,"needs_clarification":false,"clarify_question":null,"reasons":[]}',
            ]
        )
    )

    result = await parser.maybe_answer_help(
        text="что ты умеешь для репетитора?",
        locale="ru",
        timezone="UTC",
    )

    assert result == "Покажу расписание на неделю, день, и помогу с переносами и оплатами."


@pytest.mark.asyncio
async def test_conversation_manager_routes_to_answer() -> None:
    parser = CommandParserService(
        llm_client=SequenceLLM(
            [
                '{"result":{"mode":"answer","operations":[],"answer":"Я помогу с расписанием и оплатами.","question":null},"confidence":0.9,"needs_clarification":false,"clarify_question":null,"reasons":[]}',
            ]
        )
    )

    mode, ops, answer, question, execution_mode, stop_on_error = await parser.route_conversation(
        text="что умеет бот?",
        locale="ru",
        timezone="UTC",
    )

    assert mode == "answer"
    assert ops == []
    assert answer is not None
    assert question is None
    assert execution_mode == "continue_on_error"
    assert stop_on_error is False


@pytest.mark.asyncio
async def test_execution_supervisor_returns_all_or_nothing() -> None:
    parser = CommandParserService(
        llm_client=SequenceLLM(
            [
                '{"result":{"strategy":"all_or_nothing","stop_on_error":true},"confidence":0.9,"needs_clarification":false,"clarify_question":null,"reasons":[]}',
            ]
        )
    )
    strategy, stop_on_error = await parser.supervise_execution(
        text="удали ученика и его уроки",
        operations=["удали ученика", "удали его уроки"],
        execution_mode="stop_on_error",
        locale="ru",
        timezone="UTC",
    )
    assert strategy == "all_or_nothing"
    assert stop_on_error is True


@pytest.mark.asyncio
async def test_response_policy_returns_fallback_on_invalid_json() -> None:
    parser = CommandParserService(llm_client=SequenceLLM(["{"]))
    rendered = await parser.render_policy_text(
        kind="error",
        source_text="x",
        reason="fail",
        locale="ru",
        timezone="UTC",
        fallback="Ошибка",
    )
    assert rendered == "Ошибка"


@pytest.mark.asyncio
async def test_route_conversation_compresses_large_context() -> None:
    parser = CommandParserService(
        llm_client=SequenceLLM(
            [
                '{"result":{"summary":"Краткая сводка","facts":["Есть pending вопрос"]},"confidence":0.9,"needs_clarification":false,"clarify_question":null,"reasons":[]}',
                '{"result":{"mode":"answer","operations":[],"answer":"Готово","question":null},"confidence":0.9,"needs_clarification":false,"clarify_question":null,"reasons":[]}',
            ]
        )
    )
    mode, _ops, answer, _question, _execution_mode, _stop_on_error = await parser.route_conversation(
        text="что дальше?",
        locale="ru",
        timezone="UTC",
        context={
            "dialog_history": [{"role": "user", "content": "x" * 5000}],
            "latest_user_text": "что дальше?",
        },
    )
    assert mode == "answer"
    assert answer == "Готово"


@pytest.mark.asyncio
async def test_suggest_quick_replies_returns_options() -> None:
    parser = CommandParserService(
        llm_client=SequenceLLM(
            [
                '{"result":{"options":["Да","Нет"]},"confidence":0.91,"needs_clarification":false,"clarify_question":null,"reasons":[]}',
            ]
        )
    )

    options = await parser.suggest_quick_replies(
        reply_text="Подтвердите, пожалуйста.",
        locale="ru",
        timezone="UTC",
    )

    assert options == ["Да", "Нет"]


@pytest.mark.asyncio
async def test_suggest_quick_replies_ignores_invalid_option_count() -> None:
    parser = CommandParserService(
        llm_client=SequenceLLM(
            [
                '{"result":{"options":["1","2","3","4"]},"confidence":0.95,"needs_clarification":false,"clarify_question":null,"reasons":[]}',
            ]
        )
    )

    options = await parser.suggest_quick_replies(
        reply_text="Выберите вариант.",
        locale="ru",
        timezone="UTC",
    )

    assert options == []


@pytest.mark.asyncio
async def test_task_chunker_extracts_operations_for_long_text() -> None:
    parser = CommandParserService(
        llm_client=SequenceLLM(
            [
                '{"result":{"operations":["обнови цену Маше до 3000","перенеси Машу на среду 18:00"]},"confidence":0.9,"needs_clarification":false,"clarify_question":null,"reasons":[]}',
            ]
        )
    )

    operations = await parser._extract_task_operations(
        text=(
            "Поставь Маше цену 3000, и еще перенеси ее урок на среду 18:00, "
            "а дальше покажи мне итог по неделе"
        ),
        fallback_operations=["Поставь Маше цену 3000..."],
        locale="ru",
        timezone="UTC",
    )

    assert len(operations) == 2
    assert operations[0].startswith("обнови цену")


@pytest.mark.asyncio
async def test_task_graph_plans_operation_order() -> None:
    parser = CommandParserService(
        llm_client=SequenceLLM(
            [
                '{"result":{"operations":["создай ученика Маша","установи Маше цену 3000","добавь урок Маше на пятницу 18:00"],"execution_mode":"stop_on_error"},"confidence":0.92,"needs_clarification":false,"clarify_question":null,"reasons":[]}',
            ]
        )
    )

    operations, execution_mode = await parser.plan_task_graph(
        text="добавь Машу, цену 3000 и урок на пятницу 18:00",
        operations=["урок", "цена", "ученик"],
        locale="ru",
        timezone="UTC",
    )

    assert execution_mode == "stop_on_error"
    assert operations[0] == "создай ученика Маша"


@pytest.mark.asyncio
async def test_route_conversation_uses_task_chunking_and_graph_for_long_request() -> None:
    parser = CommandParserService(
        llm_client=SequenceLLM(
            [
                '{"result":{"mode":"commands","operations":["черновик"],"answer":null,"question":null},"confidence":0.88,"needs_clarification":false,"clarify_question":null,"reasons":[]}',
                '{"result":{"operations":["создай ученика Маша","установи Маше цену 3000","добавь урок Маше в среду 18:00"]},"confidence":0.9,"needs_clarification":false,"clarify_question":null,"reasons":[]}',
                '{"result":{"operations":["создай ученика Маша","установи Маше цену 3000","добавь урок Маше в среду 18:00"],"execution_mode":"stop_on_error"},"confidence":0.9,"needs_clarification":false,"clarify_question":null,"reasons":[]}',
                '{"result":{"strategy":"all_or_nothing","stop_on_error":true},"confidence":0.9,"needs_clarification":false,"clarify_question":null,"reasons":[]}',
            ]
        )
    )

    mode, ops, _answer, _question, strategy, stop_on_error = await parser.route_conversation(
        text=(
            "Добавь ученика Машу, поставь ей цену 3000 и сразу добавь урок "
            "в среду в 18:00, чтобы все применилось за один раз"
        ),
        locale="ru",
        timezone="UTC",
    )

    assert mode == "commands"
    assert len(ops) == 3
    assert strategy == "all_or_nothing"
    assert stop_on_error is True

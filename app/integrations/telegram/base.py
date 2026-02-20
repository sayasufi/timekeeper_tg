from typing import Protocol


class Notifier(Protocol):
    async def send_message(
        self,
        telegram_id: int,
        text: str,
        buttons: list[tuple[str, str]] | None = None,
    ) -> None:
        ...

    async def close(self) -> None:
        ...

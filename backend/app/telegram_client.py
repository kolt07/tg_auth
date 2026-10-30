import inspect
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from telethon import TelegramClient
from telethon.sessions import StringSession

# Telethon за замовчуванням ставить system_lang_code="en".
# У кейсах LonamiWebs/Telethon#4730 через це Telegram повертає SentCodeTypeApp,
# але повідомлення з кодом у чаті 777000 не з’являється.
# system_lang_code="en-US" + явний профіль пристрою відновлювали доставку коду.
_CLIENT_INIT = {
    "device_model": "Desktop",
    "system_version": "Windows 10",
    "app_version": "4.0.0",
    "lang_code": "en",
    "system_lang_code": "en-US",
}


def make_client(
    api_id: int,
    api_hash: str,
    string_session: str | None = None,
    *,
    receive_updates: bool = False,
) -> TelegramClient:
    session = StringSession(string_session) if string_session else StringSession()
    return TelegramClient(
        session,
        api_id,
        api_hash,
        receive_updates=receive_updates,
        flood_sleep_threshold=0,
        timeout=30,
        connection_retries=2,
        request_retries=1,
        **_CLIENT_INIT,
    )


@asynccontextmanager
async def open_client(
    api_id: int,
    api_hash: str,
    string_session: str | None = None,
) -> AsyncIterator[TelegramClient]:
    client = make_client(api_id, api_hash, string_session)
    await client.connect()
    try:
        yield client
    finally:
        await client.disconnect()


async def user_authorized(client: TelegramClient) -> bool:
    result = client.is_user_authorized()
    if inspect.isawaitable(result):
        result = await result
    return bool(result)

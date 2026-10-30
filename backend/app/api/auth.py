import asyncio
import logging
import time
from typing import NoReturn
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from telethon import TelegramClient

from app.accounts import profile_from_me, upsert_account
from app.api.deps import Db
from app.exceptions import classify_exception
from app.phones import normalize_phone
from app.config import get_settings
from app.schemas import (
    QrPasswordBody,
    QrStartResponse,
    QrStatusResponse,
    ResendCodeBody,
    SendCodeRequest,
    SendCodeResponse,
    SessionPublic,
    SignInRequest,
    SignInResponse,
    normalize_source_bot,
    session_to_public,
)
from app.telegram_client import make_client, user_authorized

logger = logging.getLogger("tgauth.auth")
router = APIRouter(prefix="/auth", tags=["auth"])

_LOGIN_TTL_SECONDS = 600

_LOGIN_MESSAGES = {
    "ApiIdInvalidError": "Неверные api_id или api_hash",
    "PhoneNumberInvalidError": "Некорректный номер",
    "PhoneNumberBannedError": "Номер заблокирован в Telegram",
    "PhoneNumberUnoccupiedError": "На этот номер нет аккаунта Telegram",
    "PhoneCodeInvalidError": "Неверный код",
    "PhoneCodeExpiredError": "Код просрочен. Запросите новый.",
    "PhoneCodeEmptyError": "Введите код",
    "PhoneCodeHashEmptyError": "Код просрочен. Запросите новый.",
    "PasswordHashInvalidError": "Неверный пароль двухэтапной проверки",
    "PhoneNumberFloodError": "Telegram временно ограничил попытки для этого номера",
    "ApiIdPublishedFloodError": "Telegram временно ограничил этот api_id",
    "SendCodeUnavailableError": "Другого способа отправить код сейчас нет. Проверьте чат «Telegram» в приложении.",
    "EOFError": "Не удалось запросить код у Telegram",
}

_CODE_HINTS = {
    "SentCodeTypeApp": "Код в приложении Telegram: откройте чат «Telegram» на этом номере.",
    "SentCodeTypeSms": "Код отправлен в SMS.",
    "SentCodeTypeCall": "Telegram позвонит и продиктует код.",
    "SentCodeTypeFlashCall": "Telegram сделает короткий звонок. Код — в номере, с которого звонили.",
    "SentCodeTypeMissedCall": "Telegram сделает пропущенный звонок. Код — последние цифры номера.",
    "SentCodeTypeFragmentSms": "Код отправлен через Fragment.",
    "SentCodeTypeFirebaseSms": "Код отправлен в SMS.",
    "SentCodeTypeSmsWord": "Код отправлен в SMS.",
    "SentCodeTypeSmsPhrase": "Код отправлен в SMS.",
}

_EMAIL_SETUP = (
    "Telegram просит сначала привязать почту в официальном приложении. После этого запросите код снова."
)


class PendingLogin:
    def __init__(
        self,
        client: TelegramClient,
        api_id: int,
        api_hash: str,
        phone: str,
        phone_code_hash: str,
        source_bot: str | None = None,
    ) -> None:
        self.client = client
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone = phone
        self.phone_code_hash = phone_code_hash
        self.source_bot = source_bot
        self.password_pending = False
        self.created = time.monotonic()
        self.lock = asyncio.Lock()
        self.expiry: asyncio.Task[None] | None = None


_pending: dict[str, PendingLogin] = {}


def delivery_name(sent: object) -> str:
    kind = type(getattr(sent, "type", None)).__name__
    if kind == "SentCodeTypeApp":
        return "app"
    if "Sms" in kind:
        return "sms"
    if kind == "SentCodeTypeEmailCode":
        return "email"
    if kind in {"SentCodeTypeCall", "SentCodeTypeFlashCall", "SentCodeTypeMissedCall"}:
        return "call"
    return "other"


def can_resend_sent(sent: object) -> bool:
    return getattr(sent, "next_type", None) is not None


def resend_wait(sent: object) -> int | None:
    raw = getattr(sent, "timeout", None)
    if isinstance(raw, int) and raw > 0:
        return raw
    return None


def hint_for_delivery(sent: object, resend_in: int | None = None) -> str:
    text = describe_sent_code(sent)
    if delivery_name(sent) == "app" and not can_resend_sent(sent):
        return (
            "Telegram подтвердил код для чата «Telegram» (служебный аккаунт 777000), но SMS недоступно. "
            "Если сообщение не появилось — это типичное поведение стороннего API; войдите по QR-коду."
        )
    if delivery_name(sent) == "app" and (can_resend_sent(sent) or resend_in):
        if resend_in:
            return f"{text} Если в чате «Telegram» кода нет, SMS можно запросить через {resend_in} с."
        return f"{text} Если в чате «Telegram» кода нет, нажмите «Отправить SMS»."
    return text


def describe_sent_code(sent: object) -> str:
    code_type = getattr(sent, "type", None)
    kind = type(code_type).__name__
    if kind == "SentCodeTypeSetUpEmailRequired":
        raise HTTPException(status_code=400, detail=_EMAIL_SETUP)
    if kind == "SentCodeTypeEmailCode":
        pattern = getattr(code_type, "email_pattern", None)
        if isinstance(pattern, str) and pattern.strip():
            return f"Код отправлен на почту {pattern.strip()}."
        return "Код отправлен на почту."
    return _CODE_HINTS.get(kind, "Код отправлен. Проверьте Telegram или SMS.")


def raise_login(exc: Exception) -> NoReturn:
    logger.info("login failed %s", type(exc).__name__)
    decision = classify_exception(exc)
    if decision.kind == "flood":
        seconds = max(int(decision.seconds or 0), 0)
        raise HTTPException(
            status_code=429,
            detail=f"FLOOD_WAIT {seconds} с",
            headers={"Retry-After": str(max(seconds, 1))},
        ) from None
    raise HTTPException(
        status_code=400,
        detail=_LOGIN_MESSAGES.get(type(exc).__name__, "Не удалось выполнить вход"),
    ) from None


async def _safe_disconnect(client: TelegramClient) -> None:
    try:
        await client.disconnect()
    except Exception:
        logger.info("login client close failed")


async def _expire_pending(login_id: str) -> None:
    try:
        await asyncio.sleep(_LOGIN_TTL_SECONDS)
    except asyncio.CancelledError:
        return
    pending = _pending.pop(login_id, None)
    if pending is not None:
        await _safe_disconnect(pending.client)


async def _drop_pending(login_id: str) -> None:
    pending = _pending.pop(login_id, None)
    if pending is None:
        return
    task = pending.expiry
    if task is not None and task is not asyncio.current_task():
        task.cancel()
    await _safe_disconnect(pending.client)


async def _sweep_pending() -> None:
    now = time.monotonic()
    stale = [key for key, item in _pending.items() if now - item.created > _LOGIN_TTL_SECONDS]
    for key in stale:
        await _drop_pending(key)


async def close_pending_logins() -> None:
    for key in list(_pending):
        await _drop_pending(key)
    for key in list(_qr):
        await _drop_qr(key)


_QR_HINT = "На телефоне откройте Telegram → Настройки → Устройства → Подключить устройство и отсканируйте код."


class PendingQr:
    def __init__(self, client: TelegramClient, api_id: int, api_hash: str, qr: object, svg: str) -> None:
        self.client = client
        self.api_id = api_id
        self.api_hash = api_hash
        self.qr = qr
        self.svg = svg
        self.status = "pending"
        self.hint = _QR_HINT
        self.session: SessionPublic | None = None
        self.created = time.monotonic()
        self.stop = False
        self.lock = asyncio.Lock()
        self.task: asyncio.Task[None] | None = None


_qr: dict[str, PendingQr] = {}


def qr_svg(url: str) -> str:
    import segno

    uri = segno.make(url, error="m").svg_data_uri(scale=8, border=2)
    if not isinstance(uri, str) or not uri.startswith("data:image/svg+xml") or "xmlns" not in uri:
        raise HTTPException(status_code=400, detail="Не удалось создать QR-код")
    return uri


async def _drop_qr(login_id: str) -> None:
    pending = _qr.pop(login_id, None)
    if pending is None:
        return
    pending.stop = True
    task = pending.task
    if task is not None and task is not asyncio.current_task() and not task.done():
        task.cancel()
    await _safe_disconnect(pending.client)


async def _sweep_qr() -> None:
    now = time.monotonic()
    stale = [key for key, item in _qr.items() if now - item.created > _LOGIN_TTL_SECONDS]
    for key in stale:
        await _drop_qr(key)


async def _finish_qr(login_id: str) -> None:
    pending = _qr.get(login_id)
    if pending is None:
        return
    client = pending.client
    if not await user_authorized(client):
        pending.status = "error"
        pending.hint = "Сессия не авторизована"
        return
    me = await client.get_me()
    raw_session = client.session.save()
    if not isinstance(raw_session, str) or not raw_session:
        pending.status = "error"
        pending.hint = "Не удалось выполнить вход"
        return
    profile = profile_from_me(me)
    from app.db import get_sessionmaker

    async with get_sessionmaker()() as db:
        account = await upsert_account(
            db,
            api_id=pending.api_id,
            api_hash=pending.api_hash,
            string_session=raw_session,
            label=None,
            phone=profile["phone"],
            username=profile["username"],
            first_name=profile["first_name"],
        )
        await db.commit()
        await db.refresh(account)
        pending.session = session_to_public(account)
    pending.status = "ready"
    pending.hint = "Аккаунт подключен."
    await _safe_disconnect(client)


async def _qr_loop(login_id: str) -> None:
    deadline = time.monotonic() + _LOGIN_TTL_SECONDS
    try:
        while time.monotonic() < deadline:
            pending = _qr.get(login_id)
            if pending is None or pending.stop or pending.status != "pending":
                return
            try:
                await pending.qr.wait()
            except TimeoutError:
                if pending.stop:
                    return
                await pending.qr.recreate()
                pending.svg = qr_svg(pending.qr.url)
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if type(exc).__name__ == "SessionPasswordNeededError":
                    pending.status = "password"
                    pending.hint = "Нужен пароль двухэтапной проверки."
                    return
                logger.info("qr login failed %s", type(exc).__name__)
                pending.status = "error"
                pending.hint = _LOGIN_MESSAGES.get(type(exc).__name__, "Не удалось выполнить вход")
                return
            try:
                await _finish_qr(login_id)
            except Exception as exc:
                logger.info("qr save failed %s", type(exc).__name__)
                current = _qr.get(login_id)
                if current is not None and current.status != "ready":
                    current.status = "error"
                    current.hint = "Не удалось сохранить сессию"
            return
        pending = _qr.get(login_id)
        if pending is not None and pending.status == "pending":
            pending.status = "error"
            pending.hint = "Время QR-кода истекло. Создайте новый."
    except asyncio.CancelledError:
        return


def _qr_status(pending: PendingQr) -> QrStatusResponse:
    status = pending.status
    if status not in {"pending", "password", "ready", "error"}:
        status = "error"
    return QrStatusResponse(
        status=status,
        qr_svg=pending.svg if status == "pending" else None,
        hint=pending.hint,
        session=pending.session,
    )


def _sent_hash(sent: object) -> str:
    value = getattr(sent, "phone_code_hash", None)
    if not isinstance(value, str) or not value:
        raise HTTPException(status_code=400, detail="Не удалось отправить код")
    return value


def _remember_hash(client: TelegramClient, phone: str, phone_code_hash: str) -> None:
    cache = getattr(client, "_phone_code_hash", None)
    if isinstance(cache, dict):
        cache[phone] = phone_code_hash


def _log_sent(action: str, sent: object) -> None:
    code_type = getattr(sent, "type", None)
    nxt = getattr(sent, "next_type", None)
    length = getattr(code_type, "length", None)
    logger.info(
        "login code %s type=%s length=%s next=%s timeout=%s hash=%s",
        action,
        type(code_type).__name__,
        length,
        type(nxt).__name__ if nxt is not None else None,
        getattr(sent, "timeout", None),
        "yes" if getattr(sent, "phone_code_hash", None) else "no",
    )


def _code_response(login_id: UUID, phone: str, sent: object, resend_in: int | None) -> SendCodeResponse:
    wait = resend_in if can_resend_sent(sent) else None
    return SendCodeResponse(
        login_id=login_id,
        phone=phone,
        hint=hint_for_delivery(sent, wait),
        delivery=delivery_name(sent),
        can_resend=can_resend_sent(sent),
        resend_in=wait,
    )


def _client_phone(client: TelegramClient, fallback: str) -> str:
    stored = getattr(client, "_phone", None)
    if isinstance(stored, str) and stored:
        return stored
    return fallback


def _telegram_app() -> tuple[int, str]:
    settings = get_settings()
    return int(settings.telegram_api_id), settings.telegram_api_hash.strip()


async def _request_next_code(client: TelegramClient, phone: str, phone_code_hash: str) -> object:
    from telethon.tl.functions.auth import ResendCodeRequest

    return await client(ResendCodeRequest(_client_phone(client, phone), phone_code_hash))


async def _switch_app_code_to_next(client: TelegramClient, phone: str, sent: object) -> tuple[object, int | None]:
    """Сторонний api_id часто получает SentCodeTypeApp, и сообщение в приложении не появляется."""
    wait = resend_wait(sent)
    if delivery_name(sent) != "app" or not can_resend_sent(sent):
        return sent, wait
    try:
        resent = await _request_next_code(client, phone, _sent_hash(sent))
    except Exception as exc:
        logger.info("login resend deferred %s", type(exc).__name__)
        if type(exc).__name__ == "FloodWaitError":
            seconds = int(getattr(exc, "seconds", 0) or 0)
            return sent, max(seconds, wait or 0) or None
        if type(exc).__name__ == "SendCodeUnavailableError":
            return sent, wait
        raise
    _log_sent("resent", resent)
    return resent, resend_wait(resent)


@router.post("/qr", response_model=QrStartResponse)
async def start_qr() -> QrStartResponse:
    api_id, api_hash = _telegram_app()
    await _sweep_qr()
    for key in list(_qr):
        await _drop_qr(key)
    client = make_client(api_id, api_hash, receive_updates=True)
    try:
        await client.connect()
        qr = await client.qr_login()
        svg = qr_svg(qr.url)
    except HTTPException:
        await _safe_disconnect(client)
        raise
    except Exception as exc:
        await _safe_disconnect(client)
        raise_login(exc)
    login_id = uuid4()
    pending = PendingQr(client, api_id, api_hash, qr, svg)
    _qr[str(login_id)] = pending
    pending.task = asyncio.create_task(_qr_loop(str(login_id)))
    logger.info("qr login started")
    return QrStartResponse(login_id=login_id, qr_svg=svg, hint=_QR_HINT)


@router.get("/qr/{login_id}", response_model=QrStatusResponse)
async def qr_status(login_id: UUID) -> QrStatusResponse:
    await _sweep_qr()
    pending = _qr.get(str(login_id))
    if pending is None:
        raise HTTPException(status_code=400, detail="Время QR-кода истекло. Создайте новый.")
    return _qr_status(pending)


@router.post("/qr/password", response_model=QrStatusResponse)
async def qr_password(payload: QrPasswordBody) -> QrStatusResponse:
    pending = _qr.get(str(payload.login_id))
    if pending is None or pending.status != "password":
        raise HTTPException(status_code=400, detail="Время QR-кода истекло. Создайте новый.")
    async with pending.lock:
        client = pending.client
        try:
            if not client.is_connected():
                await client.connect()
            await client.sign_in(password=payload.password)
            await _finish_qr(str(payload.login_id))
        except HTTPException:
            raise
        except Exception as exc:
            raise_login(exc)
    return _qr_status(pending)


@router.post("/send-code", response_model=SendCodeResponse)
async def send_code(payload: SendCodeRequest) -> SendCodeResponse:
    settings = get_settings()
    api_id, api_hash = _telegram_app()
    phone = normalize_phone(payload.phone, settings.default_phone_region)
    if phone is None:
        raise HTTPException(status_code=422, detail="Некорректный номер")
    await _sweep_pending()
    for key, item in list(_pending.items()):
        if item.phone == phone:
            await _drop_pending(key)
    client = make_client(api_id, api_hash)
    try:
        await client.connect()
        sent = await client.send_code_request(phone)
        _log_sent("sent", sent)
        sent, resend_in = await _switch_app_code_to_next(client, phone, sent)
    except HTTPException:
        await _safe_disconnect(client)
        raise
    except Exception as exc:
        await _safe_disconnect(client)
        raise_login(exc)
    phone_code_hash = _sent_hash(sent)
    _remember_hash(client, phone, phone_code_hash)
    login_id = uuid4()
    pending = PendingLogin(
        client,
        api_id,
        api_hash,
        phone,
        phone_code_hash,
        source_bot=normalize_source_bot(payload.source_bot),
    )
    pending.expiry = asyncio.create_task(_expire_pending(str(login_id)))
    _pending[str(login_id)] = pending
    return _code_response(login_id, phone, sent, resend_in)


@router.post("/resend-code", response_model=SendCodeResponse)
async def resend_code(payload: ResendCodeBody) -> SendCodeResponse:
    login_id = str(payload.login_id)
    await _sweep_pending()
    pending = _pending.get(login_id)
    if pending is None:
        raise HTTPException(status_code=400, detail="Код просрочен. Запросите новый.")
    async with pending.lock:
        client = pending.client
        try:
            if not client.is_connected():
                await client.connect()
            sent = await _request_next_code(client, pending.phone, pending.phone_code_hash)
        except HTTPException:
            raise
        except Exception as exc:
            if type(exc).__name__ == "FloodWaitError":
                seconds = max(int(getattr(exc, "seconds", 0) or 0), 1)
                raise HTTPException(
                    status_code=429,
                    detail=f"SMS можно запросить через {seconds} с",
                    headers={"Retry-After": str(seconds)},
                ) from None
            raise_login(exc)
        phone_code_hash = _sent_hash(sent)
        pending.phone_code_hash = phone_code_hash
        _remember_hash(client, pending.phone, phone_code_hash)
        _log_sent("resent", sent)
        return _code_response(payload.login_id, pending.phone, sent, resend_wait(sent))


@router.post("/sign-in", response_model=SignInResponse)
async def sign_in(payload: SignInRequest, db: Db) -> SignInResponse:
    login_id = str(payload.login_id)
    await _sweep_pending()
    pending = _pending.get(login_id)
    if pending is None:
        raise HTTPException(status_code=400, detail="Код просрочен. Запросите новый.")
    code = payload.code.strip().replace(" ", "")
    async with pending.lock:
        if pending.password_pending and not payload.password:
            return SignInResponse(password_required=True, login_id=payload.login_id)
        if not pending.password_pending and not code:
            raise HTTPException(status_code=422, detail="Введите код")
        client = pending.client
        try:
            if not client.is_connected():
                await client.connect()
            if pending.password_pending:
                await client.sign_in(password=payload.password)
            else:
                try:
                    await client.sign_in(
                        phone=pending.phone,
                        code=code,
                        phone_code_hash=pending.phone_code_hash,
                    )
                except Exception as exc:
                    if type(exc).__name__ != "SessionPasswordNeededError":
                        raise
                    pending.password_pending = True
                    if not payload.password:
                        return SignInResponse(password_required=True, login_id=payload.login_id)
                    await client.sign_in(password=payload.password)
        except HTTPException:
            raise
        except Exception as exc:
            raise_login(exc)
        if not await user_authorized(client):
            raise HTTPException(status_code=400, detail="Сессия не авторизована")
        me = await client.get_me()
        raw_session = client.session.save()
        if not isinstance(raw_session, str) or not raw_session:
            raise HTTPException(status_code=400, detail="Не удалось выполнить вход")
        profile = profile_from_me(me)
        account = await upsert_account(
            db,
            api_id=pending.api_id,
            api_hash=pending.api_hash,
            string_session=raw_session,
            label=None,
            phone=profile["phone"],
            username=profile["username"],
            first_name=profile["first_name"],
            source_bot=pending.source_bot,
        )
        await db.commit()
        await db.refresh(account)
        await _drop_pending(login_id)
        return SignInResponse(password_required=False, session=session_to_public(account))

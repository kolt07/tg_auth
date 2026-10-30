from typing import Literal


DecisionKind = Literal["row", "flood", "fatal"]


class AuthDecision:
    def __init__(self, kind: DecisionKind, seconds: int | None, error_name: str) -> None:
        self.kind = kind
        self.seconds = seconds
        self.error_name = error_name


def classify_exception(exc: BaseException) -> AuthDecision:
    name = type(exc).__name__
    if "FloodWait" in name and getattr(exc, "seconds", None) is not None:
        return AuthDecision("flood", int(getattr(exc, "seconds")), name)
    return AuthDecision("row", None, name)

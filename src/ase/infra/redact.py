def redact(text: str, secret: str) -> str:
    if not secret or not text:
        return text
    return text.replace(secret, "[redacted]")

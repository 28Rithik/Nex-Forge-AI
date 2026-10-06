from app.github_integration.webhook_verify import verify_github_webhook_signature


def test_verify_github_webhook_signature() -> None:
    payload = b'{"hello":"world"}'
    secret = "topsecret"
    import hashlib
    import hmac

    signature = "sha256=" + hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()

    assert verify_github_webhook_signature(payload, signature, secret) is True


def test_verify_github_webhook_signature_rejects_invalid_signature() -> None:
    assert verify_github_webhook_signature(b"{}", "sha256=bad", "topsecret") is False

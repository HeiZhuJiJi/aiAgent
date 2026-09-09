import base64
import os
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from enterprise_agent.contracts.domain import FailureCode
from enterprise_agent.domain.states import DomainError


class SecretCipher:
    def __init__(self, master_key: str) -> None:
        decoded = base64.urlsafe_b64decode(master_key)
        if len(decoded) != 32:
            raise ValueError("master key must be 256 bits")
        self._cipher = AESGCM(decoded)

    def encrypt(self, workspace_id: UUID, secret_id: UUID, value: str) -> bytes:
        nonce = os.urandom(12)
        aad = f"v1:{workspace_id}:{secret_id}".encode()
        return b"\x01" + nonce + self._cipher.encrypt(nonce, value.encode(), aad)

    def decrypt(self, workspace_id: UUID, secret_id: UUID, ciphertext: bytes) -> str:
        if len(ciphertext) < 30 or ciphertext[0] != 1:
            raise DomainError(FailureCode.VALIDATION_FAILED, "unsupported secret envelope")
        try:
            return self._cipher.decrypt(
                ciphertext[1:13], ciphertext[13:], f"v1:{workspace_id}:{secret_id}".encode()
            ).decode()
        except (InvalidTag, UnicodeDecodeError):
            raise DomainError(FailureCode.POLICY_DENIED, "secret authentication failed") from None

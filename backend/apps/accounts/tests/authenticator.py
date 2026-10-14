"""A software WebAuthn authenticator for tests (ES256, "none" attestation).

It builds the same JSON a browser hands to the server (@simplewebauthn/browser's
RegistrationResponseJSON and AuthenticationResponseJSON), signed with a real P-256 key,
so py_webauthn's checks run for real: challenge, origin, RP id hash, flags, signature
and the signature counter.
"""

import base64
import hashlib
import json
import os
from typing import Any

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from apps.accounts import passkeys


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class SoftKey:
    def __init__(self, *, origin: str | None = None, rp_id: str | None = None) -> None:
        self.private = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(16)
        self.counter = 0
        self.origin = origin or passkeys.expected_origin()
        self.rp_id = rp_id or passkeys.rp_id()

    @property
    def id(self) -> str:
        return b64url(self.credential_id)

    def _cose(self) -> bytes:
        numbers = self.private.public_key().public_numbers()
        return cbor2.dumps(
            {
                1: 2,  # kty: EC2
                3: -7,  # alg: ES256
                -1: 1,  # crv: P-256
                -2: numbers.x.to_bytes(32, "big"),
                -3: numbers.y.to_bytes(32, "big"),
            }
        )

    def _client_data(self, kind: str, options: dict[str, Any]) -> bytes:
        return json.dumps(
            {
                "type": kind,
                "challenge": options["challenge"],
                "origin": self.origin,
                "crossOrigin": False,
            }
        ).encode()

    def register(self, options: dict[str, Any]) -> dict[str, Any]:
        client_data = self._client_data("webauthn.create", options)
        auth_data = (
            hashlib.sha256(self.rp_id.encode()).digest()
            + bytes([0x45])  # user present, user verified, attested credential data
            + self.counter.to_bytes(4, "big")
            + bytes(16)  # AAGUID
            + len(self.credential_id).to_bytes(2, "big")
            + self.credential_id
            + self._cose()
        )
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {
            "id": self.id,
            "rawId": self.id,
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "attestationObject": b64url(attestation),
                "transports": ["usb"],
            },
            "clientExtensionResults": {},
        }

    def sign(self, options: dict[str, Any], *, counter: int | None = None) -> dict[str, Any]:
        self.counter = self.counter + 1 if counter is None else counter
        client_data = self._client_data("webauthn.get", options)
        auth_data = (
            hashlib.sha256(self.rp_id.encode()).digest()
            + bytes([0x05])  # user present, user verified
            + self.counter.to_bytes(4, "big")
        )
        signature = self.private.sign(
            auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
        )
        return {
            "id": self.id,
            "rawId": self.id,
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "authenticatorData": b64url(auth_data),
                "signature": b64url(signature),
            },
            "clientExtensionResults": {},
        }

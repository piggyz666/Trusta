"""WebAuthn strong verification (face / fingerprint / security key) with a software authenticator."""
import hashlib
import json
import struct
import unittest

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from backend.security.webauthn import b64url
from tests import test_chat as tc
from tests.test_chat import transfer

ORIGIN = "http://localhost:8000"


def cbor(value):
    def head(major, n):
        if n < 24:
            return bytes([major << 5 | n])
        for info, size in ((24, 1), (25, 2), (26, 4), (27, 8)):
            if n < 1 << (8 * size):
                return bytes([major << 5 | info]) + n.to_bytes(size, "big")
    if isinstance(value, bool):
        return bytes([0xF5 if value else 0xF4])
    if isinstance(value, int):
        return head(0, value) if value >= 0 else head(1, -1 - value)
    if isinstance(value, bytes):
        return head(2, len(value)) + value
    if isinstance(value, str):
        data = value.encode()
        return head(3, len(data)) + data
    if isinstance(value, dict):
        return head(5, len(value)) + b"".join(cbor(k) + cbor(v) for k, v in value.items())
    raise TypeError(value)


class SoftAuthenticator:
    """Behaves like a platform authenticator: user verification via face / fingerprint is simulated by the UV flag."""

    def __init__(self, rp_id="localhost"):
        self.rp_id, self.key, self.count = rp_id, ec.generate_private_key(ec.SECP256R1()), 0
        self.credential_id = hashlib.sha256(b"cred" + rp_id.encode()).digest()[:16]

    def cose(self):
        numbers = self.key.public_key().public_numbers()
        return {1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, "big"), -3: numbers.y.to_bytes(32, "big")}

    def auth_data(self, flags, attested=False):
        data = hashlib.sha256(self.rp_id.encode()).digest() + bytes([flags]) + struct.pack(">I", self.count)
        if attested:
            data += bytes(16) + struct.pack(">H", len(self.credential_id)) + self.credential_id + cbor(self.cose())
        return data

    def create(self, options, origin=ORIGIN):
        client = json.dumps({"type": "webauthn.create", "challenge": options["challenge"], "origin": origin}).encode()
        attestation = cbor({"fmt": "none", "attStmt": {}, "authData": self.auth_data(0x45, attested=True)})
        return {"id": b64url(self.credential_id), "clientDataJSON": b64url(client), "attestationObject": b64url(attestation)}

    def get(self, options, origin=ORIGIN, flags=0x05, bump=True):
        if bump:
            self.count += 1
        client = json.dumps({"type": "webauthn.get", "challenge": options["challenge"], "origin": origin}).encode()
        data = self.auth_data(flags)
        signature = self.key.sign(data + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        return json.dumps({"id": b64url(self.credential_id), "clientDataJSON": b64url(client),
                           "authenticatorData": b64url(data), "signature": b64url(signature)})


class WebAuthnTests(unittest.TestCase):
    profile = "competition"
    setUp = tc.ChatServiceTests.setUp
    say = tc.ChatServiceTests.say
    cards = tc.ChatServiceTests.cards
    balance = tc.ChatServiceTests.balance

    def register(self, device=None):
        device = device or SoftAuthenticator()
        webauthn = self.s.safety.webauthn
        result = webauthn.finish_registration("demo", device.create(webauthn.begin_registration("demo", ORIGIN)) | {"label": "测试手机"})
        self.assertTrue(result["registered"])
        return device

    def red_card(self, amount="2000"):
        card = self.cards(self.say(f"给张三转{amount}", [transfer("张三", amount, amount)]), "confirm")[0]
        self.assertEqual(card["risk_level"], "RED")
        return card

    def approve(self, card, assertion):
        result = self.chat.decide(self.session["session_id"], "demo", card["action_id"], card["action_hash"], True,
                                  {"method": "WEBAUTHN", "code": assertion})
        self.rev = result["revision"]
        return result

    def test_face_or_fingerprint_approves_exactly_one_red_action(self):
        device = self.register()
        card = self.red_card()
        options = self.chat.webauthn_options(self.session["session_id"], "demo", card["action_id"])
        self.assertEqual(options["userVerification"], "required")
        start = self.balance()
        result = self.approve(card, device.get(options))
        self.assertEqual(result["reply"]["cards"][0]["status"], "SUCCEEDED", result["reply"])
        self.assertEqual(self.balance(), start - 200000)
        self.assertTrue(self.s.audit.verify_chain())

    def test_signature_for_another_action_is_refused(self):
        device = self.register()
        first = self.red_card("2000")
        second = self.cards(self.say("给李四转3000", [transfer("李四", "3000", "3000")]), "confirm")[0]
        options_first = self.chat.webauthn_options(self.session["session_id"], "demo", first["action_id"])
        start = self.balance()
        self.approve(second, device.get(options_first))  # signed for the first action, presented for the second
        self.assertEqual(self.balance(), start)

    def test_tap_without_biometric_is_refused(self):
        device = self.register()
        card = self.red_card()
        options = self.chat.webauthn_options(self.session["session_id"], "demo", card["action_id"])
        start = self.balance()
        self.approve(card, device.get(options, flags=0x01))  # user present but not verified
        self.assertEqual(self.balance(), start)

    def test_replayed_counter_and_wrong_origin_are_refused(self):
        device = self.register()
        card = self.red_card()
        options = self.chat.webauthn_options(self.session["session_id"], "demo", card["action_id"])
        start = self.balance()
        self.approve(card, device.get(options, origin="https://evil.example"))
        self.assertEqual(self.balance(), start)  # still pending: the customer can retry
        self.approve(card, device.get(options))  # counter 2: accepted
        self.assertEqual(self.balance(), start - 200000)
        second = self.cards(self.say("给李四转3000", [transfer("李四", "3000", "3000")]), "confirm")[0]
        options = self.chat.webauthn_options(self.session["session_id"], "demo", second["action_id"])
        self.approve(second, device.get(options, bump=False))  # counter did not advance: a cloned key or a replay
        self.assertEqual(self.balance(), start - 200000)

    def test_registration_rules(self):
        webauthn = self.s.safety.webauthn
        for origin in ("http://192.168.1.5:8000", "http://trusta.example"):
            with self.assertRaises(Exception):
                webauthn.begin_registration("demo", origin)  # IP address / plain http other than localhost
        options = webauthn.begin_registration("demo", ORIGIN)
        with self.assertRaises(Exception):
            webauthn.finish_registration("demo", SoftAuthenticator().create(options, origin="http://localhost:9999"))
        self.register()
        self.assertEqual(len(self.s.safety.status("demo")["webauthn_credentials"]), 1)


if __name__ == "__main__":
    unittest.main()

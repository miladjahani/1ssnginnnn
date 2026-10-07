import unittest
import asyncio
import os
import struct
import uuid
from app.protocols.shadowsocks.crypto import (
    CIPHER_CONFIGS, evp_bytes_to_key, derive_subkey,
    AEADCipherState, parse_ss_target_address
)
from app.protocols.shadowsocks.server import ShadowSocksServer
from app.services.repository import repo
from app.database import db

class TestShadowSocks(unittest.TestCase):
    def setUp(self):
        db.init_schema()

    def test_evp_and_hkdf_derivation(self):
        password = "test_password_2026"
        master_key = evp_bytes_to_key(password, 32)
        self.assertEqual(len(master_key), 32)

        salt = os.urandom(32)
        subkey = derive_subkey(master_key, salt, 32)
        self.assertEqual(len(subkey), 32)

    def test_aead_cipher_chacha20(self):
        password = "chacha_secret_test"
        salt = os.urandom(32)
        master_key = evp_bytes_to_key(password, 32)
        subkey = derive_subkey(master_key, salt, 32)

        enc = AEADCipherState("chacha20-ietf-poly1305", subkey)
        dec = AEADCipherState("chacha20-ietf-poly1305", subkey)

        plaintext = b"MILICONFIG ShadowSocks AEAD Real Payload Verification"
        ciphertext = enc.encrypt(plaintext)
        self.assertNotEqual(ciphertext, plaintext)

        decrypted = dec.decrypt(ciphertext)
        self.assertEqual(decrypted, plaintext)

    def test_aead_cipher_aes256gcm(self):
        password = "aes_secret_test"
        salt = os.urandom(32)
        master_key = evp_bytes_to_key(password, 32)
        subkey = derive_subkey(master_key, salt, 32)

        enc = AEADCipherState("aes-256-gcm", subkey)
        dec = AEADCipherState("aes-256-gcm", subkey)

        plaintext = b"Test AES-256-GCM encryption chunk"
        ciphertext = enc.encrypt(plaintext)
        decrypted = dec.decrypt(ciphertext)
        self.assertEqual(decrypted, plaintext)

    def test_parse_target_address(self):
        data = b"\x01\x7f\x00\x00\x01" + struct.pack("!H", 8080) + b"HELLO SERVER"
        ok, host, port, payload, err = parse_ss_target_address(data)
        self.assertTrue(ok, err)
        self.assertEqual(host, "127.0.0.1")
        self.assertEqual(port, 8080)
        self.assertEqual(payload, b"HELLO SERVER")

    def test_shadowsocks_server_integration(self):
        """Integration test: start server, connect real client, send AEAD encrypted payload."""
        async def _run_integration():
            unique_uname = f"ss_user_{uuid.uuid4().hex[:8]}"
            user = repo.create_user(unique_uname, "SS Tester")
            ss_pwd = "ss_integration_pwd_2026"
            test_port = 18388
            repo.create_or_update_ss(user.id, password=ss_pwd, port=test_port)

            server = ShadowSocksServer(host="127.0.0.1", port=test_port)
            await server.start()
            self.assertTrue(server.is_running)

            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", test_port)
                
                cfg = CIPHER_CONFIGS["chacha20-ietf-poly1305"]
                client_salt = os.urandom(cfg["salt_size"])
                client_master_key = evp_bytes_to_key(ss_pwd, cfg["key_size"])
                client_subkey = derive_subkey(client_master_key, client_salt, cfg["key_size"])
                client_cipher = AEADCipherState("chacha20-ietf-poly1305", client_subkey)

                writer.write(client_salt)
                await writer.drain()

                # Encrypt payload
                target_payload = b"\x01\x7f\x00\x00\x01\x00\x09PING"
                len_hdr = struct.pack("!H", len(target_payload))
                writer.write(client_cipher.encrypt(len_hdr))
                writer.write(client_cipher.encrypt(target_payload))
                await writer.drain()

                await asyncio.sleep(0.05)
                writer.close()
                await writer.wait_closed()
            finally:
                await server.stop()

        asyncio.run(_run_integration())


    def test_shadowsocks_aes128gcm_and_chacha20_end_to_end(self):
        """Both AES-128-GCM (16B salt) and ChaCha20-Poly1305 must relay real traffic."""
        async def _run(cipher_name: str, client_port: int, echo_port: int, password: str, method: str):
            user = repo.create_user(f"ss_e2e_{uuid.uuid4().hex[:8]}", "SS E2E")
            repo.create_or_update_ss(user.id, password=password, method=method, port=client_port)

            async def echo(reader, writer):
                try:
                    while True:
                        data = await reader.read(4096)
                        if not data:
                            break
                        writer.write(data)
                        await writer.drain()
                finally:
                    writer.close()

            echo_srv = await asyncio.start_server(echo, "127.0.0.1", echo_port)
            server = ShadowSocksServer(host="127.0.0.1", port=client_port)
            await server.start()
            self.assertTrue(server.is_running)
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", client_port)
                cfg = CIPHER_CONFIGS[cipher_name]
                client_salt = os.urandom(cfg["salt_size"])
                client_subkey = derive_subkey(evp_bytes_to_key(password, cfg["key_size"]), client_salt, cfg["key_size"])
                client_cipher = AEADCipherState(cipher_name, client_subkey)

                writer.write(client_salt)
                await writer.drain()

                payload = b"\x01\x7f\x00\x00\x01" + struct.pack("!H", echo_port) + b"PING-" + cipher_name.encode()
                writer.write(client_cipher.encrypt(struct.pack("!H", len(payload))))
                writer.write(client_cipher.encrypt(payload))
                await writer.drain()

                server_salt = await asyncio.wait_for(reader.readexactly(cfg["salt_size"]), timeout=5)
                self.assertEqual(len(server_salt), cfg["salt_size"])
                server_subkey = derive_subkey(evp_bytes_to_key(password, cfg["key_size"]), server_salt, cfg["key_size"])
                server_cipher = AEADCipherState(cipher_name, server_subkey)

                enc_len = await asyncio.wait_for(reader.readexactly(2 + cfg["tag_size"]), timeout=5)
                chunk_len = struct.unpack("!H", server_cipher.decrypt(enc_len))[0]
                enc_body = await asyncio.wait_for(reader.readexactly(chunk_len + cfg["tag_size"]), timeout=5)
                self.assertEqual(server_cipher.decrypt(enc_body), b"PING-" + cipher_name.encode())

                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
            finally:
                await server.stop()
                echo_srv.close()

        async def _all():
            await _run("chacha20-ietf-poly1305", 18391, 19091, "e2e_chacha_pwd", "chacha20-ietf-poly1305")
            await _run("aes-128-gcm", 18392, 19092, "e2e_aesgcm_pwd", "aes-128-gcm")

        asyncio.run(_all())

if __name__ == "__main__":
    unittest.main()

import asyncio
import os
import struct
import logging
from typing import Dict, Optional, Tuple
from app.config import settings
from app.protocols.shadowsocks.crypto import (
    CIPHER_CONFIGS, evp_bytes_to_key, derive_subkey,
    AEADCipherState, parse_ss_target_address
)
from app.networking.relay import connect_outbound
from app.services.repository import repo
from app.services.access import user_access_error

logger = logging.getLogger("miliconfig.shadowsocks")

DEFAULT_METHOD = "chacha20-ietf-poly1305"
MAX_SALT_SIZE = max(c["salt_size"] for c in CIPHER_CONFIGS.values())
MAX_TAG_SIZE = max(c["tag_size"] for c in CIPHER_CONFIGS.values())
# Enough bytes to validate the first AEAD chunk for any supported cipher without
# consuming socket data (needed because AES-128-GCM uses a 16 byte salt).
PEEK_SIZE = MAX_SALT_SIZE + 2 + MAX_TAG_SIZE


class _PrefixedReader:
    """StreamReader facade that can be replayed with a shared peek buffer."""

    def __init__(self, reader: asyncio.StreamReader, prefix: bytes = b""):
        self._reader = reader
        self._buffer = bytes(prefix)

    async def readexactly(self, n: int) -> bytes:
        while len(self._buffer) < n:
            try:
                chunk = await self._reader.readexactly(n - len(self._buffer))
            except asyncio.IncompleteReadError as e:
                # Keep whatever partial data arrived so error handling loses nothing.
                self._buffer += e.partial
                raise
            if not chunk:
                raise asyncio.IncompleteReadError(partial=self._buffer, expected=n)
            self._buffer += chunk
        out, self._buffer = self._buffer[:n], self._buffer[n:]
        return out


class ShadowSocksServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 8388):
        self.host = host
        self.port = port
        self.server: Optional[asyncio.AbstractServer] = None
        self.is_running = False

    async def start(self):
        """Start the native Python ShadowSocks AEAD TCP server."""
        if self.is_running:
            return
        try:
            self.server = await asyncio.start_server(self.handle_client, self.host, self.port)
            self.is_running = True
            logger.info(f"ShadowSocks server listening on {self.host}:{self.port}")
        except Exception as e:
            logger.error(f"Failed to start ShadowSocks server on {self.host}:{self.port}: {e}")

    async def stop(self):
        """Stop the ShadowSocks server."""
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.is_running = False
            logger.info("ShadowSocks server stopped")

    def _try_credentials(self, peek: bytes, active_creds) -> Tuple[Optional[object], Optional[AEADCipherState], Optional[Dict], int]:
        """
        Identify the credential whose AEAD tag validates the first length chunk.

        Every candidate gets a replayed reader over the shared peek buffer, so a
        failed attempt never consumes socket bytes.
        """
        for cred in active_creds:
            cfg = CIPHER_CONFIGS.get(cred.method, CIPHER_CONFIGS[DEFAULT_METHOD])
            salt_size, tag_size = cfg["salt_size"], cfg["tag_size"]
            if len(peek) < salt_size + 2 + tag_size:
                continue
            salt = peek[:salt_size]
            enc_len_chunk = peek[salt_size:salt_size + 2 + tag_size]
            master_key = evp_bytes_to_key(cred.password, cfg["key_size"])
            subkey = derive_subkey(master_key, salt, cfg["key_size"])
            cipher = AEADCipherState(cred.method, subkey)
            try:
                payload_len = struct.unpack("!H", cipher.decrypt(enc_len_chunk))[0]
            except Exception:
                continue
            return cred, cipher, cfg, payload_len
        return None, None, None, None

    async def _read_initial_chunk(self, client_reader: asyncio.StreamReader, active_creds):
        """
        Buffer just enough bytes to identify the credential and first frame.

        Reading is incremental: as soon as any candidate cipher validates we stop,
        so clients that send a small first write (AES-128-GCM uses a 16 byte salt)
        are not kept waiting for the largest possible header.
        """
        loop = asyncio.get_event_loop()
        deadline = loop.time() + 5.0
        peek = b""
        while True:
            cred, cipher, cfg, payload_len = self._try_credentials(peek, active_creds)
            if cred is not None:
                return peek, cred, cipher, cfg, payload_len
            if len(peek) >= PEEK_SIZE:
                break
            timeout = max(0.1, deadline - loop.time())
            try:
                chunk = await asyncio.wait_for(client_reader.read(PEEK_SIZE - len(peek)), timeout=timeout)
            except asyncio.TimeoutError:
                break
            if not chunk:
                break
            peek += chunk
        return peek, None, None, None, 0

    async def handle_client(self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter):
        """Handle incoming AEAD ShadowSocks TCP connection."""
        active_creds = repo.list_all_active_ss()
        if not active_creds:
            client_writer.close()
            return

        peek, matched_cred, decrypt_state, cfg, payload_len = await self._read_initial_chunk(client_reader, active_creds)
        if not matched_cred or decrypt_state is None or cfg is None:
            logger.warning("ShadowSocks authentication failed: no matching credentials")
            client_writer.close()
            return

        authenticated_user = repo.get_user_by_id(matched_cred.user_id)
        access_error = user_access_error(authenticated_user)
        if access_error:
            username = authenticated_user.username if authenticated_user else "unknown"
            logger.warning(f"ShadowSocks connection rejected: user {username} ({access_error})")
            client_writer.close()
            return

        salt_size, tag_size = cfg["salt_size"], cfg["tag_size"]
        encrypted_reader = _PrefixedReader(client_reader, peek)
        try:
            await encrypted_reader.readexactly(salt_size)          # client salt (already validated)
            await encrypted_reader.readexactly(2 + tag_size)        # encrypted length chunk (already validated)
            enc_payload = await encrypted_reader.readexactly(payload_len + tag_size)
        except Exception as e:
            logger.warning(f"ShadowSocks truncated request: {e}")
            client_writer.close()
            return

        try:
            dec_payload = decrypt_state.decrypt(enc_payload)
        except Exception as e:
            logger.warning(f"ShadowSocks payload decryption failed: {e}")
            client_writer.close()
            return

        # Parse target address and initial data
        ok, target_addr, target_port, initial_data, err = parse_ss_target_address(dec_payload)
        if not ok:
            logger.warning(f"Failed to parse ShadowSocks target address: {err}")
            client_writer.close()
            return

        logger.info(f"ShadowSocks user {authenticated_user.username} connecting to {target_addr}:{target_port}")

        # Connect to outbound destination
        try:
            remote_reader, remote_writer = await connect_outbound(target_addr, target_port)
        except Exception as e:
            logger.error(f"Failed to connect outbound {target_addr}:{target_port}: {e}")
            client_writer.close()
            return

        # Write initial data to remote if any
        if initial_data:
            remote_writer.write(initial_data)
            await remote_writer.drain()

        # Initialize server-to-client encryptor (salt size follows the *matched* cipher)
        server_salt = os.urandom(salt_size)
        server_master_key = evp_bytes_to_key(matched_cred.password, cfg["key_size"])
        server_subkey = derive_subkey(server_master_key, server_salt, cfg["key_size"])
        encrypt_state = AEADCipherState(matched_cred.method, server_subkey)

        # Send server salt
        client_writer.write(server_salt)
        await client_writer.drain()

        # Traffic counters (salt + length chunk + payload chunk, including AEAD tags)
        total_up = len(peek) + len(enc_payload)
        total_down = len(server_salt)

        async def c2r():
            nonlocal total_up
            try:
                while True:
                    len_bytes_enc = await encrypted_reader.readexactly(2 + tag_size)
                    try:
                        len_bytes = decrypt_state.decrypt(len_bytes_enc)
                    except Exception:
                        break
                    chunk_len = struct.unpack("!H", len_bytes)[0]
                    chunk_enc = await encrypted_reader.readexactly(chunk_len + tag_size)
                    chunk = decrypt_state.decrypt(chunk_enc)
                    remote_writer.write(chunk)
                    await remote_writer.drain()
                    total_up += len(len_bytes_enc) + len(chunk_enc)
            except Exception:
                pass
            finally:
                try:
                    remote_writer.close()
                except Exception:
                    pass

        async def r2c():
            nonlocal total_down
            try:
                while True:
                    data = await remote_reader.read(16384)
                    if not data:
                        break
                    len_hdr = struct.pack("!H", len(data))
                    enc_hdr = encrypt_state.encrypt(len_hdr)
                    enc_body = encrypt_state.encrypt(data)
                    client_writer.write(enc_hdr + enc_body)
                    await client_writer.drain()
                    total_down += len(enc_hdr) + len(enc_body)
            except Exception:
                pass
            finally:
                try:
                    client_writer.close()
                except Exception:
                    pass

        await asyncio.gather(c2r(), r2c())
        repo.record_user_traffic(authenticated_user.id, total_up, total_down)


ss_server = ShadowSocksServer(host=settings.SS_BIND_HOST, port=settings.SS_PORT)

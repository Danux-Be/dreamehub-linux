"""Éditeur d'annonces : archive, conversion audio et serveur de transfert."""
import gzip
import io
import math
import struct
import tarfile
import unittest
import urllib.error
import urllib.request
import wave

from dreameui import voice
from dreameui.cloud import CloudError


def wav(seconds: float, rate: int = 44100) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(rate)
        frames = int(seconds * rate)
        w.writeframes(b"".join(struct.pack("<hh", s, s) for s in
                               (int(12000 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(frames))))
    return out.getvalue()


def evil_pack(name: str) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:") as archive:
        for n, data in (("tts.json", b"{}"), ("1.ogg", b"OggS"), (name, b"x")):
            info = tarfile.TarInfo(n); info.size = len(data); archive.addfile(info, io.BytesIO(data))
    return gzip.compress(raw.getvalue())


class PackTests(unittest.TestCase):
    def test_roundtrip_and_determinism(self):
        files = {"tts.json": b'{"a":1}', "1.ogg": b"OggS-one", "20.ogg": b"OggS-twenty"}
        packed = voice.build_pack(files)
        self.assertEqual(packed, voice.build_pack(dict(reversed(files.items()))))
        self.assertEqual(voice.parse_pack(packed), files)

    def test_rejects_unsafe_entries(self):
        for name in ("../escape", "dir/file", ".hidden"):
            with self.assertRaises(CloudError, msg=name):
                voice.parse_pack(evil_pack(name))
        with self.assertRaises(CloudError):
            voice.parse_pack(b"not gzip")


@unittest.skipUnless(voice.ffmpeg_available(), "ffmpeg avec libvorbis requis")
class ConversionTests(unittest.TestCase):
    def test_converts_to_ogg(self):
        converted = voice.convert_audio(wav(1.0))
        self.assertTrue(converted.startswith(b"OggS"))

    def test_rejects_too_short_and_garbage(self):
        with self.assertRaises(CloudError):
            voice.convert_audio(wav(0.05))
        with self.assertRaises(CloudError):
            voice.convert_audio(b"\x00" * 1000)


class TransferTests(unittest.TestCase):
    def test_serves_only_token_path_with_ranges(self):
        payload = bytes(range(256)) * 40
        host = voice.TransferHost(payload, "127.0.0.1")
        url = host.start()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"Range": "bytes=10-19"})) as r:
                self.assertEqual((r.status, r.read()), (206, payload[10:20]))
            self.assertFalse(host.downloaded)
            with urllib.request.urlopen(url) as r:
                self.assertEqual(r.read(), payload)
            self.assertTrue(host.downloaded)
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(url.rsplit("/", 2)[0] + "/other/voice_pack.tar.gz")
        finally:
            host.close()

    def test_rejects_public_robot_address(self):
        with self.assertRaises(CloudError):
            voice.TransferHost(b"x", "8.8.8.8")


if __name__ == "__main__":
    unittest.main()

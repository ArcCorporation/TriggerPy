# enigma3.py
import hashlib
import random
from typing import Dict, List

AllowedChars = (
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    "!@#$%^&*()-_=+[]{}|;:,.<>?/\\~` "
)

class Enigma3Service:
    def __init__(self):
        self._key_tables: Dict[int, List[Dict[str, str]]] = {}
        for i in range(10):
            fixed_key = self._generate_deterministic_key(i)
            plain_to_enc = {p: e for p, e in zip(AllowedChars, fixed_key)}
            enc_to_plain = {e: p for p, e in zip(AllowedChars, fixed_key)}
            self._key_tables[i] = [plain_to_enc, enc_to_plain]

    def encrypt(self, key: int, plaintext: str) -> str:
        key_index = self._get_key_index_from_int(key)
        plain_to_enc = self._key_tables[key_index][0]
        out_chars: List[str] = []
        for ch in plaintext:
            if ch not in plain_to_enc:
                raise ValueError(f"Character not allowed: {repr(ch)}")
            out_chars.append(plain_to_enc[ch])
        return "".join(out_chars)

    def decrypt(self, key: int, ciphertext: str) -> str:
        key_index = self._get_key_index_from_int(key)
        enc_to_plain = self._key_tables[key_index][1]
        out_chars: List[str] = []
        for ch in ciphertext:
            if ch not in enc_to_plain:
                raise ValueError(f"Character not recognized: {repr(ch)}")
            out_chars.append(enc_to_plain[ch])
        return "".join(out_chars)

    def _get_key_index_from_int(self, key: int) -> int:
        return abs(key) % 10

    def _generate_deterministic_key(self, key_index: int) -> str:
        h = hashlib.sha256(str(key_index).encode("utf-8")).digest()
        seed = int.from_bytes(h, byteorder="big", signed=False)
        rng = random.Random(seed)
        chars = list(AllowedChars)
        rng.shuffle(chars)
        return "".join(chars)


if __name__ == "__main__":
    svc = Enigma3Service()

    try:
        key = int(input("Enter integer key: ").strip())
        mode = input("Encrypt (E) or Decrypt (D)? ").strip().upper()

        if mode not in ("E", "D"):
            print("Invalid choice. Must be E or D.")
        else:
            text = input("Enter your text: ")
            if mode == "E":
                result = svc.encrypt(key, text)
                print(f"Encrypted: {result}")
            else:
                result = svc.decrypt(key, text)
                print(f"Decrypted: {result}")
    except Exception as e:
        print("Error:", e)

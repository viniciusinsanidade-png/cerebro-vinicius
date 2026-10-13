# -*- coding: utf-8 -*-
"""Criptografia dos arquivos de dados (fin.enc, estado.enc). A mesma conta é refeita no navegador pelo app:
chave = PBKDF2-SHA256(senha, 'cerebro-fin-v1', 600000) · AES-256-GCM · arquivo = base64(iv[12] + texto cifrado)."""
import base64, hashlib, json, os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SALT = b'cerebro-fin-v1'
ITER = 600000

def chave(senha):
    return hashlib.pbkdf2_hmac('sha256', senha.encode('utf-8'), SALT, ITER, 32)

def cifrar(obj, senha, k=None):
    k = k or chave(senha)
    iv = os.urandom(12)
    ct = AESGCM(k).encrypt(iv, json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode('utf-8'), None)
    return base64.b64encode(iv + ct).decode('ascii')

def decifrar(texto, senha, k=None):
    k = k or chave(senha)
    b = base64.b64decode(texto.strip())
    return json.loads(AESGCM(k).decrypt(b[:12], b[12:], None).decode('utf-8'))

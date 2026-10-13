# -*- coding: utf-8 -*-
"""Cria o fin.enc inicial a partir do FIN que já está embutido no app. Roda uma vez.
Uso: FIN_PASSWORD=... python sync/bootstrap.py caminho/do/cerebro.html"""
import json, os, re, sys
from datetime import datetime, timezone
from fincrypto import cifrar, chave

html = open(sys.argv[1], encoding='utf-8').read()
m = re.search(r'const FIN=(\{.*?\});\n', html, re.S)
FIN = json.loads(m.group(1))
senha = os.environ['FIN_PASSWORD']
dados = {'v': 1, 'gerado': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'ultima_tx': None,
         'aviso': None, 'revisar': 0, 'FIN': FIN}
saida = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'fin.enc')
open(saida, 'w', encoding='ascii').write(cifrar(dados, senha, chave(senha)))
print('Criado', saida, '-', sum(len(v) for v in FIN.values()), 'lançamentos.')

# -*- coding: utf-8 -*-
"""Cliente mínimo do Banco MCP (MCP por HTTP + login OAuth com renovação de token). Sem IA, sem tokens de modelo."""
import json, os, time
import requests

MCP_URL = os.environ.get('MCP_URL', 'https://api.mcp.ai/banco')
TOKEN_URL = os.environ.get('MCP_TOKEN_URL', 'https://api.mcp.ai/oauth/token')

class ErroMCP(Exception):
    pass

class ErroAuth(ErroMCP):
    pass

EXPIRA_EM = 0   # segundos de validade do último access token renovado (0 = desconhecido)

def renovar_token(client_id, refresh_token):
    """Troca o refresh token por um access token novo. Devolve (access_token, refresh_token_atual).
    Tenta de novo em caso de timeout/queda (o servidor pode estar lento). Se a 1ª tentativa deu timeout e a 2ª
    é recusada, é provável que o servidor tenha renovado e a resposta se perdeu: o aviso diz isso."""
    global EXPIRA_EM
    r = None; houve_timeout = False; ultimo = ''
    for tentativa in range(3):
        try:
            r = requests.post(TOKEN_URL, data={'grant_type': 'refresh_token', 'refresh_token': refresh_token,
                                               'client_id': client_id}, timeout=(10, 90))
        except requests.RequestException as e:
            houve_timeout = True; ultimo = e.__class__.__name__; r = None
            time.sleep(3 * (tentativa + 1)); continue
        if r.status_code >= 500:
            houve_timeout = True; ultimo = 'HTTP %s' % r.status_code; time.sleep(3 * (tentativa + 1)); continue
        break
    if r is None:
        raise ErroMCP('Sem conexão com o login do Banco MCP: %s' % ultimo)
    if r.status_code in (400, 401):
        extra = (' (houve timeout antes: o servidor pode ter renovado o token e a resposta se perdeu; '
                 'rode login.py de novo)') if houve_timeout else ''
        raise ErroAuth('Login do Banco MCP recusado (%s): %s%s' % (r.status_code, r.text[:200], extra))
    if r.status_code >= 400:
        raise ErroMCP('Login do Banco MCP respondeu %s: %s' % (r.status_code, r.text[:200]))
    j = r.json()
    try:
        EXPIRA_EM = int(j.get('expires_in') or 0)
    except (TypeError, ValueError):
        EXPIRA_EM = 0
    return j['access_token'], j.get('refresh_token') or refresh_token

class MCP:
    def __init__(self, access_token, url=None):
        self.url = url or MCP_URL
        self.s = requests.Session()
        self.s.headers.update({'Authorization': 'Bearer ' + access_token, 'Content-Type': 'application/json',
                               'Accept': 'application/json, text/event-stream'})
        self._id = 0
        self.sessao = None

    def _post(self, corpo, espera_resposta=True):
        h = {}
        if self.sessao:
            h['Mcp-Session-Id'] = self.sessao
        try:
            r = self.s.post(self.url, data=json.dumps(corpo), headers=h, timeout=120)
        except requests.RequestException as e:
            raise ErroMCP('Sem conexão com o Banco MCP: %s' % e.__class__.__name__)
        if r.status_code == 401:
            raise ErroAuth('Token expirado ou recusado pelo Banco MCP (401).')
        if r.status_code >= 400:
            raise ErroMCP('Banco MCP respondeu %s: %s' % (r.status_code, r.text[:300]))
        if r.headers.get('Mcp-Session-Id'):
            self.sessao = r.headers['Mcp-Session-Id']
        if not espera_resposta or not r.text.strip():
            return None
        ct = r.headers.get('Content-Type', '')
        if 'text/event-stream' in ct:
            ultimo = None
            for ln in r.text.splitlines():
                if ln.startswith('data:'):
                    try:
                        j = json.loads(ln[5:].strip())
                    except ValueError:
                        continue
                    if 'result' in j or 'error' in j:
                        ultimo = j
            return ultimo
        return r.json()

    def rpc(self, metodo, params=None):
        self._id += 1
        j = self._post({'jsonrpc': '2.0', 'id': self._id, 'method': metodo, 'params': params or {}})
        if j is None:
            raise ErroMCP('Resposta vazia do Banco MCP em ' + metodo)
        if 'error' in j:
            raise ErroMCP('Erro do Banco MCP em %s: %s' % (metodo, j['error']))
        return j['result']

    def iniciar(self):
        self.rpc('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                                'clientInfo': {'name': 'cerebro-sync', 'version': '1.0'}})
        self._post({'jsonrpc': '2.0', 'method': 'notifications/initialized'}, espera_resposta=False)

    def ferramenta(self, nome, args=None):
        res = self.rpc('tools/call', {'name': nome, 'arguments': args or {}})
        texto = ''.join(c.get('text', '') for c in res.get('content', []) if c.get('type') == 'text')
        if res.get('isError'):
            raise ErroMCP('Ferramenta %s falhou: %s' % (nome, texto[:300]))
        try:
            return json.loads(texto)
        except ValueError:
            raise ErroMCP('Resposta de %s não veio em JSON: %s' % (nome, texto[:200]))

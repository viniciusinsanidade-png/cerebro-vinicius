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

def renovar_token(client_id, refresh_token):
    """Troca o refresh token por um access token novo. Devolve (access_token, refresh_token_atual)."""
    try:
        r = requests.post(TOKEN_URL, data={'grant_type': 'refresh_token', 'refresh_token': refresh_token,
                                           'client_id': client_id}, timeout=30)
    except requests.RequestException as e:
        raise ErroMCP('Sem conexão com o login do Banco MCP: %s' % e.__class__.__name__)
    if r.status_code in (400, 401):
        raise ErroAuth('Login do Banco MCP recusado (%s): %s' % (r.status_code, r.text[:200]))
    if r.status_code >= 400:
        raise ErroMCP('Login do Banco MCP respondeu %s: %s' % (r.status_code, r.text[:200]))
    j = r.json()
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

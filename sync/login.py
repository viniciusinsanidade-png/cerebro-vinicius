# -*- coding: utf-8 -*-
"""Login único no Banco MCP (rode no SEU computador, uma vez por ano).
Abre o navegador para você autorizar e imprime os dois valores que vão para os Secrets do GitHub:
MCP_CLIENT_ID e MCP_REFRESH_TOKEN.   Uso: python sync/login.py"""
import base64, hashlib, http.server, json, os, secrets, sys, threading, urllib.parse, webbrowser
import requests

BASE = os.environ.get('MCP_BASE', 'https://api.mcp.ai')
RESOURCE = BASE + '/banco'

def main():
    meta = requests.get(BASE + '/.well-known/oauth-authorization-server', timeout=30)
    meta.raise_for_status()
    meta = meta.json()

    porta = 8765
    redirect = 'http://127.0.0.1:%d/callback' % porta
    reg = requests.post(meta['registration_endpoint'], json={
        'client_name': 'Cerebro sync (GitHub Actions)', 'redirect_uris': [redirect],
        'grant_types': ['authorization_code', 'refresh_token'], 'response_types': ['code'],
        'token_endpoint_auth_method': 'none'}, timeout=30)
    reg.raise_for_status()
    client_id = reg.json()['client_id']

    verifier = secrets.token_urlsafe(64)
    desafio = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    estado = secrets.token_urlsafe(16)
    url = meta['authorization_endpoint'] + '?' + urllib.parse.urlencode({
        'response_type': 'code', 'client_id': client_id, 'redirect_uri': redirect, 'state': estado,
        'code_challenge': desafio, 'code_challenge_method': 'S256', 'resource': RESOURCE})

    achado = {}
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if self.path.startswith('/callback'):
                achado['q'] = {k: v[0] for k, v in q.items()}
                self.send_response(200); self.send_header('Content-Type', 'text/html; charset=utf-8'); self.end_headers()
                self.wfile.write('<h2>Pronto! Pode fechar esta aba e voltar ao terminal.</h2>'.encode('utf-8'))
            else:
                self.send_response(404); self.end_headers()
        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(('127.0.0.1', porta), H)
    t = threading.Thread(target=srv.handle_request); t.start()
    print('Abrindo o navegador para você autorizar o Banco MCP...')
    print('Se não abrir sozinho, copie este endereço:\n', url, '\n')
    webbrowser.open(url)
    t.join(300)
    q = achado.get('q')
    if not q or q.get('state') != estado or 'code' not in q:
        print('ERRO: a autorização não voltou (ou foi negada):', q)
        return 1
    tok = requests.post(meta['token_endpoint'], data={
        'grant_type': 'authorization_code', 'code': q['code'], 'redirect_uri': redirect,
        'client_id': client_id, 'code_verifier': verifier, 'resource': RESOURCE}, timeout=30)
    if tok.status_code >= 400:
        print('ERRO ao trocar o código:', tok.status_code, tok.text[:300]); return 1
    j = tok.json()
    if not j.get('refresh_token'):
        print('ERRO: o servidor não devolveu refresh_token. Resposta:', list(j.keys())); return 1
    print('\n=== COPIE ESTES DOIS VALORES PARA OS SECRETS DO GITHUB ===')
    print('MCP_CLIENT_ID     =', client_id)
    print('MCP_REFRESH_TOKEN =', j['refresh_token'])
    print('==========================================================')
    print('Não compartilhe nem salve esses valores em lugar nenhum além dos Secrets.')
    return 0

if __name__ == '__main__':
    sys.exit(main())

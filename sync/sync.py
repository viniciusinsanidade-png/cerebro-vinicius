# -*- coding: utf-8 -*-
"""Robô do Financeiro do Cérebro: busca o extrato, categoriza com regras fixas e grava fin.enc (criptografado).
Roda de hora em hora no GitHub Actions. Não usa IA. Variáveis: FIN_PASSWORD, MCP_CLIENT_ID, MCP_REFRESH_TOKEN."""
import json, os, sys, argparse
from datetime import datetime, timedelta, timezone

from categorize import para_item, aliases_extra, mes_de, MESES
from fincrypto import cifrar, decifrar, chave
from mcp_client import MCP, renovar_token, ErroAuth, ErroMCP

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)                       # fin.enc fica na raiz do repositório (servido pelo site)
FIN_ARQ = os.environ.get('FIN_ARQ', os.path.join(RAIZ, 'fin.enc'))
ESTADO_ARQ = os.environ.get('ESTADO_ARQ', os.path.join(AQUI, 'estado.enc'))
JANELA_DIAS = int(os.environ.get('JANELA_DIAS', '10'))

def agora():
    return datetime.now(timezone.utc)

def ler(arq, senha, k):
    if not os.path.exists(arq):
        return None
    return decifrar(open(arq, encoding='ascii').read(), senha, k)

def gravar(arq, obj, senha, k):
    open(arq, 'w', encoding='ascii').write(cifrar(obj, senha, k))

def achar_antigo(sem_id, tx):
    """Casa uma transação do banco com um lançamento antigo do app que ainda não tem id (mesmo valor, ±1 dia)."""
    d = datetime.strptime(tx['date'][:10], '%Y-%m-%d')
    for it in sem_id:
        mes = MESES.index(it['_mes']) + 1
        try:
            di = datetime(d.year, mes, int(it['d'][:2]))
        except ValueError:
            continue
        if abs(it['v'] - round(tx['amount'], 2)) < 0.005 and abs((di - d).days) <= 1:
            return it
    return None

def mesclar(FIN, txs, extra):
    ids = {i['id'] for itens in FIN.values() for i in itens if 'id' in i}
    sem_id = []
    for mes, itens in FIN.items():
        for i in itens:
            if 'id' not in i:
                i['_mes'] = mes
                sem_id.append(i)
    novos, marcados = [], 0
    recentes = [i for m in reversed(MESES) for i in reversed(FIN.get(m, []))][:200]
    for tx in sorted(txs, key=lambda t: t['date']):
        if tx['id'] in ids:
            continue
        antigo = achar_antigo(sem_id, tx)
        if antigo:
            antigo['id'] = tx['id']; sem_id.remove(antigo); marcados += 1
            continue
        it = para_item(tx, extra, recentes)
        FIN.setdefault(mes_de(tx), []).append(it)
        recentes.insert(0, it); novos.append(it)
    for itens in FIN.values():
        for i in itens:
            i.pop('_mes', None)
    for mes in {mes_de(t) for t in txs}:
        if mes in FIN:
            FIN[mes].sort(key=lambda i: int(i['d'][:2]))
    return novos, marcados

def buscar(mcp, janela_ini, hoje, forcar):
    aviso = None
    if forcar:
        try:
            r = mcp.ferramenta('openfinance_force_sync', {})
            for x in (r.get('results') or []):
                if x.get('needs_action') or str(x.get('status', '')).upper() in ('LOGIN_ERROR', 'WAITING_USER_INPUT', 'OUTDATED'):
                    aviso = 'O Nubank pediu para reconectar o Open Finance. Abra o Banco MCP e renove a autorização.'
        except ErroAuth:
            raise
        except ErroMCP as e:
            print('Aviso: não consegui forçar a sincronização do banco (%s). Sigo com os dados que o banco já tem.' % e)
    contas = mcp.ferramenta('openfinance_list_accounts', {'type': 'BANK'})
    lista = contas.get('results') or []
    if not lista:
        raise ErroMCP('Nenhuma conta bancária encontrada no Banco MCP.')
    txs = []
    for c in lista:
        r = mcp.ferramenta('openfinance_list_transactions', {'account_id': c['account_id'], 'from': janela_ini,
                                                              'to': hoje, 'detail': 'compact'})
        for t in r.get('results') or []:
            if str(t.get('status', 'POSTED')).upper() != 'POSTED':
                continue
            txs.append({'id': t['id'], 'date': t['date'], 'description': t.get('description', ''),
                        'amount': float(t['amount']), 'operationType': t.get('operationType', ''),
                        'receiver': (t.get('paymentData') or {}).get('receiver', '')})
    return txs, aviso

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true', help='só testa o login e a leitura, sem gravar nada')
    a = ap.parse_args()

    senha = os.environ.get('FIN_PASSWORD')
    cid = os.environ.get('MCP_CLIENT_ID')
    rt0 = os.environ.get('MCP_REFRESH_TOKEN')
    if not (senha and cid and rt0):
        print('ERRO: faltam FIN_PASSWORD, MCP_CLIENT_ID ou MCP_REFRESH_TOKEN (cadastre em Settings > Secrets do GitHub).')
        return 2
    k = chave(senha)
    try:
        estado = ler(ESTADO_ARQ, senha, k) or {}
    except Exception:
        estado = {}                            # estado.enc velho (senha trocada): ignora e usa o segredo
    rt = estado.get('refresh_token') or rt0
    try:
        try:
            token, rt_novo = renovar_token(cid, rt)
        except ErroAuth:
            if rt != rt0:                      # o guardado pode estar velho: tenta o segredo original
                token, rt_novo = renovar_token(cid, rt0)
            else:
                raise
        mcp = MCP(token); mcp.iniciar()
        hoje = agora().strftime('%Y-%m-%d')
        try:
            dados = ler(FIN_ARQ, senha, k)
        except Exception:
            print('ERRO: a senha FIN_PASSWORD não abre o fin.enc. Ela precisa ser a mesma senha do Cérebro.')
            return 2
        if dados is None and not a.check:
            print('ERRO: fin.enc não existe. Rode "python sync/bootstrap.py" uma vez (veja o LEIA-ME).')
            return 2
        ultimo = (dados or {}).get('ultima_tx')
        ini = (agora() - timedelta(days=JANELA_DIAS)).strftime('%Y-%m-%d')
        if ultimo:                              # se ficou muito tempo parado, volta a partir da última transação
            ini = min(ini, (datetime.strptime(ultimo, '%Y-%m-%d') - timedelta(days=3)).strftime('%Y-%m-%d'))
            ini = max(ini, (agora() - timedelta(days=90)).strftime('%Y-%m-%d'))
        txs, aviso = buscar(mcp, ini, hoje, os.environ.get('FORCE_SYNC', '1') != '0')
    except ErroAuth as e:
        print('ERRO DE LOGIN:', e)
        print('Rode login.py de novo no seu computador e atualize os segredos (veja o LEIA-ME).')
        return 3
    except ErroMCP as e:
        print('ERRO:', e)
        return 4

    print('Banco devolveu %d transações desde %s.' % (len(txs), ini))
    if a.check:
        print('Teste OK: login e leitura funcionam. Nada foi gravado.')
        if rt_novo != rt:
            print('\nATENÇÃO: o Banco MCP trocou o seu refresh token neste teste. O antigo deixou de valer.')
            print('Atualize o Secret MCP_REFRESH_TOKEN no GitHub com este valor novo:')
            print(rt_novo)
        return 0

    FIN = dados['FIN']
    extra = aliases_extra(FIN)
    novos, marcados = mesclar(FIN, txs, extra)
    mudou = bool(novos) or bool(marcados) or dados.get('aviso') != aviso
    if txs:
        dados['ultima_tx'] = max(t['date'][:10] for t in txs) if not dados.get('ultima_tx') else max(dados['ultima_tx'], max(t['date'][:10] for t in txs))
    dados['aviso'] = aviso
    revisar = [i for itens in FIN.values() for i in itens if 'r' in i]
    dados['revisar'] = len(revisar)
    if mudou:
        dados['gerado'] = agora().strftime('%Y-%m-%dT%H:%M:%SZ')
        gravar(FIN_ARQ, dados, senha, k)
        print('Gravado: %d novos, %d antigos ligados ao banco. %d lançamentos a revisar.' % (len(novos), marcados, len(revisar)))
    else:
        print('Sem novidades. Nada a gravar.')
    if rt_novo != estado.get('refresh_token') and rt_novo != rt0:
        gravar(ESTADO_ARQ, {'refresh_token': rt_novo}, senha, k)
        print('Token renovado e guardado.')
    if aviso:
        print('AVISO:', aviso)
        return 5                                # falha visível no GitHub (e-mail de alerta)
    return 0

if __name__ == '__main__':
    sys.exit(main())

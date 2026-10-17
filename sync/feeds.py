# -*- coding: utf-8 -*-
"""Robô de feeds do Head Vinci: lê o Google Agenda (endereço secreto iCal, somente leitura), monta a Agenda e os Jogos
do Corinthians e grava agenda.enc (criptografado com a senha do app) no ramo "dados". Regras fixas, sem IA.

Segredos (GitHub Actions): CAL_ICS_URL (obrigatório), FIN_PASSWORD (a mesma senha do app),
FOOTBALL_DATA_KEY (opcional: tabela e resultados do Brasileirão).
Saída: <FEEDS_DIR>/agenda.enc (padrão: ./dados/agenda.enc)."""
import datetime as dt
import os
import re
import sys
import unicodedata
from zoneinfo import ZoneInfo

import requests

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
from fincrypto import chave, cifrar, decifrar  # noqa: E402

TZ = ZoneInfo('America/Sao_Paulo')
JANELA_ATRAS = 3        # dias
JANELA_FRENTE = 400     # dias
BATIMENTO_HORAS = 6     # regrava mesmo sem mudança, para o app saber que o robô está vivo


# ---------- categorias (mesmas do app: conta, academico, aniversario, casa, lazer, familia) ----------
def _n(s):
    return unicodedata.normalize('NFD', s.lower()).encode('ascii', 'ignore').decode()


def categoria(titulo):
    t, n = titulo, _n(titulo)
    if re.search(r'escola|emef|\bceu\b|🏫', n) or '🏫' in t:
        return 'familia'
    if re.search(r'vencer|fatura|boleto|mensalidade|aluguel|condominio:', n) or any(e in t for e in '💰💳'):
        return 'conta'
    if re.search(r'aniversario|niver|bolinho|parabens', n) or '🎂' in t:
        return 'aniversario'
    if any(e in t for e in '📚📝📊') or re.search(r'unicid|\bava\b|\ba1\b|avaliacao final|entrega:', n):
        return 'academico'
    if '⚽' in t and 'corinthians' in n:
        return 'lazer'
    if any(e in t for e in '🔧🔒') or re.search(r'vistoria|academia fechada|bombeiros|condominio', n):
        return 'casa'
    if any(e in t for e in '🎬🎮') or re.search(r'festival|cinema|show\b', n):
        return 'lazer'
    return 'familia'


# ---------- jogos do Corinthians ----------
SINONIMOS_CANAL = {'rede globo': 'TV Globo', 'globo': 'TV Globo'}


def _canais(desc):
    m = re.search(r'Onde passa:\s*(.+)', desc or '', re.I)
    if not m:
        return []
    txt = m.group(1).strip().rstrip('.')
    txt = re.sub(r'\s+e\s+(?=[A-ZÀ-Ú])', ', ', txt)             # "A, B e C" -> "A, B, C" (parênteses protegem vírgulas)
    partes, nivel, atual = [], 0, ''
    for ch in txt:
        nivel += (ch == '(') - (ch == ')')
        if ch == ',' and nivel == 0:
            partes.append(atual)
            atual = ''
        else:
            atual += ch
    partes.append(atual)
    saida = []
    for p in (x.strip() for x in partes):
        if not p:
            continue
        base = _n(re.sub(r'\s*\(.*?\)\s*', '', p))
        saida.append(SINONIMOS_CANAL.get(base, p))
    return saida


RE_JOGO = re.compile(r'^\s*⚽\s*(?P<m>.+?)\s+x\s+(?P<v>[^(]+?)\s*(?:\((?P<info>.*)\))?\s*$')


def jogo(titulo, inicio, local, desc):
    """Devolve o jogo no formato do app ou None se o evento não for um jogo do Corinthians."""
    m = RE_JOGO.match(titulo or '')
    if not m or 'corinthians' not in _n(titulo) or not isinstance(inicio, dt.datetime):
        return None
    mand, vis = m.group('m').strip(), m.group('v').strip()
    if 'corinthians' not in _n(mand) and 'corinthians' not in _n(vis):
        return None
    loc = re.sub(r',\s*([A-Z]{2})$', r' (\1)', (local or '').strip())
    return {'data': inicio.strftime('%Y-%m-%d'), 'hora': inicio.strftime('%H:%M'), 'mandante': mand, 'visitante': vis,
            'competicao': (m.group('info') or '').strip(), 'local': loc, 'canais': _canais(desc)}


# ---------- iCal -> eventos ----------
def _data(v):
    """date ou datetime do iCal -> (date, datetime|None) já no fuso de São Paulo."""
    if isinstance(v, dt.datetime):
        v = v.astimezone(TZ) if v.tzinfo else v.replace(tzinfo=TZ)
        return v.date(), v
    return v, None


def eventos_do_ics(texto_ics, hoje=None):
    import icalendar
    import recurring_ical_events
    hoje = hoje or dt.datetime.now(TZ).date()
    cal = icalendar.Calendar.from_ical(texto_ics)
    ini = hoje - dt.timedelta(days=JANELA_ATRAS)
    fim = hoje + dt.timedelta(days=JANELA_FRENTE)
    brutos = recurring_ical_events.of(cal).between(ini, fim)
    agenda, jogos, vistos = [], [], set()
    for ev in brutos:
        if str(ev.get('STATUS', '')).upper() == 'CANCELLED':
            continue
        titulo = re.sub(r'\s+', ' ', str(ev.get('SUMMARY', '')).strip())
        if not titulo:
            continue
        d0, dh = _data(ev.decoded('DTSTART'))
        fim_ev = ev.decoded('DTEND') if ev.get('DTEND') else None
        if dh is None and fim_ev is not None:                    # dia inteiro: DTEND é exclusivo
            ult = fim_ev - dt.timedelta(days=1)
            if isinstance(ult, dt.date) and ult > d0:
                titulo += ' (até %02d/%02d)' % (ult.day, ult.month)
        j = jogo(titulo, dh, str(ev.get('LOCATION', '')), str(ev.get('DESCRIPTION', '')))
        if j:
            jogos.append(j)
        if dh is not None and not re.search(r'\d{1,2}h\d{0,2}|\d{1,2}:\d{2}', titulo):
            titulo += ' · ' + dh.strftime('%H:%M')
        chv = (d0.isoformat(), _n(titulo))
        if chv in vistos:
            continue
        vistos.add(chv)
        agenda.append([d0.isoformat(), titulo, categoria(titulo), 'Google Agenda'])
    agenda.sort(key=lambda x: (x[0], x[1]))
    jogos.sort(key=lambda x: (x['data'], x['hora']))
    return agenda, jogos


# ---------- football-data.org (opcional): tabela completa, forma e resultados por campeonato ----------
# (código na API, nome curto igual ao usado no título dos eventos do calendário, nome completo)
# Para acompanhar outro campeonato, é só acrescentar uma linha aqui. Copa do Brasil e Sul-Americana não existem nessa API.
COMPETICOES = [('BSA', 'Brasileirão', 'Campeonato Brasileiro Série A'), ('CLI', 'Libertadores', 'Copa Libertadores')]
NOME_TIME = 'corinthians'


def _erro_http(e, codigo):
    r = getattr(e, 'response', None)
    st = getattr(r, 'status_code', None)
    if st == 403:
        return '%s não está no plano grátis ou a chave não vale para ela' % codigo
    if st == 404:
        return '%s não encontrada na API' % codigo
    if st == 429:
        return 'limite de chamadas da API (tente de novo mais tarde)'
    return '%s: %s' % (codigo, str(e)[:100])


def _competicao(s, h, codigo, curto, nome):
    base = 'https://api.football-data.org/v4/competitions/%s' % codigo
    r = s.get(base + '/standings', headers=h, timeout=30)
    r.raise_for_status()
    js = r.json()
    blocos = [b for b in js.get('standings', []) if b.get('type') == 'TOTAL' and b.get('table')]
    alvo = next((b for b in blocos if any(NOME_TIME in _n(l['team'].get('name', '')) for l in b['table'])), blocos[0] if blocos else None)
    r = s.get(base + '/matches', headers=h, params={'status': 'FINISHED'}, timeout=30)
    r.raise_for_status()
    jogos = []
    for p in r.json().get('matches', []):
        ft = (p.get('score') or {}).get('fullTime') or {}
        if ft.get('home') is None or ft.get('away') is None:
            continue
        jogos.append((p['utcDate'], p, ft))
    jogos.sort(key=lambda x: x[0])
    forma = {}
    for _, p, ft in jogos:
        a, b = p['homeTeam'].get('id'), p['awayTeam'].get('id')
        ra, rb = ('V', 'D') if ft['home'] > ft['away'] else ('D', 'V') if ft['home'] < ft['away'] else ('E', 'E')
        forma.setdefault(a, []).append(ra)
        forma.setdefault(b, []).append(rb)
    tabela = []
    for l in (alvo['table'] if alvo else []):
        t = l['team']
        tabela.append({'pos': l['position'], 'time': t.get('shortName') or t['name'], 'sigla': t.get('tla') or '', 'escudo': t.get('crest') or '',
                       'pts': l['points'], 'j': l['playedGames'], 'v': l['won'], 'e': l['draw'], 'd': l['lost'],
                       'gp': l.get('goalsFor'), 'gc': l.get('goalsAgainst'), 'sg': l['goalDifference'],
                       'forma': forma.get(t.get('id'), [])[-5:]})
    res = []
    for _, p, ft in jogos:
        a, b = p['homeTeam'], p['awayTeam']
        if NOME_TIME in _n(a.get('name', '') + ' ' + b.get('name', '')):
            res.append({'data': p['utcDate'][:10], 'rodada': p.get('matchday'), 'mandante': a.get('shortName') or a['name'],
                        'visitante': b.get('shortName') or b['name'], 'gm': ft['home'], 'gv': ft['away']})
    temp = (js.get('season') or {}).get('startDate', '')[:4]
    return {'codigo': codigo, 'curto': curto, 'nome': nome, 'temporada': temp, 'grupo': (alvo or {}).get('group') or '',
            'tabela': tabela, 'resultados': res[-6:]}


def futebol(chave_api, sessao=None, antigo=None):
    """Uma entrada por campeonato. Se um falhar, mantém os últimos dados bons dele e anota o erro, sem derrubar os outros."""
    s = sessao or requests
    h = {'X-Auth-Token': chave_api}
    ant = {c.get('codigo'): c for c in ((antigo or {}).get('competicoes') or [])}
    saida = []
    for codigo, curto, nome in COMPETICOES:
        try:
            saida.append(_competicao(s, h, codigo, curto, nome))
        except Exception as e:
            vel = dict(ant.get(codigo) or {'codigo': codigo, 'curto': curto, 'nome': nome, 'tabela': [], 'resultados': []})
            vel['erro'] = _erro_http(e, codigo)
            saida.append(vel)
    if saida and all(c.get('erro') for c in saida) and not any(c.get('tabela') for c in saida):
        raise RuntimeError('; '.join(c['erro'] for c in saida))
    return {'competicoes': saida, 'fonte': 'football-data.org (placares com atraso)'}


# ---------- orquestração ----------
def montar(texto_ics, chave_api=None, antigo=None, agora=None, sessao=None):
    agora = agora or dt.datetime.now(TZ)
    agenda, jogos = eventos_do_ics(texto_ics, agora.date())
    out = {'versao': 1, 'gerado': agora.astimezone(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
           'eventos': agenda, 'jogos': jogos, 'fonte': 'Google Agenda (iCal)'}
    if chave_api:
        try:
            out['futebol'] = futebol(chave_api, sessao, (antigo or {}).get('futebol'))
            out['futebol']['gerado'] = out['gerado']
        except Exception as e:                                   # falha visível, sem apagar o que já havia
            ant = (antigo or {}).get('futebol') or {}
            out['futebol'] = dict(ant, erro='football-data.org: %s' % (str(e)[:160]))
    return out


def mudou(novo, antigo, agora=None):
    """False quando nada relevante mudou e o último batimento é recente."""
    if not antigo:
        return True
    a, b = dict(antigo), dict(novo)
    for x in (a, b):
        x.pop('gerado', None)
        if isinstance(x.get('futebol'), dict):
            x['futebol'] = {k: v for k, v in x['futebol'].items() if k != 'gerado'}
    if a != b:
        return True
    try:
        t = dt.datetime.strptime(antigo['gerado'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=dt.timezone.utc)
    except Exception:
        return True
    agora = agora or dt.datetime.now(dt.timezone.utc)
    return (agora - t) > dt.timedelta(hours=BATIMENTO_HORAS)


def baixar_ics(url, sessao=None):
    r = (sessao or requests).get(url, timeout=60)
    r.raise_for_status()
    txt = r.text
    if 'BEGIN:VCALENDAR' not in txt:
        raise RuntimeError('o endereço não devolveu um calendário (confira o segredo CAL_ICS_URL)')
    return txt


def main():
    senha, url = os.environ.get('FIN_PASSWORD'), (os.environ.get('CAL_ICS_URL') or '').strip()
    if not (senha and url):
        print('ERRO: faltam os segredos FIN_PASSWORD e/ou CAL_ICS_URL no repositório.')
        return 2
    pasta = os.environ.get('FEEDS_DIR', os.path.join(os.path.dirname(AQUI), 'dados'))
    os.makedirs(pasta, exist_ok=True)
    arq = os.path.join(pasta, 'agenda.enc')
    k = chave(senha)
    antigo = None
    if os.path.exists(arq):
        try:
            antigo = decifrar(open(arq, encoding='ascii').read(), senha, k)
        except Exception:
            print('Aviso: agenda.enc antigo não abre com esta senha; será recriado.')
    try:
        ics = baixar_ics(url)
    except Exception as e:
        print('ERRO ao baixar o calendário: %s' % e)
        return 3
    novo = montar(ics, (os.environ.get('FOOTBALL_DATA_KEY') or '').strip() or None, antigo)
    print('Eventos: %d · jogos do Corinthians: %d' % (len(novo['eventos']), len(novo['jogos'])))
    fb = novo.get('futebol')
    if fb:
        for c in fb.get('competicoes') or []:
            print('Futebol %s: %s' % (c['codigo'], ('AVISO ' + c['erro']) if c.get('erro') else '%d times, %d resultados do Corinthians' % (len(c['tabela']), len(c['resultados']))))
        if fb.get('erro'):
            print('Aviso: ' + fb['erro'])
    if not mudou(novo, antigo):
        print('Sem novidades.')
        return 0
    open(arq, 'w', encoding='ascii').write(cifrar(novo, senha, k))
    print('Gravado %s' % arq)
    return 0


if __name__ == '__main__':
    sys.exit(main())

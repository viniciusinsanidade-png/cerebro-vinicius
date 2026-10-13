# -*- coding: utf-8 -*-
"""Categoriza transações do Nubank (formato do Open Finance) no padrão do Cérebro.
Sem IA: regras fixas + tabela de comerciantes. O que não reconhece vira "Outro" e entra na lista de revisão."""
import re, unicodedata
from datetime import date, datetime

CAT = {
    'inv': '💰 Investimento', 'ent': '📥 Entrada', 'cartao': '💳 Cartão de Crédito', 'merc': '🛒 Mercado',
    'lazer': '🎬 Lazer', 'pix': '👤 Pix pessoas', 'outro': '🗂️ Outro', 'carro': '🚗 Carro',
    'online': '🛍️ Compras online', 'rest': '🍔 Restaurante', 'luz': '⚡ Luz', 'aluguel': '🏠 Aluguel',
    'trab': '💼 Trabalho', 'tel': '📱 Telefone', 'edu': '🎓 Educação', 'cond': '🏢 Condomínio',
    'casa': '🏠 Casa', 'net': '📶 Internet', 'saude': '💊 Saúde', 'agua': '💧 Água',
}

def norm(s):
    s = unicodedata.normalize('NFD', s or '')
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    return re.sub(r'\s+', ' ', s).strip().upper()

PART = {'de', 'da', 'do', 'dos', 'das', 'e'}
def titulo(nome):
    nome = re.sub(r'\s+', ' ', (nome or '').strip())
    if not nome or not nome.isupper() or len(nome) <= 3:
        return nome
    out = []
    for i, p in enumerate(nome.lower().split(' ')):
        out.append(p if (p in PART and i > 0) else p[:1].upper() + p[1:])
    return ' '.join(out)

EMPRESA = re.compile(r'\b(LTDA|S\.? ?A\.?|S/A|ME|EPP|EIRELI|MEI|CIA|BANCO|INSTITUICAO|PAGAMENTOS?|SERVICOS?|SISTEMAS?|COMERCIO|CLUB|ASSOCIACAO|SOCIEDADE|COOPERATIVA|LOJA|MERCADO|FARMACIA)\b|\d{5,}')

# (trecho do nome normalizado, categoria, rótulo). Ordem importa: o primeiro que casar vale.
ALIASES = [
    ('SHPP BRASIL', 'online', 'Shopee'),
    ('SHOPEE', 'online', 'Shopee'),
    ('I.C. SERVICE', 'online', 'I.C. Service'),
    ('ELETROPAULO', 'luz', 'ENEL (Eletropaulo)'),
    ('ENEL DISTRIBUICAO', 'luz', 'ENEL'),
    ('ENEL', 'luz', 'ENEL'),
    ('TIM S A', 'tel', 'TIM'),
    ('TIM SA', 'tel', 'TIM'),
    ('GA DE MATOS', 'net', 'GM Telecom (GA de Matos)'),
    ('SECID', 'edu', 'UNICID'),
    ('SOCIEDADE EDUCACIONAL CIDADE', 'edu', 'UNICID'),
    ('GRPQA', 'aluguel', 'Aluguel GRPQA'),
    ('HABITACIONAL COMERCIAL', 'aluguel', 'Boleto habitacional (Miguel Yunes)'),
    ('R2A', 'trab', 'R2A Informática'),
    ('UBER', 'carro', 'Uber'),
    ('CORINTHIANS', 'lazer', 'Sport Club Corinthians (ingresso jogo)'),
    ('BANCO C6', 'outro', 'Pagamento boleto (C6)'),
    ('KEETA', 'rest', 'Keeta Delivery'),
    ('MP *ARENAIDEIA', 'lazer', 'Arena Ideia'),
    ('ARENAIDEIA', 'lazer', 'Arena Ideia'),
    ('SELVA CLUB', 'lazer', 'Selva Club'),
    ('GRMARKET', 'merc', 'GrMarket'),
    ('RDDLITORAL', 'merc', 'RddLitoral'),
    ('ATACADAO', 'merc', 'Atacadao 229 As'),
    ('OXXO', 'merc', 'Oxxo Tabare'),
    ('150FARMA', 'saude', '150Farma'),
    ('CLEANCARLAVA', 'carro', 'CleanCarLava'),
    ('SABESP', 'agua', 'SABESP'),
]

def aliases_extra(fin_hist):
    """Aprende comerciantes do próprio histórico (rótulo -> categoria) para o que não está na lista fixa."""
    fixos = {norm(a[0]) for a in ALIASES}
    ja = set()
    out = []
    ignorar = {CAT['inv'], CAT['ent'], CAT['cartao'], CAT['pix'], CAT['outro']}
    for itens in fin_hist.values():
        for x in itens:
            lab = re.sub(r'\s*\(.*?\)\s*', ' ', x.get('l', '')).strip()
            k = norm(lab)
            if x.get('c') in ignorar or len(k) < 4 or k in ja or k in fixos:
                continue
            ja.add(k)
            cat = next((c for c, v in CAT.items() if v == x['c']), None)
            if cat:
                out.append((k, cat, x['l']))
    return out

def _dia(iso):
    return iso[:10]   # mesma regra do histórico do app: data UTC (DD/MM)

def estorno(tx, hist_recente):
    v = abs(tx['amount'])
    for x in hist_recente:           # do mais novo para o mais antigo
        if abs(abs(x['v']) - v) < 0.005 and x['v'] < 0:
            return x['c'], (re.sub(r'\s*\(estorno\)\s*$', '', x['l']) + ' (estorno)')
    return CAT['outro'], 'Estorno'

def classificar(tx, aliases_hist=(), hist_recente=()):
    """tx: dict com description, amount (float), operationType, receiver (opcional).
    Retorna (categoria com emoji, rótulo, confiante: bool)."""
    desc = tx.get('description') or ''
    amt = tx['amount']
    op = tx.get('operationType') or ''
    pre, _, nome = desc.partition('|')
    pre_n, nome_n = norm(pre), norm(nome)

    if pre_n.startswith('PAGAMENTO DE FATURA'):
        return CAT['cartao'], 'Fatura do cartão', True
    if 'RDB' in pre_n or op == 'RESGATE_APLIC_FINANCEIRA':
        return CAT['inv'], ('Resgate RDB' if amt > 0 else 'Aplicação RDB'), True
    if pre_n.startswith('ESTORNO') and amt > 0:
        c, l = estorno(tx, hist_recente)
        return c, l, c != CAT['outro']
    if pre_n.startswith('SAQUE'):
        return CAT['outro'], 'Saque (retirada em espécie)', True
    if pre_n.startswith('CREDITO EM CONTA'):
        return CAT['ent'], 'Crédito em conta', True
    if amt > 0 and pre_n.startswith('TRANSFERENCIA RECEBIDA'):
        if nome_n == 'VINICIUS ROBERTO SANTOS':
            return CAT['ent'], 'Pix próprio (C6)', True
        if 'INGRID' in nome_n:
            return CAT['ent'], 'Pix Ingrid', True
        return CAT['ent'], 'Pix ' + titulo(nome), True

    alvo = nome_n or norm(tx.get('receiver') or '') or norm(desc)
    for lista in (ALIASES, aliases_hist):
        for chave, cat, rot in lista:
            if norm(chave) in alvo:
                return CAT[cat], rot, True
    rec = norm(tx.get('receiver') or '')
    if rec:
        for lista in (ALIASES, aliases_hist):
            for chave, cat, rot in lista:
                if norm(chave) in rec:
                    return CAT[cat], rot, True

    if pre_n.startswith('TRANSFERENCIA ENVIADA') and amt < 0:
        if EMPRESA.search(nome_n):
            return CAT['outro'], nome.strip() + ' (Pix)', False
        return CAT['pix'], titulo(nome), True
    if pre_n.startswith('COMPRA NO DEBITO'):
        return CAT['outro'], titulo(nome), False
    if pre_n.startswith('PAGAMENTO EFETUADO'):
        return CAT['outro'], 'Boleto ' + titulo(nome), False
    if amt > 0:
        return CAT['ent'], titulo(nome or desc), False
    return CAT['outro'], titulo(nome or desc), False

def para_item(tx, aliases_hist=(), hist_recente=()):
    cat, rot, ok = classificar(tx, aliases_hist, hist_recente)
    item = {'d': _dia(tx['date'])[8:10] + '/' + _dia(tx['date'])[5:7], 'v': round(tx['amount'], 2), 'c': cat, 'l': rot, 'id': tx['id']}
    if not ok:
        item['r'] = tx.get('description', '')     # guarda o texto original para eu revisar depois
    return item

MESES = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez']
def mes_de(tx):
    return MESES[int(_dia(tx['date'])[5:7]) - 1]

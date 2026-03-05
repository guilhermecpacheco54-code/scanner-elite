"""
Scanner Elite Web API
Servidor Flask que replica todas as funcionalidades do bot Telegram
e se comunica com SofaScore para fornecer dados à interface web.
"""
from flask import Flask, jsonify, request, send_from_directory
try:
    from flask_cors import CORS
    _cors_available = True
except ImportError:
    _cors_available = False
try:
    from curl_cffi import requests as curl_requests
    _USE_CURL = True
except ImportError:
    import requests as curl_requests
    _USE_CURL = False

import json, math, time
from datetime import datetime, timedelta
import os, hashlib

app = Flask(__name__, static_folder='static', static_url_path='')
if _cors_available:
    CORS(app)
else:
    @app.after_request
    def add_cors(response):
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        response.headers['Access-Control-Allow-Methods'] = 'GET,POST,OPTIONS'
        return response

TOKEN    = "8510661728:AAFrYlV-iSyJUws-pOft3qmZE-LAtPoHYb4"
CANAL_ID = -100355105792
BASE_TG  = f"https://api.telegram.org/bot{TOKEN}"
BASE_SOFA = "https://api.sofascore.com/api/v1"
HEADERS  = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.sofascore.com/"
}

def sofa_get(url, timeout=10):
    """Requisição ao SofaScore imitando navegador real via curl_cffi."""
    if _USE_CURL:
        return curl_requests.get(url, impersonate="chrome110", timeout=timeout)
    else:
        return curl_requests.get(url, timeout=timeout)

LIGAS_MAIORES = {
    "premier league", "la liga", "bundesliga", "serie a", "ligue 1",
    "champions league", "europa league", "conference league",
    "libertadores", "sul-americana", "brasileirao", "brasileirão",
    "serie b", "série b", "copa do brasil", "mls", "eredivisie",
    "primeira liga", "super lig", "pro league", "scottish premiership",
    "liga mx", "argentina primera", "premier league 2"
}

# ========= CACHE =========
_cache = {}
def cache_get(key):
    v = _cache.get(key)
    if v and time.time() - v['ts'] < v['ttl']:
        return v['data']
    return None

def cache_set(key, data, ttl=20):
    _cache[key] = {'data': data, 'ts': time.time(), 'ttl': ttl}

# ========= CÁLCULOS (replicando o robô) =========

def calcular_pressao(j):
    return (
        (j.get('chutes_c', 0) + j.get('chutes_f', 0)) * 2 +
        (j.get('chutes_gol_c', 0) + j.get('chutes_gol_f', 0)) * 6 +
        (j.get('esc_c', 0) + j.get('esc_f', 0)) * 3
    )

def calcular_btts(j):
    try:
        g_c, g_f = map(int, j.get('placar', '0x0').split('x'))
        if g_c > 0 and g_f > 0:
            return 100
    except:
        pass
    cg_c = j.get('chutes_gol_c', 0)
    cg_f = j.get('chutes_gol_f', 0)
    if cg_c >= 3 and cg_f >= 3:   return 90
    elif cg_c >= 2 and cg_f >= 2: return 80
    elif cg_c >= 3 or cg_f >= 3:  return 65
    elif cg_c >= 2 or cg_f >= 2:  return 50
    return max(25, (cg_c + cg_f) * 12)

def ajuste_tempo(prob, minuto):
    if minuto >= 75:  return min(99, prob * 1.2)
    elif minuto >= 60: return min(99, prob * 1.1)
    return prob

def calcular_prob_gol(j):
    minuto = j.get('minuto', 0)
    ch_c  = j.get('chutes_c', 0) * 1.2 + j.get('chutes_gol_c', 0) * 9 + j.get('esc_c', 0) * 2.5 + j.get('posse_c', 50) * 0.3
    ch_f  = j.get('chutes_f', 0) * 1.2 + j.get('chutes_gol_f', 0) * 9 + j.get('esc_f', 0) * 2.5 + j.get('posse_f', 50) * 0.3
    fator = 0.6 + (minuto / 75)
    prob  = ((ch_c + ch_f) / 2) * fator
    try:
        g_c, g_f = map(int, j.get('placar', '0x0').split('x'))
        if g_c < g_f: prob *= 1.3
    except:
        pass
    return int(max(1, min(95, ajuste_tempo(prob, minuto))))

def _over_odds_jogo(j):
    try:
        gols_c, gols_f = map(int, j.get("placar", "0x0").split("x"))
        total_gols = gols_c + gols_f
    except:
        total_gols = 0
    chutes_gol = j.get("chutes_gol_c", 0) + j.get("chutes_gol_f", 0)
    minuto = j.get("minuto", 1)
    minutos_restantes = max(1, 90 - minuto)
    if chutes_gol == 0:
        prob05 = 100 if total_gols >= 1 else 10
        prob15 = 100 if total_gols >= 2 else (5 if total_gols == 1 else 3)
        prob25 = 100 if total_gols >= 3 else (10 if total_gols == 2 else 5)
        return prob05, prob15, prob25
    ritmo_chutes = chutes_gol / max(1, minuto)
    projecao_chutes = ritmo_chutes * minutos_restantes
    prob05 = 100 if total_gols >= 1 else (90 if projecao_chutes >= 3 else (80 if projecao_chutes >= 2 else (65 if projecao_chutes >= 1 else 40)))
    if total_gols >= 2:   prob15 = 100
    elif total_gols == 1: prob15 = 85 if projecao_chutes >= 2 else (70 if projecao_chutes >= 1 else 45)
    else:                 prob15 = 80 if projecao_chutes >= 4 else (65 if projecao_chutes >= 3 else (45 if projecao_chutes >= 2 else 20))
    if total_gols >= 3:   prob25 = 100
    elif total_gols == 2: prob25 = 90 if projecao_chutes >= 2 else (75 if projecao_chutes >= 1 else 50)
    elif total_gols == 1: prob25 = 80 if projecao_chutes >= 4 else (65 if projecao_chutes >= 3 else (45 if projecao_chutes >= 2 else 25))
    else:                 prob25 = 70 if projecao_chutes >= 7 else (50 if projecao_chutes >= 5 else (30 if projecao_chutes >= 3 else 15))
    return prob05, prob15, prob25

def gerar_estrelas(j):
    score = j.get('prob_gol', 0) * 0.5 + j.get('btts', 0) * 0.3 + j.get('over25', 0) * 0.2
    if score >= 75:   return "⭐⭐⭐⭐⭐"
    elif score >= 65: return "⭐⭐⭐⭐"
    elif score >= 55: return "⭐⭐⭐"
    return "⭐⭐"

def dominancia(j):
    if j.get('chutes_gol_c', 0) > j.get('chutes_gol_f', 0) + 2: return "CASA FORTE"
    elif j.get('chutes_gol_f', 0) > j.get('chutes_gol_c', 0) + 2: return "FORA FORTE"
    return "EQUILIBRADO"

def jogo_elite(j):
    score = j.get('prob_gol', 0) * 0.5 + j.get('pressao', 0) * 0.3 + j.get('btts', 0) * 0.2
    return score >= 70

def detectar_gol_iminente(j):
    cg = j.get('chutes_gol_c', 0) + j.get('chutes_gol_f', 0)
    pressao = calcular_pressao(j)
    ap = j.get('chutes_gol_c', 0) * 2 + j.get('chutes_c', 0)
    if cg >= 8 and pressao >= 80 and ap >= 30 and j.get('minuto', 0) >= 60:
        return True, "🚨 GOL IMINENTE!"
    elif cg >= 5 and pressao >= 60 and ap >= 20:
        return True, "🔥 PRESSÃO FORTE"
    return False, ""

def verificar_funil_escanteios(j):
    minuto = max(1, j.get("minuto", 0))
    esc_total = j.get("esc_c", 0) + j.get("esc_f", 0)
    chutes_total = j.get("chutes_c", 0) + j.get("chutes_f", 0)
    if minuto < 10 or chutes_total <= 10 or esc_total < 2:
        return None
    ritmo_esc = round((esc_total / minuto) * 10, 2)
    tempo_restante = max(0, 90 - minuto)
    capacidade_extra = (ritmo_esc / 10) * tempo_restante
    proj_final = esc_total + capacidade_extra
    over_sugerido = math.floor(proj_final - 0.7) + 0.5
    if over_sugerido <= esc_total or tempo_restante < 5:
        return None
    folga = proj_final - over_sugerido
    conf = max(30, min(95, int(50 + (folga - 1.0) * 25)))
    if tempo_restante <= 15:
        conf = max(20, conf - int((15 - tempo_restante) * 2))
    if conf < 35:
        return None
    return {"entrada": f"Over {over_sugerido} Escanteios", "projecao": round(proj_final, 1),
            "ritmo": ritmo_esc, "conf": conf, "linha": over_sugerido}

def elegivel_ht05(j):
    try: gols = sum(map(int, j.get("placar", "0x0").split("x")))
    except: gols = 0
    pressao = calcular_pressao(j)
    return 10 <= j.get("minuto", 0) <= 40 and gols == 0 and j.get("prob_gol", 0) > 70 and pressao > 45

def e_liga_maior(torneio):
    t = torneio.lower()
    return any(liga in t for liga in LIGAS_MAIORES)

def analisar_oportunidade(j):
    if j.get('minuto', 0) > 95:
        return False, f"Acréscimos - não recomendado"
    prob    = j.get('prob_gol', 0)
    minuto  = j.get('minuto', 0)
    cg      = j.get('chutes_gol_c', 0) + j.get('chutes_gol_f', 0)
    pressao = calcular_pressao(j)
    if minuto >= 85 and prob < 85: return False, f"{minuto}' — {prob}% abaixo de 85%"
    if minuto >= 75 and prob < 80: return False, f"{minuto}' — {prob}% abaixo de 80%"
    if minuto >= 60 and prob < 75: return False, f"{minuto}' — {prob}% abaixo de 75%"
    try:
        gols_c, gols_f = map(int, j.get('placar', '0x0').split('x'))
        if gols_c + gols_f >= 4 and minuto >= 70: return False, "Jogo resolvido"
    except: pass
    if cg < 2 and minuto >= 60: return False, "Pouca pressão ofensiva"
    limite = 85 if minuto >= 85 else (80 if minuto >= 75 else (75 if minuto >= 60 else 70))
    if prob >= limite:
        if pressao >= 100: return True, f"OPORTUNIDADE MÁXIMA! {prob}% | Pressão {pressao}"
        elif pressao >= 70: return True, f"OPORTUNIDADE! {prob}% pressão forte"
        else: return True, f"Entrada: {prob}%"
    return False, f"Abaixo do limite ({limite}%) no minuto {minuto}'"

# ========= BUSCA SOFASCORE =========

def gerar_jogos_demo():
    """Gera jogos de demonstração quando não há jogos ao vivo."""
    import random
    jogos_demo_raw = [
        {'time_c':'Manchester City','time_f':'Arsenal','torneio':'Premier League','pais':'England','placar':'1x0','minuto':62,
         'chutes_c':9,'chutes_f':5,'chutes_gol_c':5,'chutes_gol_f':2,'esc_c':6,'esc_f':3,'posse_c':58,'posse_f':42,'amarelo_c':1,'amarelo_f':2,'vermelho_c':0,'vermelho_f':0},
        {'time_c':'Real Madrid','time_f':'Barcelona','torneio':'La Liga','pais':'Spain','placar':'0x1','minuto':38,
         'chutes_c':7,'chutes_f':8,'chutes_gol_c':3,'chutes_gol_f':4,'esc_c':4,'esc_f':5,'posse_c':46,'posse_f':54,'amarelo_c':2,'amarelo_f':1,'vermelho_c':0,'vermelho_f':0},
        {'time_c':'Bayern Munich','time_f':'Borussia Dortmund','torneio':'Bundesliga','pais':'Germany','placar':'2x1','minuto':75,
         'chutes_c':12,'chutes_f':7,'chutes_gol_c':6,'chutes_gol_f':3,'esc_c':8,'esc_f':4,'posse_c':61,'posse_f':39,'amarelo_c':1,'amarelo_f':3,'vermelho_c':0,'vermelho_f':0},
        {'time_c':'Flamengo','time_f':'Palmeiras','torneio':'Brasileirão','pais':'Brazil','placar':'0x0','minuto':23,
         'chutes_c':4,'chutes_f':3,'chutes_gol_c':2,'chutes_gol_f':1,'esc_c':2,'esc_f':3,'posse_c':52,'posse_f':48,'amarelo_c':0,'amarelo_f':1,'vermelho_c':0,'vermelho_f':0},
        {'time_c':'PSG','time_f':'Olympique Lyon','torneio':'Ligue 1','pais':'France','placar':'1x1','minuto':51,
         'chutes_c':8,'chutes_f':6,'chutes_gol_c':4,'chutes_gol_f':3,'esc_c':5,'esc_f':4,'posse_c':55,'posse_f':45,'amarelo_c':2,'amarelo_f':2,'vermelho_c':0,'vermelho_f':0},
        {'time_c':'Juventus','time_f':'AC Milan','torneio':'Serie A','pais':'Italy','placar':'0x0','minuto':15,
         'chutes_c':3,'chutes_f':2,'chutes_gol_c':1,'chutes_gol_f':1,'esc_c':1,'esc_f':2,'posse_c':50,'posse_f':50,'amarelo_c':0,'amarelo_f':0,'vermelho_c':0,'vermelho_f':0},
    ]
    jogos = []
    for i, raw in enumerate(jogos_demo_raw):
        p = {**raw, 'id': f'demo_{i}', 'vermelho_c': raw.get('vermelho_c',0), 'vermelho_f': raw.get('vermelho_f',0)}
        try:
            g_c, g_f = map(int, p['placar'].split('x'))
            total = g_c + g_f
            p['over05_confirmado'] = total >= 1
            p['over15_confirmado'] = total >= 2
            p['over25_confirmado'] = total >= 3
            p['btts_confirmado']   = g_c > 0 and g_f > 0
        except:
            p['over05_confirmado'] = p['over15_confirmado'] = p['over25_confirmado'] = p['btts_confirmado'] = False
        p['pressao']    = calcular_pressao(p)
        p['prob_gol']   = calcular_prob_gol(p)
        p['chance_gol'] = p['prob_gol']
        p['btts']       = calcular_btts(p)
        p['over05'], p['over15'], p['over25'] = _over_odds_jogo(p)
        p['estrelas']   = gerar_estrelas(p)
        p['dominancia'] = dominancia(p)
        p['elite']      = jogo_elite(p)
        p['liga_maior'] = e_liga_maior(p.get('torneio', ''))
        tem_gi, alerta_gi = detectar_gol_iminente(p)
        p['gol_iminente'] = tem_gi
        p['alerta_gi']    = alerta_gi
        funil = verificar_funil_escanteios(p)
        p['funil_esc'] = funil
        valido, motivo = analisar_oportunidade(p)
        p['oportunidade']  = valido
        p['motivo_op']     = motivo
        p['elegivel_ht05'] = elegivel_ht05(p)
        p['_demo'] = True
        jogos.append(p)
    jogos.sort(key=lambda x: x['prob_gol'], reverse=True)
    return jogos

# Controle global de modo demo
_DEMO_FORCADO = False

def buscar_ao_vivo():
    global _DEMO_FORCADO

    try:
        r = sofa_get(f"{BASE_SOFA}/sport/football/events/live", timeout=10)
        print(f"[DEBUG] SofaScore status: {r.status_code}")

        if r.status_code == 200:
            eventos = r.json().get("events", [])
            print(f"[DEBUG] Eventos recebidos: {len(eventos)}")
            data = processar_eventos_ao_vivo(eventos)
            print(f"[DEBUG] Jogos processados: {len(data)}")

            if data and len(data) > 0:
                _DEMO_FORCADO = False
                cache_set("ao_vivo", data, ttl=50)
                return data
        else:
            print(f"[DEBUG] Resposta inesperada: {r.text[:200]}")

    except Exception as e:
        print(f"Erro buscar_ao_vivo: {e}")

    # 🔴 só entra no demo se NÃO tiver jogo real
    print("⚠️ Usando modo DEMO")
    _DEMO_FORCADO = True
    demo = gerar_jogos_demo()
    cache_set("ao_vivo", demo, ttl=50)
    return demo

def buscar_stats(eid):
    cached = cache_get(f"stats_{eid}")
    if cached: return cached
    s = {'chutes_c': 0, 'chutes_f': 0, 'chutes_gol_c': 0, 'chutes_gol_f': 0,
         'esc_c': 0, 'esc_f': 0, 'amarelo_c': 0, 'amarelo_f': 0,
         'vermelho_c': 0, 'vermelho_f': 0, 'posse_c': 50, 'posse_f': 50}
    try:
        r = sofa_get(f"{BASE_SOFA}/event/{eid}/statistics", timeout=5)
        if r.status_code == 200:
            stats_json = r.json().get('statistics', [])
            if stats_json:
                periodo_alvo = next((p for p in stats_json if p.get('period', '').upper() in ('ALL', 'FULL TIME', '')), stats_json[-1])
                for group in periodo_alvo.get('groups', []):
                    for item in group.get('statisticsItems', []):
                        nome = item.get('name', '').lower()
                        h = str(item.get('home', '0') or '0').replace('%', '').strip()
                        a = str(item.get('away', '0') or '0').replace('%', '').strip()
                        try:
                            if 'shots on target' in nome:   s['chutes_gol_c'], s['chutes_gol_f'] = int(h), int(a)
                            elif 'total shots' in nome:     s['chutes_c'], s['chutes_f'] = int(h), int(a)
                            elif 'corner kicks' in nome:    s['esc_c'], s['esc_f'] = int(h), int(a)
                            elif 'ball possession' in nome: s['posse_c'], s['posse_f'] = int(h), int(a)
                            elif 'yellow cards' in nome:    s['amarelo_c'], s['amarelo_f'] = int(h), int(a)
                            elif 'red cards' in nome:       s['vermelho_c'], s['vermelho_f'] = int(h), int(a)
                        except: pass
    except: pass
    cache_set(f"stats_{eid}", s, ttl=50)
    return s

def calcular_tempo_jogo(jogo):
    status_obj = jogo.get('status', {})
    if status_obj.get('description') == 'HT': return "HT", 45
    api_minuto = status_obj.get('minute', 0)
    ts = jogo.get('time', {}).get('currentPeriodStartTimestamp')
    if ts:
        tm_real = int((time.time() - ts) / 60)
        if status_obj.get('code') == 7: tm_real += 45
        if 0 < tm_real < 120: return f"{tm_real}'", tm_real
    return f"{api_minuto}'", api_minuto

def processar_eventos_ao_vivo(eventos):
    jogos = []
    for jogo in eventos:
        if jogo.get('status', {}).get('type') != 'inprogress':
            continue
        _, minuto = calcular_tempo_jogo(jogo)
        if minuto < 5:
            continue
        eid   = jogo.get('id')
        stats = buscar_stats(eid)
        p = {
            'id':      str(eid),
            'time_c':  jogo.get('homeTeam', {}).get('name', '?'),
            'time_f':  jogo.get('awayTeam', {}).get('name', '?'),
            'placar':  f"{jogo.get('homeScore', {}).get('current', 0)}x{jogo.get('awayScore', {}).get('current', 0)}",
            'minuto':  minuto,
            'torneio':        jogo.get('tournament', {}).get('name', ''),
            'pais':           jogo.get('tournament', {}).get('category', {}).get('name', ''),
            'tournament_id':  jogo.get('tournament', {}).get('uniqueTournament', {}).get('id'),
            **stats
        }
        try:
            g_c, g_f = map(int, p['placar'].split('x'))
            total = g_c + g_f
            p['over05_confirmado'] = total >= 1
            p['over15_confirmado'] = total >= 2
            p['over25_confirmado'] = total >= 3
            p['btts_confirmado']   = g_c > 0 and g_f > 0
        except:
            p['over05_confirmado'] = p['over15_confirmado'] = p['over25_confirmado'] = p['btts_confirmado'] = False
        p['pressao']    = calcular_pressao(p)
        p['prob_gol']   = calcular_prob_gol(p)
        p['chance_gol'] = p['prob_gol']
        p['btts']       = calcular_btts(p)
        p['over05'], p['over15'], p['over25'] = _over_odds_jogo(p)
        p['estrelas']   = gerar_estrelas(p)
        p['dominancia'] = dominancia(p)
        p['elite']      = jogo_elite(p)
        p['liga_maior'] = e_liga_maior(p.get('torneio', ''))
        tem_gi, alerta_gi = detectar_gol_iminente(p)
        p['gol_iminente'] = tem_gi
        p['alerta_gi']    = alerta_gi
        funil = verificar_funil_escanteios(p)
        p['funil_esc'] = funil
        valido, motivo = analisar_oportunidade(p)
        p['oportunidade']  = valido
        p['motivo_op']     = motivo
        p['elegivel_ht05'] = elegivel_ht05(p)
        jogos.append(p)
    jogos.sort(key=lambda x: x['prob_gol'], reverse=True)
    return jogos

def buscar_proximos_jogos():
    cached = cache_get("proximos")
    if cached: return cached
    hoje = datetime.now().strftime('%Y-%m-%d')
    try:
        r = sofa_get(f"{BASE_SOFA}/sport/football/scheduled-events/{hoje}", timeout=10)
        if r.status_code == 200:
            eventos = r.json().get('events', [])
            jogos = []
            for ev in eventos:
                status = ev.get('status', {}).get('type', '')
                if status not in ('notstarted', 'scheduled'):
                    continue
                tc = ev.get('homeTeam', {}).get('name', '')
                tf = ev.get('awayTeam', {}).get('name', '')
                if not tc or not tf: continue
                ts = ev.get('startTimestamp', 0)
                horario = datetime.fromtimestamp(ts).strftime('%H:%M') if ts else '--:--'
                torneio = ev.get('tournament', {}).get('name', '')
                pais    = ev.get('tournament', {}).get('category', {}).get('name', '')
                tc_id   = ev.get('homeTeam', {}).get('id')
                tf_id   = ev.get('awayTeam', {}).get('id')
                forma_c = buscar_forma_time(tc_id)
                forma_f = buscar_forma_time(tf_id)
                prob_gol = round((forma_c['aproveitamento'] + forma_f['aproveitamento']) / 2, 1)
                btts_val = round(((forma_c['gm'] + forma_f['gm']) / 2) * 20, 1)
                btts_val = max(40, min(85, btts_val))
                over25   = round(((forma_c['gm'] + forma_f['gm']) / 2) * 15, 1)
                over25   = max(35, min(80, over25))
                score_pj = int(prob_gol * 0.4 + btts_val * 0.3 + over25 * 0.3)
                nivel_pj = "🔥 ELITE" if score_pj >= 75 else ("⚡ FORTE" if score_pj >= 65 else ("📊 MÉDIO" if score_pj >= 55 else "⚪ FRACO"))
                jogos.append({
                    'id': str(ev.get('id', '')),
                    'time_c': tc, 'time_f': tf,
                    'time_c_id': tc_id, 'time_f_id': tf_id,
                    'horario': horario, 'ts': ts,
                    'data': datetime.now().strftime('%d/%m'),
                    'torneio': torneio, 'pais': pais,
                    'forma_c': forma_c['aproveitamento'],
                    'forma_f': forma_f['aproveitamento'],
                    'gm_c': forma_c['gm'], 'gm_f': forma_f['gm'],
                    'sequencia_c': sequencia_forma(forma_c),
                    'sequencia_f': sequencia_forma(forma_f),
                    'icone_c': get_icone_forma(forma_c['aproveitamento']),
                    'icone_f': get_icone_forma(forma_f['aproveitamento']),
                    'vitorias_c': forma_c['v'], 'empates_c': forma_c['e'], 'derrotas_c': forma_c['d'],
                    'vitorias_f': forma_f['v'], 'empates_f': forma_f['e'], 'derrotas_f': forma_f['d'],
                    'prob_gol': prob_gol, 'btts': btts_val, 'over25': over25,
                    'score': score_pj, 'nivel': nivel_pj,
                    'liga_maior': e_liga_maior(torneio),
                    'tournament_id': ev.get('tournament', {}).get('uniqueTournament', {}).get('id'),
                })
            jogos.sort(key=lambda x: (x['score'], x['ts']), reverse=True)
            if jogos:
                cache_set("proximos", jogos[:60], ttl=300)
                return jogos[:60]
    except Exception as e:
        print(f"Erro proximos: {e}")
    # Sem jogos — retornar dados de demonstração
    return gerar_proximos_demo()

def gerar_proximos_demo():
    """Gera próximos jogos de demonstração."""
    demo_raw = [
        {'time_c':'Liverpool','time_f':'Chelsea','torneio':'Premier League','pais':'England','horario':'14:00','forma_c':78,'forma_f':64,'gm_c':2.1,'gm_f':1.8,'seq_c':'✅✅🤝✅❌','seq_f':'✅❌✅✅🤝','v_c':3,'e_c':1,'d_c':1,'v_f':3,'e_f':1,'d_f':1},
        {'time_c':'Atletico Madrid','time_f':'Sevilla','torneio':'La Liga','pais':'Spain','horario':'15:30','forma_c':85,'forma_f':55,'gm_c':2.4,'gm_f':1.3,'seq_c':'✅✅✅🤝✅','seq_f':'❌✅🤝❌✅','v_c':4,'e_c':1,'d_c':0,'v_f':2,'e_f':1,'d_f':2},
        {'time_c':'Inter Milan','time_f':'Napoli','torneio':'Serie A','pais':'Italy','horario':'16:00','forma_c':72,'forma_f':68,'gm_c':1.9,'gm_f':1.7,'seq_c':'✅✅❌✅🤝','seq_f':'✅🤝✅❌✅','v_c':3,'e_c':1,'d_c':1,'v_f':3,'e_f':1,'d_f':1},
        {'time_c':'Corinthians','time_f':'São Paulo','torneio':'Brasileirão','pais':'Brazil','horario':'17:00','forma_c':50,'forma_f':60,'gm_c':1.2,'gm_f':1.5,'seq_c':'🤝❌✅🤝❌','seq_f':'✅✅🤝❌✅','v_c':1,'e_c':2,'d_c':2,'v_f':3,'e_f':1,'d_f':1},
        {'time_c':'Borussia Dortmund','time_f':'RB Leipzig','torneio':'Bundesliga','pais':'Germany','horario':'18:30','forma_c':70,'forma_f':75,'gm_c':2.0,'gm_f':2.2,'seq_c':'✅❌✅✅🤝','seq_f':'✅✅✅❌✅','v_c':3,'e_c':1,'d_c':1,'v_f':4,'e_f':0,'d_f':1},
        {'time_c':'Olympique Marseille','time_f':'Monaco','torneio':'Ligue 1','pais':'France','horario':'20:00','forma_c':65,'forma_f':70,'gm_c':1.8,'gm_f':1.9,'seq_c':'✅🤝✅❌✅','seq_f':'✅✅❌✅🤝','v_c':3,'e_c':1,'d_c':1,'v_f':3,'e_f':1,'d_f':1},
        {'time_c':'Benfica','time_f':'Sporting CP','torneio':'Primeira Liga','pais':'Portugal','horario':'21:15','forma_c':82,'forma_f':78,'gm_c':2.3,'gm_f':2.0,'seq_c':'✅✅✅✅🤝','seq_f':'✅✅🤝✅✅','v_c':4,'e_c':1,'d_c':0,'v_f':4,'e_f':1,'d_f':0},
        {'time_c':'Boca Juniors','time_f':'River Plate','torneio':'Argentina Primera','pais':'Argentina','horario':'22:00','forma_c':73,'forma_f':80,'gm_c':1.9,'gm_f':2.1,'seq_c':'✅🤝✅✅❌','seq_f':'✅✅✅🤝✅','v_c':3,'e_c':1,'d_c':1,'v_f':4,'e_f':1,'d_f':0},
    ]
    jogos = []
    for i, raw in enumerate(demo_raw):
        fc = raw['forma_c']
        ff = raw['forma_f']
        gm_c = raw['gm_c']
        gm_f = raw['gm_f']
        prob_gol = round((fc + ff) / 2, 1)
        btts_val = round(((gm_c + gm_f) / 2) * 20, 1)
        btts_val = max(40, min(85, btts_val))
        over25 = round(((gm_c + gm_f) / 2) * 15, 1)
        over25 = max(35, min(80, over25))
        score_pj = int(prob_gol * 0.4 + btts_val * 0.3 + over25 * 0.3)
        nivel_pj = "🔥 ELITE" if score_pj >= 75 else ("⚡ FORTE" if score_pj >= 65 else ("📊 MÉDIO" if score_pj >= 55 else "⚪ FRACO"))
        jogos.append({
            'id': f'prox_demo_{i}', 'time_c': raw['time_c'], 'time_f': raw['time_f'],
            'time_c_id': None, 'time_f_id': None,
            'horario': raw['horario'], 'ts': 0,
            'data': datetime.now().strftime('%d/%m'),
            'torneio': raw['torneio'], 'pais': raw['pais'],
            'forma_c': fc, 'forma_f': ff,
            'gm_c': gm_c, 'gm_f': gm_f,
            'sequencia_c': raw['seq_c'], 'sequencia_f': raw['seq_f'],
            'icone_c': get_icone_forma(fc), 'icone_f': get_icone_forma(ff),
            'vitorias_c': raw['v_c'], 'empates_c': raw['e_c'], 'derrotas_c': raw['d_c'],
            'vitorias_f': raw['v_f'], 'empates_f': raw['e_f'], 'derrotas_f': raw['d_f'],
            'prob_gol': prob_gol, 'btts': btts_val, 'over25': over25,
            'score': score_pj, 'nivel': nivel_pj,
            'liga_maior': e_liga_maior(raw['torneio']),
            '_demo': True,
        })
    return jogos

def buscar_forma_time(team_id):
    if not team_id:
        return {'aproveitamento': 50, 'gm': 1.0, 'v': 0, 'e': 0, 'd': 0}
    cached = cache_get(f"forma_{team_id}")
    if cached: return cached
    try:
        r = sofa_get(f"{BASE_SOFA}/team/{team_id}/events/last/0", timeout=8)
        if r.status_code == 200:
            eventos = r.json().get('events', [])
            historico = []
            for ev in eventos:
                g_c = ev.get('homeScore', {}).get('current', 0)
                g_f = ev.get('awayScore', {}).get('current', 0)
                historico.append({
                    'time_c': ev.get('homeTeam', {}).get('name', ''),
                    'time_f': ev.get('awayTeam', {}).get('name', ''),
                    'placar': f"{g_c}x{g_f}"
                })
            forma = calcular_forma(historico)
            cache_set(f"forma_{team_id}", forma, ttl=300)
            return forma
    except: pass
    return {'aproveitamento': 50, 'gm': 1.0, 'v': 0, 'e': 0, 'd': 0}

def calcular_forma(historico):
    if not historico:
        return {'aproveitamento': 50, 'gm': 1.0, 'v': 0, 'e': 0, 'd': 0}
    v, e, d, gm = 0, 0, 0, 0
    jogos = 0
    for j in historico[-5:]:
        try:
            g_c, g_f = map(int, j['placar'].split('x'))
            jogos += 1
            gm += g_c
            if g_c > g_f: v += 1
            elif g_c == g_f: e += 1
            else: d += 1
        except: continue
    if jogos == 0:
        return {'aproveitamento': 50, 'gm': 1.0, 'v': 0, 'e': 0, 'd': 0}
    pontos = (v * 3) + (e * 1)
    aprov  = round((pontos / (jogos * 3)) * 100, 1)
    return {'aproveitamento': aprov, 'gm': round(gm / jogos, 2), 'v': v, 'e': e, 'd': d}

def get_icone_forma(aprov):
    if aprov >= 80: return "🔥"
    elif aprov >= 60: return "⚡"
    elif aprov >= 40: return "🟡"
    elif aprov >= 20: return "🔴"
    return "💀"

def sequencia_forma(forma):
    seq = "✅" * forma['v'] + "🤝" * forma['e'] + "❌" * forma['d']
    return seq[:5] if seq else "—"

def prever_vencedor(jogo):
    fc = jogo.get('forma_c', 50)
    ff = jogo.get('forma_f', 50)
    diff = fc - ff
    if diff >= 20:   p_c, p_e, p_f = 0.60, 0.22, 0.18
    elif diff >= 10: p_c, p_e, p_f = 0.50, 0.25, 0.25
    elif diff >= 5:  p_c, p_e, p_f = 0.43, 0.28, 0.29
    elif diff <= -20: p_c, p_e, p_f = 0.18, 0.22, 0.60
    elif diff <= -10: p_c, p_e, p_f = 0.25, 0.25, 0.50
    elif diff <= -5:  p_c, p_e, p_f = 0.29, 0.28, 0.43
    else:             p_c, p_e, p_f = 0.33, 0.34, 0.33
    total = p_c + p_e + p_f
    return {
        'casa': round(p_c / total * 100, 1),
        'empate': round(p_e / total * 100, 1),
        'fora': round(100 - round(p_c / total * 100, 1) - round(p_e / total * 100, 1), 1)
    }

def buscar_h2h(tc_id, tf_id, time_c, time_f):
    if not tc_id or not tf_id:
        return []
    chave = f"h2h_{tc_id}_{tf_id}"
    cached = cache_get(chave)
    if cached: return cached
    try:
        hist_c = []
        hist_f = []
        r_c = sofa_get(f"{BASE_SOFA}/team/{tc_id}/events/last/0", timeout=8)
        if r_c.status_code == 200:
            for ev in r_c.json().get('events', []):
                g_c = ev.get('homeScore', {}).get('current', 0)
                g_f = ev.get('awayScore', {}).get('current', 0)
                hist_c.append({'time_c': ev.get('homeTeam', {}).get('name', ''),
                                'time_f': ev.get('awayTeam', {}).get('name', ''),
                                'placar': f"{g_c}x{g_f}"})
        tc_low = time_c.lower()
        tf_low = time_f.lower()
        confrontos = []
        vistos = set()
        for j in hist_c:
            nc = j.get('time_c', '').lower()
            nf = j.get('time_f', '').lower()
            chave_j = (j['time_c'], j['time_f'], j['placar'])
            if chave_j in vistos: continue
            if tf_low in nc or tf_low in nf:
                confrontos.append(j)
                vistos.add(chave_j)
        result = confrontos[-5:]
        cache_set(chave, result, ttl=600)
        return result
    except:
        return []

# ========= ROTAS API =========

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/api/ao_vivo')
def api_ao_vivo():
    jogos = buscar_ao_vivo()
    # Estatísticas resumidas
    total   = len(jogos)
    quentes = [j for j in jogos if j.get('prob_gol', 0) >= 70]
    elites  = [j for j in jogos if j.get('elite')]
    ops     = [j for j in jogos if j.get('oportunidade')]
    return jsonify({
        'jogos': jogos,
        'total': total,
        'quentes': len(quentes),
        'elites': len(elites),
        'oportunidades': len(ops),
        'hora': datetime.now().strftime('%H:%M:%S')
    })

@app.route('/api/top5')
def api_top5():
    jogos = buscar_ao_vivo()
    ranked = sorted(jogos,
        key=lambda x: x.get('pressao', 0) * 0.4 + x.get('prob_gol', 0) * 0.4 + x.get('btts', 0) * 0.2,
        reverse=True)[:5]
    return jsonify({'jogos': ranked, 'hora': datetime.now().strftime('%H:%M')})

@app.route('/api/gol_iminente')
def api_gol_iminente():
    jogos  = buscar_ao_vivo()
    result = [j for j in jogos if j.get('gol_iminente')]
    result.sort(key=lambda x: x.get('pressao', 0), reverse=True)
    return jsonify({'jogos': result, 'hora': datetime.now().strftime('%H:%M')})

@app.route('/api/btts')
def api_btts():
    jogos  = buscar_ao_vivo()
    result = sorted(jogos, key=lambda x: x.get('btts', 0), reverse=True)[:6]
    return jsonify({'jogos': result, 'hora': datetime.now().strftime('%H:%M')})

@app.route('/api/over05')
def api_over05():
    jogos = buscar_ao_vivo()
    result = [j for j in jogos
              if not j.get('over05_confirmado')
              and 10 <= j.get('minuto', 0) <= 40
              and (j.get('chutes_gol_c', 0) + j.get('chutes_gol_f', 0)) > 0
              and j.get('over05', 0) >= 65]
    result.sort(key=lambda x: x.get('over05', 0), reverse=True)
    return jsonify({'jogos': result[:6], 'hora': datetime.now().strftime('%H:%M')})

@app.route('/api/over15')
def api_over15():
    jogos = buscar_ao_vivo()
    result = [j for j in jogos
              if not j.get('over15_confirmado')
              and 10 <= j.get('minuto', 0) <= 85
              and (j.get('chutes_gol_c', 0) + j.get('chutes_gol_f', 0)) > 0
              and j.get('over15', 0) >= 65]
    result.sort(key=lambda x: x.get('over15', 0), reverse=True)
    return jsonify({'jogos': result[:6], 'hora': datetime.now().strftime('%H:%M')})

@app.route('/api/over25')
def api_over25():
    jogos = buscar_ao_vivo()
    result = [j for j in jogos
              if not j.get('over25_confirmado')
              and 10 <= j.get('minuto', 0) <= 85
              and (j.get('chutes_gol_c', 0) + j.get('chutes_gol_f', 0)) >= 2
              and j.get('over25', 0) >= 60]
    result.sort(key=lambda x: x.get('over25', 0), reverse=True)
    return jsonify({'jogos': result[:6], 'hora': datetime.now().strftime('%H:%M')})

@app.route('/api/escanteios')
def api_escanteios():
    jogos = buscar_ao_vivo()
    result = [j for j in jogos if j.get('funil_esc')]
    result.sort(key=lambda x: x.get('funil_esc', {}).get('conf', 0), reverse=True)
    return jsonify({'jogos': result[:5], 'hora': datetime.now().strftime('%H:%M')})

@app.route('/api/gol_ht')
def api_gol_ht():
    jogos  = buscar_ao_vivo()
    result = [j for j in jogos if j.get('elegivel_ht05')]
    result.sort(key=lambda x: calcular_pressao(x), reverse=True)
    return jsonify({'jogos': result[:5], 'hora': datetime.now().strftime('%H:%M')})

@app.route('/api/oportunidades')
def api_oportunidades():
    jogos  = buscar_ao_vivo()
    result = [j for j in jogos if j.get('oportunidade')]
    return jsonify({'jogos': result[:8], 'hora': datetime.now().strftime('%H:%M')})

@app.route('/api/proximos')
def api_proximos():
    cached = cache_get("proximos")
    if cached:
        jogos = cached
    else:
        jogos = buscar_proximos_jogos()
    agora  = datetime.now().strftime('%H:%M')
    offset = int(request.args.get('offset', 0))
    futuros = [j for j in jogos if j.get('horario', '99:99') > agora]
    pagina  = futuros[offset:offset+6]
    for j in pagina:
        j['previsao'] = prever_vencedor(j)
    return jsonify({
        'jogos': pagina,
        'total': len(futuros),
        'offset': offset,
        'hora': datetime.now().strftime('%H:%M')
    })

@app.route('/api/proximos/h2h/<int:jogo_idx>')
def api_h2h(jogo_idx):
    cached = cache_get("proximos")
    if not cached:
        cached = buscar_proximos_jogos()
    agora  = datetime.now().strftime('%H:%M')
    futuros = [j for j in cached if j.get('horario', '99:99') > agora]
    if jogo_idx >= len(futuros):
        return jsonify({'h2h': [], 'resumo': {}})
    j = futuros[jogo_idx]
    h2h = buscar_h2h(j.get('time_c_id'), j.get('time_f_id'), j['time_c'], j['time_f'])
    v_c = v_f = emp = 0
    for confronto in h2h:
        try:
            g_c, g_f = map(int, confronto['placar'].split('x'))
            if confronto['time_c'] == j['time_c']:
                if g_c > g_f: v_c += 1
                elif g_f > g_c: v_f += 1
                else: emp += 1
            else:
                if g_c > g_f: v_f += 1
                elif g_f > g_c: v_c += 1
                else: emp += 1
        except: pass
    return jsonify({'h2h': h2h, 'resumo': {'v_c': v_c, 'emp': emp, 'v_f': v_f}})

@app.route('/api/ligas_a')
def api_ligas_a():
    cached = cache_get("proximos")
    if not cached:
        cached = buscar_proximos_jogos()
    result = [j for j in cached if j.get('liga_maior')]
    agora  = datetime.now().strftime('%H:%M')
    futuros = [j for j in result if j.get('horario', '99:99') > agora]
    for j in futuros:
        j['previsao'] = prever_vencedor(j)
    return jsonify({'jogos': futuros[:10], 'hora': datetime.now().strftime('%H:%M')})

@app.route('/api/relatorio')
def api_relatorio():
    jogos   = buscar_ao_vivo()
    proximos = cache_get("proximos") or []
    elites  = [j for j in jogos if j.get('elite')]
    ops     = [j for j in jogos if j.get('oportunidade')]
    gol_im  = [j for j in jogos if j.get('gol_iminente')]
    return jsonify({
        'ao_vivo':     len(jogos),
        'proximos':    len(proximos),
        'elites':      len(elites),
        'oportunidades': len(ops),
        'gol_iminente': len(gol_im),
        'hora': datetime.now().strftime('%H:%M:%S'),
        'data': datetime.now().strftime('%d/%m/%Y'),
    })

@app.route('/api/mercados')
def api_mercados():
    jogos = buscar_ao_vivo()
    result = []
    for j in jogos[:8]:
        mercados = []
        if not j.get('over05_confirmado'): mercados.append({'nome': 'Over 0.5', 'prob': j.get('over05', 0), 'tipo': 'over05'})
        if not j.get('over15_confirmado'): mercados.append({'nome': 'Over 1.5', 'prob': j.get('over15', 0), 'tipo': 'over15'})
        if not j.get('over25_confirmado'): mercados.append({'nome': 'Over 2.5', 'prob': j.get('over25', 0), 'tipo': 'over25'})
        if not j.get('btts_confirmado'):   mercados.append({'nome': 'BTTS', 'prob': j.get('btts', 0), 'tipo': 'btts'})
        if mercados:
            melhor = max(mercados, key=lambda x: x['prob'])
            result.append({**j, 'melhor_mercado': melhor, 'todos_mercados': mercados})
    return jsonify({'jogos': result, 'hora': datetime.now().strftime('%H:%M')})

@app.route('/api/jogo/<jogo_id>')
def api_jogo_detalhe(jogo_id):
    jogos = buscar_ao_vivo()
    jogo  = next((j for j in jogos if j.get('id') == jogo_id), None)
    if not jogo:
        return jsonify({'erro': 'Jogo não encontrado'}), 404
    return jsonify(jogo)

@app.route('/api/buscar_jogo_h2h/<jogo_id>')
def api_buscar_h2h_live(jogo_id):
    jogos = buscar_ao_vivo()
    jogo  = next((j for j in jogos if j.get('id') == jogo_id), None)
    if not jogo:
        return jsonify({'h2h': [], 'resumo': {}})
    try:
        r = sofa_get(f"{BASE_SOFA}/event/{jogo_id}", timeout=5)
        if r.status_code == 200:
            ev = r.json().get('event', {})
            tc_id = ev.get('homeTeam', {}).get('id')
            tf_id = ev.get('awayTeam', {}).get('id')
            h2h = buscar_h2h(tc_id, tf_id, jogo['time_c'], jogo['time_f'])
            v_c = v_f = emp = 0
            for confronto in h2h:
                try:
                    g_c, g_f = map(int, confronto['placar'].split('x'))
                    if confronto['time_c'] == jogo['time_c']:
                        if g_c > g_f: v_c += 1
                        elif g_f > g_c: v_f += 1
                        else: emp += 1
                    else:
                        if g_c > g_f: v_f += 1
                        elif g_f > g_c: v_c += 1
                        else: emp += 1
                except: pass
            return jsonify({'h2h': h2h, 'resumo': {'v_c': v_c, 'emp': emp, 'v_f': v_f}})
    except: pass
    return jsonify({'h2h': [], 'resumo': {}})

@app.route('/api/proximo_jogo')
def api_proximo_jogo():
    cached = cache_get("proximos")
    if not cached:
        cached = buscar_proximos_jogos()
    agora  = datetime.now().strftime('%H:%M')
    offset = int(request.args.get('offset', 0))
    futuros = [j for j in cached if j.get('horario', '99:99') > agora]
    pagina  = futuros[offset:offset+3]
    for j in pagina:
        j['previsao'] = prever_vencedor(j)
        melhor_entrada = "⚠️ Sem entrada clara"
        melhor_prob    = 0
        btts_v  = j.get('btts', 0)
        over25  = j.get('over25', 0)
        forma_c = j.get('forma_c', 50)
        forma_f = j.get('forma_f', 50)
        candidatos = []
        if btts_v >= 55:   candidatos.append(('btts', btts_v, f"⚽⚽ Ambos Marcam"))
        if over25 >= 50:   candidatos.append(('over25', over25, "📈 Over 2.5"))
        if forma_c >= 65 and forma_c > forma_f + 15: candidatos.append(('vitoria_casa', forma_c * 0.9, f"🏠 Vitória {j['time_c']}"))
        if forma_f >= 65 and forma_f > forma_c + 15: candidatos.append(('vitoria_fora', forma_f * 0.9, f"✈️ Vitória {j['time_f']}"))
        if candidatos:
            candidatos.sort(key=lambda x: x[1], reverse=True)
            melhor_entrada = candidatos[0][2]
            melhor_prob    = round(candidatos[0][1], 1)
        j['melhor_entrada'] = melhor_entrada
        j['prob_entrada']   = melhor_prob
    return jsonify({
        'jogos': pagina,
        'total': len(futuros),
        'offset': offset,
        'hora': datetime.now().strftime('%H:%M')
    })


@app.route('/api/classificacao/<int:tournament_id>')
def api_classificacao(tournament_id):
    """Busca tabela de classificação pelo ID do torneio no SofaScore."""
    cache_key = f"classif_{tournament_id}"
    cached = cache_get(cache_key)
    if cached:
        return jsonify(cached)
    try:
        r = sofa_get(f"{BASE_SOFA}/unique-tournament/{tournament_id}/season/", timeout=8)
        if r.status_code != 200:
            return jsonify({'error': 'Não encontrado'}), 404
        seasons = r.json().get('seasons', [])
        if not seasons:
            return jsonify({'error': 'Sem temporadas'}), 404
        season_id = seasons[0].get('id')
        r2 = sofa_get(f"{BASE_SOFA}/unique-tournament/{tournament_id}/season/{season_id}/standings/total", timeout=8)
        if r2.status_code != 200:
            return jsonify({'error': 'Sem tabela'}), 404
        standings = r2.json().get('standings', [])
        if not standings:
            return jsonify({'error': 'Tabela vazia'}), 404
        rows = standings[0].get('rows', [])
        tabela = []
        for row in rows:
            tabela.append({
                'pos':     row.get('position'),
                'time':    row.get('team', {}).get('name', ''),
                'pts':     row.get('points'),
                'pj':      row.get('matches'),
                'v':       row.get('wins'),
                'e':       row.get('draws'),
                'd':       row.get('losses'),
                'gp':      row.get('scoresFor'),
                'gc':      row.get('scoresAgainst'),
                'sg':      row.get('scoreDifference'),
            })
        result = {'tabela': tabela, 'nome': standings[0].get('name', '')}
        cache_set(cache_key, result, ttl=3600)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/status')
def api_status():
    return jsonify({
        'online': True,
        'hora': datetime.now().strftime('%H:%M:%S'),
        'data': datetime.now().strftime('%d/%m/%Y'),
        'versao': '2.0',
        'demo_ativo': _DEMO_FORCADO
    })

@app.route('/api/demo', methods=['GET', 'POST'])
def api_demo():
    """Controla o modo demonstração manualmente."""
    global _DEMO_FORCADO
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        ativo = data.get('ativo', not _DEMO_FORCADO)
        _DEMO_FORCADO = bool(ativo)
        # Limpar cache para forçar recarga
        _cache.clear()
        return jsonify({'demo_ativo': _DEMO_FORCADO, 'msg': f'Modo demo {"ATIVADO ✅" if _DEMO_FORCADO else "DESATIVADO ❌"}'})
    return jsonify({'demo_ativo': _DEMO_FORCADO})

if __name__ == '__main__':
    print("🚀 Scanner Elite Web - Iniciando servidor...")
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

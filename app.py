"""StructQuest - Activity Provider para a Inven!RA.

Endpoints da Inven!RA:
  GET  /config                 pagina de configuracao (docente)
  GET  /params                 lista de parametros de configuracao (JSON)
  GET|POST /deploy             cria instancia e devolve o URL do aluno
  GET  /analytics/list         lista de analytics disponiveis
  GET|POST /analytics          analytics de uma instancia

API interna (usada pelas paginas):
  GET|PUT /api/instancias/<id>/config
  GET  /api/instancias/<id>/mapa?inveniraStdID=...
  POST /api/instancias/<id>/missoes/<mission_id>/submeter
"""
import copy
import os
import re
from datetime import datetime, timezone

from flask import Flask, abort, jsonify, render_template_string, request
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)

db_url = os.environ.get("DATABASE_URL", "sqlite:///structquest.db")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["JSON_AS_ASCII"] = False
db = SQLAlchemy(app)


# ---------------------------------------------------------------- modelos
class Instancia(db.Model):
    id = db.Column(db.String(100), primary_key=True)
    config = db.Column(db.JSON, nullable=False)
    criado_em = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class Tentativa(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    activity_id = db.Column(db.String(100), index=True, nullable=False)
    student_id = db.Column(db.String(100), index=True, nullable=False)
    mundo_id = db.Column(db.String(50), nullable=False)
    mission_id = db.Column(db.String(50), nullable=False)
    resposta = db.Column(db.Text)
    correta = db.Column(db.Boolean, nullable=False)
    pontos = db.Column(db.Integer, default=0)
    ts = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


with app.app_context():
    db.create_all()


# ------------------------------------------------------- configuracao base
CONFIG_DEFAULT = {
    "titulo": "StructQuest",
    "regras": {
        "nota_minima_desbloqueio": 60,  # % de pontos para dominar um mundo
        "max_tentativas": 0,            # 0 = ilimitadas
        "penalizacao_por_erro": 2,      # pontos perdidos por cada erro anterior
    },
    "mundos": [
        {
            "id": "listas", "nome": "Mundo das Listas", "estrutura": "Listas ligadas",
            "missoes": [
                {"id": "l1", "tipo": "quiz", "pontos": 10,
                 "enunciado": "Qual a complexidade de inserir no inicio de uma lista ligada simples?",
                 "opcoes": ["O(1)", "O(n)", "O(log n)"], "resposta": 0},
                {"id": "l2", "tipo": "completar_codigo", "pontos": 15,
                 "enunciado": "Complete: novo->next = ____; (insercao no inicio, cabeca = head)",
                 "respostas": ["head", "*head", "lista->head"]},
            ],
            "boss": {"id": "l_boss", "tipo": "debug", "pontos": 30,
                     "enunciado": "Este codigo perde a lista ao inserir: 'head = novo; novo->next = head;'. "
                                  "Qual a ordem correta das duas instrucoes? (escreva-as)",
                     "respostas": ["novo->next=head;head=novo;"]},
        },
        {
            "id": "pilhas", "nome": "Mundo das Pilhas e Filas", "estrutura": "Pilhas e Filas",
            "missoes": [
                {"id": "p1", "tipo": "quiz", "pontos": 10,
                 "enunciado": "Que politica segue uma pilha?",
                 "opcoes": ["FIFO", "LIFO", "Aleatoria"], "resposta": 1},
                {"id": "p2", "tipo": "prever_saida", "pontos": 15,
                 "enunciado": "Push(1), Push(2), Push(3), Pop(), Pop(). Qual o elemento no topo?",
                 "respostas": ["1"]},
            ],
            "boss": {"id": "p_boss", "tipo": "quiz", "pontos": 30,
                     "enunciado": "Qual estrutura e ideal para implementar 'anular' (undo)?",
                     "opcoes": ["Fila", "Pilha", "Arvore"], "resposta": 1},
        },
        {
            "id": "arvores", "nome": "Mundo das Arvores", "estrutura": "Arvores binarias de pesquisa",
            "missoes": [
                {"id": "a1", "tipo": "quiz", "pontos": 10,
                 "enunciado": "Que percurso de uma ABP devolve os valores ordenados?",
                 "opcoes": ["Pre-ordem", "Em-ordem", "Pos-ordem"], "resposta": 1},
                {"id": "a2", "tipo": "completar_codigo", "pontos": 15,
                 "enunciado": "Complete a pesquisa: if (x < no->valor) return pesquisa(____, x);",
                 "respostas": ["no->esq", "no->left"]},
            ],
            "boss": {"id": "a_boss", "tipo": "prever_saida", "pontos": 30,
                     "enunciado": "Inserindo 5, 3, 8, 1 numa ABP, qual o percurso em-ordem? (separe por espacos)",
                     "respostas": ["1 3 5 8"]},
        },
    ],
}

PARAMS = [
    {"name": "titulo", "type": "text/plain"},
    {"name": "nota_minima_desbloqueio", "type": "integer"},
    {"name": "max_tentativas", "type": "integer"},
    {"name": "penalizacao_por_erro", "type": "integer"},
    {"name": "mundos", "type": "json"},
]


def validar_config(cfg):
    """Devolve lista de erros (vazia se a configuracao e valida)."""
    erros = []
    if not isinstance(cfg, dict) or not isinstance(cfg.get("mundos"), list) or not cfg["mundos"]:
        return ["'mundos' deve ser uma lista nao vazia"]
    if not isinstance(cfg.get("regras"), dict):
        erros.append("'regras' em falta")
    ids = set()
    for w in cfg["mundos"]:
        if not w.get("id") or not w.get("boss") or not isinstance(w.get("missoes"), list):
            erros.append(f"mundo invalido: {w.get('id')}")
            continue
        for m in w["missoes"] + [w["boss"]]:
            if not m.get("id") or m["id"] in ids:
                erros.append(f"id de missao em falta ou repetido: {m.get('id')}")
            ids.add(m.get("id"))
            if m.get("tipo") == "quiz":
                if "resposta" not in m or not m.get("opcoes"):
                    erros.append(f"quiz {m.get('id')} precisa de 'opcoes' e 'resposta'")
            elif not m.get("respostas"):
                erros.append(f"missao {m.get('id')} precisa de 'respostas'")
            if not isinstance(m.get("pontos"), int) or m["pontos"] <= 0:
                erros.append(f"missao {m.get('id')} precisa de 'pontos' > 0")
    return erros


# --------------------------------------------------------------- logica
def norm(txt):
    return re.sub(r"\s+", "", str(txt)).lower()


def verificar(missao, resposta):
    if missao["tipo"] == "quiz":
        try:
            return int(resposta) == int(missao["resposta"])
        except (TypeError, ValueError):
            return False
    return norm(resposta) in {norm(r) for r in missao["respostas"]}


def todas_missoes(mundo):
    return [dict(m, boss=False) for m in mundo["missoes"]] + [dict(mundo["boss"], boss=True)]


def estado_aluno(inst, sid):
    """Calcula progresso, pontos e desbloqueios de um aluno."""
    cfg = inst.config
    regras = cfg["regras"]
    por_missao = {}
    for t in Tentativa.query.filter_by(activity_id=inst.id, student_id=sid).all():
        por_missao.setdefault(t.mission_id, []).append(t)

    mundos, anterior_dominado = [], True
    for w in cfg["mundos"]:
        missoes, ganhos, maximo = [], 0, 0
        normais_ok = True
        for m in todas_missoes(w):
            ts = por_missao.get(m["id"], [])
            certas = [t.pontos for t in ts if t.correta]
            concluida = bool(certas)
            pontos = max(certas) if certas else 0
            if not m["boss"] and not concluida:
                normais_ok = False
            ganhos += pontos
            maximo += m["pontos"]
            missoes.append({"id": m["id"], "tipo": m["tipo"], "boss": m["boss"],
                            "enunciado": m["enunciado"], "opcoes": m.get("opcoes"),
                            "pontos_max": m["pontos"], "pontos": pontos,
                            "tentativas": len(ts), "concluida": concluida,
                            "disponivel": anterior_dominado and (not m["boss"] or normais_ok)})
        dominado = all(x["concluida"] for x in missoes) and \
            maximo > 0 and ganhos * 100 / maximo >= regras.get("nota_minima_desbloqueio", 0)
        mundos.append({"id": w["id"], "nome": w["nome"], "estrutura": w.get("estrutura"),
                       "desbloqueado": anterior_dominado, "dominado": dominado,
                       "pontos": ganhos, "pontos_max": maximo, "missoes": missoes})
        anterior_dominado = dominado
    return {"aluno": sid, "mundos": mundos,
            "pontos_total": sum(w["pontos"] for w in mundos)}


def obter_instancia(activity_id):
    inst = db.session.get(Instancia, activity_id)
    if not inst:
        abort(404, description=f"Instancia '{activity_id}' nao existe")
    return inst


def param(nome):
    """Le um parametro do query string, form ou JSON."""
    corpo = request.get_json(silent=True) or {}
    return request.values.get(nome) or corpo.get(nome)


def student_id():
    return param("inveniraStdID") or param("Inven!RAstdID") or param("student_id")


@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(409)
def erro_json(e):
    return jsonify({"erro": e.description}), e.code


# ------------------------------------------------- endpoints Inven!RA
@app.route("/")
def raiz():
    return jsonify({"atividade": "StructQuest", "estado": "online"})


@app.route("/config")
def config_page():
    return render_template_string(CONFIG_HTML, activity_id=request.args.get("activityID", ""))


@app.route("/params")
def params():
    return jsonify(PARAMS)


@app.route("/deploy", methods=["GET", "POST"])
def deploy():
    activity_id = param("activityID")
    if not activity_id:
        abort(400, description="activityID em falta")
    inst = db.session.get(Instancia, activity_id)
    if not inst:
        inst = Instancia(id=activity_id, config=copy.deepcopy(CONFIG_DEFAULT))
        db.session.add(inst)
        db.session.commit()
    return jsonify(f"{request.host_url}atividade/{activity_id}")


@app.route("/analytics/list")
def analytics_list():
    return jsonify({
        "qualAnalytics": [
            {"name": "Mundo atual", "type": "text/plain"},
            {"name": "Missoes falhadas", "type": "text/plain"},
        ],
        "quantAnalytics": [
            {"name": "Pontos totais", "type": "integer"},
            {"name": "Missoes concluidas", "type": "integer"},
            {"name": "Mundos dominados", "type": "integer"},
            {"name": "Tentativas totais", "type": "integer"},
            {"name": "Taxa de acerto (%)", "type": "integer"},
        ],
    })


@app.route("/analytics", methods=["GET", "POST"])
def analytics():
    inst = obter_instancia(param("activityID"))
    sids = [r[0] for r in db.session.query(Tentativa.student_id)
            .filter_by(activity_id=inst.id).distinct()]
    saida = []
    for sid in sids:
        est = estado_aluno(inst, sid)
        tents = Tentativa.query.filter_by(activity_id=inst.id, student_id=sid).all()
        certas = sum(1 for t in tents if t.correta)
        falhadas = sorted({t.mission_id for t in tents if not t.correta})
        atual = next((w["nome"] for w in est["mundos"] if w["desbloqueado"] and not w["dominado"]),
                     "Concluido")
        saida.append({
            "inveniraStdID": sid,
            "quantAnalytics": [
                {"name": "Pontos totais", "value": est["pontos_total"]},
                {"name": "Missoes concluidas",
                 "value": sum(m["concluida"] for w in est["mundos"] for m in w["missoes"])},
                {"name": "Mundos dominados", "value": sum(w["dominado"] for w in est["mundos"])},
                {"name": "Tentativas totais", "value": len(tents)},
                {"name": "Taxa de acerto (%)", "value": round(100 * certas / len(tents)) if tents else 0},
            ],
            "qualAnalytics": [
                {"name": "Mundo atual", "value": atual},
                {"name": "Missoes falhadas", "value": ", ".join(falhadas) or "nenhuma"},
            ],
        })
    return jsonify(saida)


# ---------------------------------------------------------- API interna
@app.route("/api/instancias/<activity_id>/config", methods=["GET", "PUT"])
def config_instancia(activity_id):
    if request.method == "GET":
        inst = db.session.get(Instancia, activity_id)
        return jsonify(inst.config if inst else CONFIG_DEFAULT)
    cfg = request.get_json(silent=True)
    erros = validar_config(cfg)
    if erros:
        return jsonify({"erro": "configuracao invalida", "detalhes": erros}), 400
    inst = db.session.get(Instancia, activity_id)
    if inst:
        inst.config = cfg
    else:
        db.session.add(Instancia(id=activity_id, config=cfg))
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/instancias/<activity_id>/mapa")
def mapa(activity_id):
    sid = student_id()
    if not sid:
        abort(400, description="inveniraStdID em falta")
    return jsonify(estado_aluno(obter_instancia(activity_id), sid))


@app.route("/api/instancias/<activity_id>/missoes/<mission_id>/submeter", methods=["POST"])
def submeter(activity_id, mission_id):
    inst = obter_instancia(activity_id)
    sid = student_id()
    if not sid:
        abort(400, description="inveniraStdID em falta")
    resposta = param("resposta")
    if resposta is None:
        abort(400, description="resposta em falta")

    mundo = next((w for w in inst.config["mundos"]
                  if mission_id in [m["id"] for m in todas_missoes(w)]), None)
    if not mundo:
        abort(404, description="missao nao existe")
    missao = next(m for m in todas_missoes(mundo) if m["id"] == mission_id)

    estado = estado_aluno(inst, sid)
    m_estado = next(m for w in estado["mundos"] for m in w["missoes"] if m["id"] == mission_id)
    if not m_estado["disponivel"]:
        abort(409, description="missao bloqueada")
    if m_estado["concluida"]:
        abort(409, description="missao ja concluida")
    regras = inst.config["regras"]
    limite = regras.get("max_tentativas", 0)
    if limite and m_estado["tentativas"] >= limite:
        abort(409, description="limite de tentativas atingido")

    correta = verificar(missao, resposta)
    pontos = 0
    if correta:
        erros_antes = m_estado["tentativas"]
        pontos = max(0, missao["pontos"] - erros_antes * regras.get("penalizacao_por_erro", 0))
    db.session.add(Tentativa(activity_id=activity_id, student_id=sid, mundo_id=mundo["id"],
                             mission_id=mission_id, resposta=str(resposta),
                             correta=correta, pontos=pontos))
    db.session.commit()
    novo = estado_aluno(inst, sid)
    return jsonify({"correta": correta, "pontos": pontos,
                    "mundo_dominado": next(w["dominado"] for w in novo["mundos"] if w["id"] == mundo["id"]),
                    "pontos_total": novo["pontos_total"]})


# ------------------------------------------------------------- paginas
@app.route("/atividade/<activity_id>")
def atividade(activity_id):
    obter_instancia(activity_id)
    return render_template_string(ALUNO_HTML, activity_id=activity_id,
                                  sid=request.args.get("inveniraStdID", ""))


CONFIG_HTML = """<!doctype html><html lang="pt"><meta charset="utf-8">
<title>StructQuest - Configuracao</title>
<style>body{font-family:system-ui;max-width:800px;margin:2rem auto;padding:0 1rem}
textarea{width:100%;height:480px;font-family:monospace}button{padding:.5rem 1rem}</style>
<h1>StructQuest: configuracao</h1>
<p>Atividade: <input id="aid" value="{{ activity_id }}"></p>
<p>Edite os mundos, as missoes e as regras (JSON).</p>
<textarea id="cfg"></textarea><p><button onclick="guardar()">Guardar</button> <span id="msg"></span></p>
<script>
const aid=()=>document.getElementById('aid').value;
async function carregar(){const r=await fetch('/api/instancias/'+(aid()||'novo')+'/config');
 document.getElementById('cfg').value=JSON.stringify(await r.json(),null,2);}
async function guardar(){let c;try{c=JSON.parse(document.getElementById('cfg').value)}
 catch(e){msg.textContent='JSON invalido';return}
 const r=await fetch('/api/instancias/'+aid()+'/config',{method:'PUT',
  headers:{'Content-Type':'application/json'},body:JSON.stringify(c)});
 const j=await r.json();msg.textContent=r.ok?'Guardado':(j.detalhes||[j.erro]).join('; ');}
carregar();
</script></html>"""

ALUNO_HTML = """<!doctype html><html lang="pt"><meta charset="utf-8">
<title>StructQuest</title>
<style>body{font-family:system-ui;max-width:800px;margin:2rem auto;padding:0 1rem}
.mundo{border:1px solid #888;border-radius:8px;padding:1rem;margin:1rem 0}
.bloq{opacity:.45}.ok{color:#0a7d33}.m{margin:.6rem 0;padding:.4rem;border-left:3px solid #aaa}</style>
<h1>StructQuest</h1><p>Aluno: <input id="sid" value="{{ sid }}"> <button onclick="carregar()">Entrar</button>
 <b id="tot"></b></p><div id="mapa"></div>
<script>
const AID={{ activity_id|tojson }};
const q=s=>encodeURIComponent(s);
async function carregar(){
 const r=await fetch(`/api/instancias/${AID}/mapa?inveniraStdID=${q(sid.value)}`);
 const d=await r.json();if(!r.ok){mapa.textContent=d.erro;return}
 tot.textContent=' | Pontos: '+d.pontos_total;
 mapa.innerHTML=d.mundos.map(w=>`<div class="mundo ${w.desbloqueado?'':'bloq'}">
  <h2>${w.nome} ${w.dominado?'<span class=ok>(dominado)</span>':''} <small>${w.pontos}/${w.pontos_max}</small></h2>
  ${w.desbloqueado?w.missoes.map(m=>`<div class=m><b>${m.boss?'BOSS: ':''}${m.tipo}</b> (${m.pontos_max} pts)
   <p>${m.enunciado}</p>${m.concluida?'<span class=ok>Concluida: '+m.pontos+' pts</span>':
   m.disponivel?campo(m):'Bloqueada ate concluir as missoes normais'}</div>`).join(''):'Bloqueado: domine o mundo anterior.'}
 </div>`).join('');}
function campo(m){
 const c=m.opcoes?m.opcoes.map((o,i)=>`<label><input type=radio name="r_${m.id}" value="${i}"> ${o}</label><br>`).join('')
 :`<input id="r_${m.id}" size=40>`;
 return c+`<button onclick="enviar('${m.id}',${!!m.opcoes})">Submeter</button> <span id="f_${m.id}"></span>`;}
async function enviar(id,quiz){
 const v=quiz?(document.querySelector(`input[name=r_${id}]:checked`)||{}).value:document.getElementById('r_'+id).value;
 if(v===undefined||v===''){return}
 const r=await fetch(`/api/instancias/${AID}/missoes/${id}/submeter`,{method:'POST',
  headers:{'Content-Type':'application/json'},body:JSON.stringify({inveniraStdID:sid.value,resposta:v})});
 const d=await r.json();
 if(!r.ok){document.getElementById('f_'+id).textContent=d.erro;return}
 if(d.correta){carregar()}else{document.getElementById('f_'+id).textContent='Resposta errada, tenta outra vez.'}}
if(sid.value)carregar();
</script></html>"""


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))

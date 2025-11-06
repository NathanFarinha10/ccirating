# app_cci_rating.py
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import datetime
from dateutil.relativedelta import relativedelta
from fpdf import FPDF
import os
import json
from io import BytesIO
import uuid # Necessário para criar IDs únicos
import firebase_admin
from firebase_admin import credentials, firestore
from google.oauth2 import service_account
import plotly.express as px # Para o gráfico de linha

# ==============================================================================
# CONFIGURAÇÃO DO BANCO DE DADOS (FIRESTORE)
# ==============================================================================
# Define o nome da coleção no Firestore
DB_COLLECTION = "cci_operacoes"

# --- DEFINIÇÃO DOS VALORES PADRÃO ---
default_emissao = datetime.date(2024, 5, 1)
default_prazo_meses = 120 # 10 anos

# Valores padrão para os DADOS CADASTRAIS (só preenchidos uma vez)
DEFAULTS_CADASTRO = {
    'op_nome': 'Nova Operação', 'op_codigo': 'CCI-NEW',
    'op_emissor': 'Banco Exemplo S.A.', 'op_volume': 1000000.0,
    'op_taxa': 10.0, 'op_indexador': 'IPCA +', 'op_prazo': default_prazo_meses,
    'op_amortizacao': 'SAC',
    'op_data_emissao': default_emissao,
    'op_data_vencimento': default_emissao + relativedelta(months=+default_prazo_meses),
    'op_tipo': 'Interna', # 'Interna' ou 'Externa'
}

# Valores padrão para os DADOS DA ANÁLISE (resetados a cada nova análise)
DEFAULTS_ANALISE = {
    'analise_ref_atual': '', # Chave da análise (ex: 2025-Q4)
    'input_ltv': 75.0, 'input_demanda': 150000,
    'input_behavior_30_60': 0, 'input_behavior_60_90': 0, 'input_behavior_90_mais': 0,
    'input_comprometimento': 20.0,
    'input_inad_30_60': 0, 'input_inad_60_90': 0, 'input_inad_90_mais': 0,
    'justificativa_final': '',
    'scores_operacao': {}, # Resultados da análise ativa
    'rating_final_operacao': {}, # Resultados da análise ativa
}

# Combinação para inicialização e para coletar dados da sessão
DEFAULTS = {**DEFAULTS_CADASTRO, **DEFAULTS_ANALISE, 'historico_analises': {}}

# ==============================================================================
# CONEXÃO COM O FIREBASE
# ==============================================================================

@st.cache_resource
def get_firestore_client():
    """
    Inicializa o Firebase Admin e retorna o cliente Firestore.
    Usa st.cache_resource para garantir que isso seja executado apenas uma vez.
    """
    try:
        creds_json = dict(st.secrets["firebase_service_account"])
        
        if not firebase_admin._apps:
            cred_obj = credentials.Certificate(creds_json)
            firebase_admin.initialize_app(cred_obj)
            
        return firestore.client()
    
    except Exception as e:
        st.error("Erro ao conectar ao Firestore. Verifique suas credenciais nos Secrets.")
        st.error(e)
        return None

@st.cache_data(ttl=300) # Cache de 5 minutos
def carregar_db():
    """Carrega todos os dados do Firestore."""
    db = get_firestore_client()
    if db is None:
        return {}
        
    try:
        operacoes_ref = db.collection(DB_COLLECTION).stream()
        db_data = {}
        for op in operacoes_ref:
            db_data[op.id] = op.to_dict()
        return db_data
    except Exception as e:
        st.error(f"Erro ao carregar dados do Firestore: {e}")
        return {}

# ==============================================================================
# INICIALIZAÇÃO E GESTÃO DE ESTADO (SESSION_STATE)
# ==============================================================================

def inicializar_session_state():
    """Garante que todos os valores de input e scores sejam inicializados no st.session_state apenas uma vez."""
    if 'state_initialized_cci' not in st.session_state:
        st.session_state.state_initialized_cci = True
        
        # Controle de página
        st.session_state.pagina_atual = "painel" # 'painel', 'detalhe' ou 'analise'
        st.session_state.operacao_selecionada_id = None
        
        # Inicializa os campos do formulário com os padrões
        limpar_formulario_cadastro()
        limpar_formulario_analise()
        st.session_state.historico_analises = {}

def limpar_formulario_cadastro():
    """Reseta o session_state para os valores padrão de CADASTRO."""
    for key, value in DEFAULTS_CADASTRO.items():
        st.session_state[key] = value

def limpar_formulario_analise():
    """Reseta o session_state para os valores padrão de ANÁLISE."""
    for key, value in DEFAULTS_ANALISE.items():
        st.session_state[key] = value

def coletar_dados_estaticos_da_sessao():
    """Coleta apenas os dados de CADASTRO (estáticos) do st.session_state para salvar."""
    dados = {}
    for key in DEFAULTS_CADASTRO.keys():
        if key in st.session_state:
            value = st.session_state[key]
            if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
                dados[key] = datetime.datetime.combine(value, datetime.datetime.min.time())
            else:
                dados[key] = value
    return dados

def coletar_dados_analise_da_sessao():
    """Coleta apenas os dados da ANÁLISE ATIVA do st.session_state para salvar no histórico."""
    
    # 1. Coleta os inputs
    inputs = {}
    for key in DEFAULTS_ANALISE.keys():
        if key.startswith('input_'):
            inputs[key] = st.session_state[key]
            
    # 2. Coleta os resultados
    scores = st.session_state.scores_operacao
    resultados = st.session_state.rating_final_operacao
    justificativa = st.session_state.justificativa_final
    
    # 3. Monta o pacote da análise
    pacote_analise = {
        'data_analise': datetime.datetime.now(), # Data em que a análise foi salva
        'inputs': inputs,
        'scores': scores,
        'resultados': resultados,
        'justificativa': justificativa
    }
    return pacote_analise

# ==============================================================================
# FUNÇÕES AUXILIARES (Gráficos, PDF, etc.)
# ==============================================================================

def create_gauge_chart(score, title):
    """Cria um gráfico de velocímetro para a nota (escala 2-10)."""
    if score is None: score = 2.0
    fig = go.Figure(go.Indicator(
        mode="gauge+number", value=round(score, 2),
        title={'text': title, 'font': {'size': 20}},
        gauge={
            'axis': {'range': [2, 10], 'tickwidth': 1, 'tickcolor': "darkblue"},
            'bar': {'color': "black", 'thickness': 0.3}, 'bgcolor': "white", 'borderwidth': 1, 'bordercolor': "gray",
            'steps': [
                {'range': [2, 5], 'color': '#dc3545'},  # C e B
                {'range': [5, 7], 'color': '#ffc107'},  # A-
                {'range': [7, 10], 'color': '#28a745'}], # A e A+
        }))
    fig.update_layout(height=250, margin={'t':40, 'b':40, 'l':30, 'r':30})
    return fig

def converter_nota_para_rating(nota):
    """Converte a nota (10, 8, 6, 4, 2) para o rating (A+ ... C)."""
    nota = int(nota) # Garante que é int para comparação
    if nota == 10: return 'A+'
    elif nota == 8: return 'A'
    elif nota == 6: return 'A-'
    elif nota == 4: return 'B'
    elif nota == 2: return 'C'
    else: return "N/A"

def extrair_analise_mais_recente(historico_analises):
    """Encontra a análise mais recente no histórico."""
    if not historico_analises or not isinstance(historico_analises, dict):
        return None
    
    # Tenta ordenar pelas chaves (ex: "2025-Q4"). A ordem alfabética funciona.
    try:
        chave_recente = sorted(historico_analises.keys(), reverse=True)[0]
        return historico_analises[chave_recente]
    except Exception:
        return None # Retorna None se o histórico estiver mal formatado

class PDF(FPDF):
    """Classe de PDF personalizada para o relatório."""
    def header(self):
        try:
            if os.path.exists("assets/seu_logo.png"):
                self.image("assets/seu_logo.png", x=10, y=8, w=33)
        except Exception:
            self.set_xy(10, 10)
            self.set_font('Arial', 'I', 8)
            self.cell(0, 10, "[Logo]", 0, 0, 'L')

        self.set_font('Arial', 'B', 15)
        self.cell(0, 10, f'Relatório de Rating de CCI', 0, 0, 'C')
        self.ln(20)

    def footer(self):
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.cell(0, 10, f'Página {self.page_no()}', 0, 0, 'C')

    def _write_text(self, text):
        return str(text).encode('latin-1', 'replace').decode('latin-1')

    def chapter_title(self, title):
        self.set_font('Arial', 'B', 14)
        self.multi_cell(0, 10, self._write_text(title), 0, 'L')
        self.ln(4)

    def TabelaCadastro(self, ss):
        self.set_font('Arial', '', 10)
        line_height = self.font_size * 1.5
        col_width = self.epw / 4
        
        data_emissao = ss.op_data_emissao
        if isinstance(data_emissao, datetime.datetime): data_emissao = data_emissao.date()
        
        data_vencimento = ss.op_data_vencimento
        if isinstance(data_vencimento, datetime.datetime): data_vencimento = data_vencimento.date()

        data = {
            "Nome da Operação:": ss.op_nome, "Código/Série:": ss.op_codigo,
            "Volume Emitido:": f"R$ {ss.op_volume:,.2f}", "Taxa:": f"{ss.op_indexador} {ss.op_taxa}% a.a.",
            "Data de Emissão:": data_emissao.strftime('%d/%m/%Y'), "Vencimento:": data_vencimento.strftime('%d/%m/%Y'),
            "Emissor:": ss.op_emissor, "Tipo:": ss.op_tipo,
        }
        for i, (label, value) in enumerate(data.items()):
            if i > 0 and i % 2 == 0: self.ln(line_height)
            self.set_font('Arial', 'B', 10)
            self.cell(col_width, line_height, self._write_text(label), border=1)
            self.set_font('Arial', '', 10)
            self.cell(col_width, line_height, self._write_text(str(value)), border=1)
        self.ln(line_height)
        self.ln(10)

    def TabelaScorecard(self, ss, analise_ref):
        self.set_font('Arial', 'B', 10)
        line_height = self.font_size * 1.5
        col_widths = [self.epw * 0.4, self.epw * 0.15, self.epw * 0.15, self.epw * 0.15, self.epw * 0.15]
        headers = ["Atributo", "Peso", "Nota (2-10)", "Rating", "Score Ponderado"]
        for i, header in enumerate(headers): self.cell(col_widths[i], line_height, header, border=1, align='C')
        self.ln(line_height)
        
        self.set_font('Arial', '', 10)
        
        # Pega a análise correta (a ativa)
        scores = ss.scores_operacao
        
        nomes_inputs = {
            'ltv': '1. LTV',
            'demanda': '2. Demanda',
            'behavior': '3. Behavior',
            'comprometimento': '4. Comprometimento de Renda',
            'inadimplencia': '5. Inadimplência'
        }
        
        for key, nome in nomes_inputs.items():
            nota = float(scores.get(key, 2)) # Garante que é float
            rating = converter_nota_para_rating(nota)
            peso = 0.20
            row = [nome, f"{peso*100:.0f}%", f"{nota:.0f}", rating, f"{nota * peso:.2f}"]
            for i, item in enumerate(row): self.cell(col_widths[i], line_height, item, border=1, align='C')
            self.ln(line_height)
        self.ln(10)

def gerar_relatorio_pdf(ss):
    """Gera o PDF com os dados da análise ATIVA no session_state."""
    try:
        pdf = PDF()
        pdf.add_page()
        pdf.chapter_title('1. Dados Cadastrais da Operação')
        pdf.TabelaCadastro(ss) # Usa dados cadastrais do session_state

        analise_ref = ss.analise_ref_atual
        pdf.chapter_title(f'2. Scorecard e Rating (Análise: {analise_ref})')
        pdf.TabelaScorecard(ss, analise_ref) # Usa dados da análise ativa

        resultados = ss.rating_final_operacao
        nota_media = float(resultados.get('nota_media', 0))
        rating_final = resultados.get('rating_final', 'N/A')

        pdf.set_font('Arial', 'B', 12)
        pdf.cell(0, 10, f"Score Médio Ponderado: {nota_media:.2f}", 0, 1)
        pdf.cell(0, 10, f"Rating Final Atribuído: {rating_final}", 0, 1)
        pdf.set_font('Arial', 'B', 10)
        pdf.write(5, pdf._write_text(f"Justificativa: {ss.justificativa_final}"))
        pdf.ln(10)

        buffer = BytesIO()
        pdf.output(buffer)
        return buffer.getvalue()

    except Exception as e:
        st.error(f"Ocorreu um erro crítico ao gerar o PDF: {e}")
        st.exception(e) # Mostra o traceback completo
        return b''

# ==============================================================================
# FUNÇÕES DE CÁLCULO DE SCORE
# ==============================================================================

def calcular_nota_ltv(ltv):
    ltv_perc = float(ltv)
    if ltv_perc <= 60: return 10
    elif ltv_perc <= 70: return 8
    elif ltv_perc <= 80: return 6
    elif ltv_perc <= 90: return 4
    else: return 2

def calcular_nota_demanda(demanda):
    demanda = int(demanda)
    if demanda > 200000: return 10
    elif demanda >= 100000: return 8
    elif demanda >= 50000: return 6
    elif demanda >= 30000: return 4
    else: return 2

def calcular_nota_behavior(soma_behavior):
    soma_behavior = int(soma_behavior)
    if soma_behavior == 0: return 10
    elif soma_behavior == 2: return 8
    elif soma_behavior == 4: return 6
    elif soma_behavior == 6: return 4
    else: return 2 # > 6

def calcular_nota_comprometimento(comprometimento):
    comp_perc = float(comprometimento)
    if comp_perc < 15: return 10
    elif comp_perc <= 20: return 8
    elif comp_perc <= 25: return 6
    elif comp_perc <= 30: return 4
    else: return 2 # > 30

def calcular_nota_inadimplencia(soma_inad):
    soma_inad = int(soma_inad)
    if soma_inad == 0: return 10
    elif soma_inad <= 4: return 8 # 0-4 (mas 0 já foi pego, então 1-4)
    elif soma_inad <= 6: return 6 # 4-6 (mas >4, então 5-6)
    elif soma_inad <= 8: return 4 # 6-8 (mas >6, então 7-8)
    else: return 2 # > 8

def calcular_rating(inputs):
    """
    Função pura que calcula o rating com base em um dicionário de inputs.
    Retorna os scores e os resultados.
    """
    
    # 1. Calcular Somas de Penalização
    soma_behavior = (int(inputs.get('input_behavior_30_60', 0)) * 2) + \
                    (int(inputs.get('input_behavior_60_90', 0)) * 4) + \
                    (int(inputs.get('input_behavior_90_mais', 0)) * 6)
    
    soma_inad = (int(inputs.get('input_inad_30_60', 0)) * 2) + \
                (int(inputs.get('input_inad_60_90', 0)) * 4) + \
                (int(inputs.get('input_inad_90_mais', 0)) * 6)

    # 2. Calcular Notas Individuais
    nota_ltv = calcular_nota_ltv(inputs.get('input_ltv', 999))
    nota_demanda = calcular_nota_demanda(inputs.get('input_demanda', 0))
    nota_behavior = calcular_nota_behavior(soma_behavior)
    nota_comp = calcular_nota_comprometimento(inputs.get('input_comprometimento', 999))
    nota_inad = calcular_nota_inadimplencia(soma_inad)

    # 3. Armazenar notas individuais (convertendo para tipos nativos)
    scores_operacao = {
        'ltv': int(nota_ltv),
        'demanda': int(nota_demanda),
        'behavior': int(nota_behavior),
        'comprometimento': int(nota_comp),
        'inadimplencia': int(nota_inad),
        'soma_behavior': int(soma_behavior),
        'soma_inad': int(soma_inad)
    }

    # 4. Calcular Média Ponderada
    lista_notas = [nota_ltv, nota_demanda, nota_behavior, nota_comp, nota_inad]
    nota_media = np.mean(lista_notas) # Média simples é igual a ponderada de 20%
    
    # 5. Mapear nota média para a nota final (10, 8, 6, 4, 2)
    possible_scores = np.array([2, 4, 6, 8, 10])
    idx = np.abs(possible_scores - float(nota_media)).argmin()
    nota_final_arredondada = possible_scores[idx]
    
    rating_final = converter_nota_para_rating(nota_final_arredondada)

    # 6. Armazenar resultado final (convertendo para tipos nativos)
    rating_final_operacao = {
        'nota_media': float(nota_media),
        'nota_final': int(nota_final_arredondada),
        'rating_final': str(rating_final)
    }
    
    return scores_operacao, rating_final_operacao

# ==============================================================================
# CALLBACKS DE NAVEGAÇÃO E AÇÕES
# ==============================================================================

def callback_voltar_painel():
    """(De Detalhe/Análise -> Painel) Volta para o painel e limpa tudo."""
    st.session_state.pagina_atual = "painel"
    limpar_formulario_cadastro()
    limpar_formulario_analise()
    st.session_state.historico_analises = {}
    st.session_state.operacao_selecionada_id = None

def callback_voltar_detalhe():
    """(De Análise -> Detalhe) Volta para a pág de detalhe, limpando a análise ativa."""
    st.session_state.pagina_atual = "detalhe"
    # Limpa apenas os dados da análise, mantendo os cadastrais e o histórico
    limpar_formulario_analise() 
    # O ID da operação e o histórico são mantidos

def callback_nova_operacao():
    """(Do Painel -> Análise) Prepara o estado para cadastrar uma nova operação e sua primeira análise."""
    limpar_formulario_cadastro() # Limpa dados cadastrais (formulário novo)
    limpar_formulario_analise() # Limpa dados de análise (formulário novo)
    st.session_state.historico_analises = {} # Histórico vazio
    st.session_state.operacao_selecionada_id = str(uuid.uuid4()) # Gera um novo ID
    st.session_state.analise_ref_atual = "" # Força o usuário a digitar
    st.session_state.pagina_atual = "analise"

def callback_selecionar_operacao(op_id, op_data):
    """(Do Painel -> Detalhe) Carrega dados de uma op para a página de DETALHE."""
    # Limpa TUDO primeiro para garantir um estado limpo
    limpar_formulario_cadastro() 
    limpar_formulario_analise()
    st.session_state.historico_analises = {}
    
    st.session_state.pagina_atual = "detalhe"
    st.session_state.operacao_selecionada_id = op_id

    # Carrega todos os dados do banco para o session_state
    for key, value in op_data.items():
        if key == 'historico_analises':
             st.session_state.historico_analises = value if isinstance(value, dict) else {}
        
        elif key in DEFAULTS_CADASTRO:
            # Converte timestamps do Firestore de volta para datetime.date
            if key in ['op_data_emissao', 'op_data_vencimento'] and isinstance(value, datetime.datetime):
                st.session_state[key] = value.date()
            else:
                st.session_state[key] = value

def callback_ir_para_analise(analise_ref_para_editar):
    """(Do Detalhe -> Análise) Prepara o editor para criar ou editar uma análise."""
    st.session_state.pagina_atual = "analise"
    
    if analise_ref_para_editar is None:
        # --- CRIAR NOVA ANÁLISE ---
        # Reseta APENAS os campos da análise, mantendo os dados cadastrais
        limpar_formulario_analise()
        # Os dados cadastrais (op_nome, etc.) que já estão no session_state são preservados.
        
    else:
        # --- EDITAR ANÁLISE EXISTENTE ---
        st.session_state.analise_ref_atual = analise_ref_para_editar
        
        # Carrega os dados daquela análise específica para o formulário
        try:
            dados_analise = st.session_state.historico_analises[analise_ref_para_editar]
            
            # Carrega Inputs
            inputs = dados_analise.get('inputs', {})
            st.session_state.input_ltv = inputs.get('input_ltv', DEFAULTS_ANALISE['input_ltv'])
            st.session_state.input_demanda = inputs.get('input_demanda', DEFAULTS_ANALISE['input_demanda'])
            st.session_state.input_behavior_30_60 = inputs.get('input_behavior_30_60', 0)
            st.session_state.input_behavior_60_90 = inputs.get('input_behavior_60_90', 0)
            st.session_state.input_behavior_90_mais = inputs.get('input_behavior_90_mais', 0)
            st.session_state.input_comprometimento = inputs.get('input_comprometimento', DEFAULTS_ANALISE['input_comprometimento'])
            st.session_state.input_inad_30_60 = inputs.get('input_inad_30_60', 0)
            st.session_state.input_inad_60_90 = inputs.get('input_inad_60_90', 0)
            st.session_state.input_inad_90_mais = inputs.get('input_inad_90_mais', 0)
            
            # Carrega Resultados Salvos (para referência)
            st.session_state.scores_operacao = dados_analise.get('scores', {})
            st.session_state.rating_final_operacao = dados_analise.get('resultados', {})
            st.session_state.justificativa_final = dados_analise.get('justificativa', '')
            
        except Exception as e:
            st.error(f"Erro ao carregar dados da análise '{analise_ref_para_editar}': {e}")
            limpar_formulario_analise() # Reseta em caso de erro

def callback_deletar_operacao(op_id):
    """(Do Painel) Deleta uma operação inteira do banco de dados Firestore."""
    db = get_firestore_client()
    if db is None: return
        
    try:
        db.collection(DB_COLLECTION).document(op_id).delete()
        st.toast(f"Operação {op_id} deletada.", icon="🗑️")
        carregar_db.clear() # Limpa o cache para forçar recarregar
    except Exception as e:
        st.error(f"Erro ao deletar operação: {e}")

def callback_calcular_e_salvar():
    """(Da Análise) Calcula o rating e salva a análise no histórico da operação."""
    
    # --- 1. Validação ---
    op_id = st.session_state.operacao_selecionada_id
    if not op_id:
        st.error("Erro: ID da operação não definido. Tente novamente.")
        return
        
    analise_ref = st.session_state.analise_ref_atual
    if not analise_ref or len(analise_ref.strip()) < 4:
        st.error("Erro: A 'Referência da Análise' (Ex: 2025-Q4) é obrigatória.")
        return

    # --- 2. Coletar Dados Estáticos (Cadastro) ---
    # Isso garante que os dados de cadastro sejam salvos/atualizados na primeira vez
    dados_para_salvar = coletar_dados_estaticos_da_sessao()
    
    # --- 3. Calcular a Análise ---
    inputs_atuais = {}
    for key in DEFAULTS_ANALISE.keys():
        if key.startswith('input_'):
            inputs_atuais[key] = st.session_state[key]
            
    scores_calc, resultados_calc = calcular_rating(inputs_atuais)
    
    # Atualiza o session_state com os resultados calculados (para o PDF)
    st.session_state.scores_operacao = scores_calc
    st.session_state.rating_final_operacao = resultados_calc
    
    # --- 4. Montar Pacote da Análise ---
    pacote_analise = {
        'data_analise': datetime.datetime.now(),
        'inputs': inputs_atuais,
        'scores': scores_calc,
        'resultados': resultados_calc,
        'justificativa': st.session_state.justificativa_final
    }

    # --- 5. Salvar no Firestore ---
    db = get_firestore_client()
    if db is None: return
        
    try:
        doc_ref = db.collection(DB_COLLECTION).document(op_id)
        
        # Usa 'set' com 'merge=True' para salvar/atualizar os dados cadastrais
        # E usa 'set' com 'merge=True' para adicionar/atualizar a análise no histórico
        dados_para_salvar['historico_analises'] = {
            analise_ref: pacote_analise
        }
        
        doc_ref.set(dados_para_salvar, merge=True) # merge=True é crucial
        
        # Limpa o cache do DB para que o painel e o detalhe sejam atualizados
        carregar_db.clear()
        
        # Atualiza o histórico no session_state local
        st.session_state.historico_analises[analise_ref] = pacote_analise
        
        st.success(f"Análise '{analise_ref}' salva com sucesso!")
        
        # --- CORREÇÃO ---
        # Não chame outro callback. Apenas mude a página.
        # O Streamlit vai recarregar e renderizar a página de detalhe.
        st.session_state.pagina_atual = "detalhe"
        # callback_voltar_detalhe() # REMOVIDO
        # --- FIM DA CORREÇÃO ---
        
    except Exception as e:
        st.error(f"Erro ao salvar no Firestore: {e}")
        st.exception(e) # Mostra o traceback completo

# ==============================================================================
# RENDERIZAÇÃO DAS PÁGINAS (Views)
# ==============================================================================

def renderizar_tabela_operacoes(operacoes_filtradas):
    """Função auxiliar para renderizar a tabela no painel."""
    
    if not operacoes_filtradas:
        st.info("Nenhuma operação cadastrada neste grupo.")
        return
        
    # Define as colunas do painel
    col1, col2, col3, col4, col5 = st.columns([3, 2, 1, 1, 1])
    col1.markdown("**Nome da Operação**")
    col2.markdown("**Código**")
    col3.markdown("**Rating (Último)**")
    col4.markdown("**Ação**")
    col5.markdown("**Excluir**")

    # Itera e exibe cada operação
    for op_id, op_data in operacoes_filtradas:
        op_nome = op_data.get('op_nome', 'Sem Nome')
        op_codigo = op_data.get('op_codigo', 'N/A')
        
        # Pega o rating final da análise mais recente
        historico = op_data.get('historico_analises', {})
        analise_recente = extrair_analise_mais_recente(historico)
        
        if analise_recente:
            rating_final = analise_recente.get('resultados', {}).get('rating_final', 'N/A')
        else:
            rating_final = 'N/A'
        
        with st.container():
            c1, c2, c3, c4, c5 = st.columns([3, 2, 1, 1, 1])
            c1.write(op_nome)
            c2.write(op_codigo)
            
            # Adiciona cor ao rating
            if rating_final.startswith('A'):
                c3.markdown(f"**<span style='color:green;'>{rating_final}</span>**", unsafe_allow_html=True)
            elif rating_final == 'B':
                c3.markdown(f"**<span style='color:orange;'>{rating_final}</span>**", unsafe_allow_html=True)
            elif rating_final == 'C':
                c3.markdown(f"**<span style='color:red;'>{rating_final}</span>**", unsafe_allow_html=True)
            else:
                c3.write(rating_final)

            # Botões de Ação
            c4.button("Analisar", key=f"analisar_{op_id}", on_click=callback_selecionar_operacao, args=(op_id, op_data), use_container_width=True)
            c5.button("🗑️", key=f"deletar_{op_id}", on_click=callback_deletar_operacao, args=(op_id,), use_container_width=True, help="Deletar operação")

def renderizar_painel():
    """Renderiza o painel principal com a lista de operações."""
    st.header("Painel de Operações de CCI")
    
    if st.button("Cadastrar Nova Operação", type="primary", use_container_width=True):
        callback_nova_operacao()
        st.rerun() # Força o rerender para a página de análise

    st.divider()
    
    db_data = carregar_db()
    
    if not db_data:
        st.info("Nenhuma operação cadastrada. Clique em 'Cadastrar Nova Operação' para começar.")
        return

    # Filtra operações
    ops_internas = []
    ops_externas = []
    for op_id, op_data in db_data.items():
        if op_data.get('op_tipo', 'Interna') == 'Interna':
            ops_internas.append((op_id, op_data))
        else:
            ops_externas.append((op_id, op_data))
            
    # Cria abas para os tipos
    tab_int, tab_ext = st.tabs([
        f"Operações Internas ({len(ops_internas)})",
        f"Operações Externas ({len(ops_externas)})"
    ])
    
    with tab_int:
        renderizar_tabela_operacoes(ops_internas)
        
    with tab_ext:
        renderizar_tabela_operacoes(ops_externas)
            
    st.divider()

def renderizar_detalhe_operacao():
    """Renderiza a página de detalhe de uma operação específica."""
    
    if st.button("⬅️ Voltar ao Painel"):
        callback_voltar_painel()
        st.rerun()

    st.header(f"Detalhe: {st.session_state.op_nome}")
    st.caption(f"ID: {st.session_state.operacao_selecionada_id}")
    
    # 1. Recupera o histórico
    historico = st.session_state.historico_analises
    
    # 2. Encontra a análise mais recente
    analise_recente = extrair_analise_mais_recente(historico)
    
    if not analise_recente:
        st.warning("Esta operação ainda não possui análises.")
        if st.button("Criar Primeira Análise", type="primary"):
            callback_ir_para_analise(None) # Vai para o editor
            st.rerun()
        return

    st.divider()
    
    # --- Seção do Rating Atual ---
    st.subheader("Rating Mais Recente")
    resultados_recentes = analise_recente.get('resultados', {})
    nota_media = float(resultados_recentes.get('nota_media', 0))
    rating_final = resultados_recentes.get('rating_final', 'N/A')
    
    col_gauge, col_metrics = st.columns([2, 1])
    with col_gauge:
        st.plotly_chart(create_gauge_chart(nota_media, "Score Médio Ponderado (Última Análise)"), use_container_width=True)
    with col_metrics:
        st.metric("Score Médio (0-10)", f"{nota_media:.2f}")
        st.metric("Rating Final Atribuído", rating_final)
    
    st.divider()
    
    # --- Seção do Histórico ---
    st.subheader("Histórico de Análises")
    
    if len(historico) > 0:
        # Prepara dados para o gráfico
        data_grafico = []
        for ref, analise in historico.items():
            data_grafico.append({
                "Referência": ref,
                "Nota Média": float(analise.get('resultados', {}).get('nota_media', 0)),
                "Rating": analise.get('resultados', {}).get('rating_final', 'N/A')
            })
        
        # Ordena pela Referência (ex: 2024-Q4, 2025-Q1)
        df_grafico = pd.DataFrame(data_grafico).sort_values(by="Referência")
        
        # Gráfico de Linha
        if len(df_grafico) > 1:
            fig = px.line(df_grafico, x="Referência", y="Nota Média", title="Evolução da Nota Média da Operação",
                          text="Rating", markers=True)
            fig.update_traces(textposition="top center")
            st.plotly_chart(fig, use_container_width=True)
        else:
             st.info("Apenas uma análise registrada. O gráfico de evolução será exibido quando houver 2 ou mais análises.")

    else:
        st.info("Nenhuma análise registrada para esta operação.")


    # Lista de análises para edição
    st.markdown("**Gerenciar Análises:**")
    
    # Botão para criar nova
    if st.button("Criar Nova Análise (Ex: 2025-Q2)", use_container_width=True):
        callback_ir_para_analise(None) # Envia None para indicar "nova"
        st.rerun()
        
    st.markdown("Editar análise anterior:")
    
    # Botões para editar existentes
    if historico:
        col_edit = st.columns(4)
        i = 0
        refs_ordenadas = sorted(historico.keys(), reverse=True) # Mais recentes primeiro
        for ref in refs_ordenadas:
            col = col_edit[i % 4]
            col.button(f"Editar {ref}", key=f"edit_{ref}", on_click=callback_ir_para_analise, args=(ref,), use_container_width=True)
            i += 1
    else:
        st.caption("Nenhuma análise para editar.")


def renderizar_analise():
    """Renderiza a página de análise (abas de cadastro, inputs, resultado)."""
    
    # Verifica se é uma análise nova ou edição
    is_primeira_analise = (st.session_state.historico_analises == {})
    
    if is_primeira_analise:
        st.header(f"Cadastrar Nova Operação: {st.session_state.op_nome}")
        # Botão de voltar para o painel
        if st.button("⬅️ Voltar ao Painel (Cancelar)"):
            callback_voltar_painel()
            st.rerun()
    else:
        st.header(f"Analisando: {st.session_state.op_nome}")
        # Botão de voltar para o detalhe
        if st.button("⬅️ Voltar aos Detalhes (Cancelar)"):
            callback_voltar_detalhe()
            st.rerun()

    
    # --- DEFINIÇÃO DAS ABAS ---
    tab0, tab_inputs, tab_res, tab_met = st.tabs([
        "1. Dados Cadastrais", "2. Inputs da Análise", "3. Resultado (Preview)", "Metodologia"
    ])

    # --- ABA 0: CADASTRO ---
    with tab0:
        st.header("Informações Gerais da Operação (Dados Cadastrais)")
        
        # --- LÓGICA DE TRAVAMENTO ---
        # Se NÃO for a primeira análise, desabilita os campos
        campos_desabilitados = not is_primeira_analise 
        
        if campos_desabilitados:
            st.info("Os dados cadastrais são compartilhados por todas as análises e não podem ser editados após a primeira análise.")
        else:
            st.info("Preencha os dados cadastrais. Eles serão salvos com a primeira análise e não poderão ser alterados depois.")
        # --- FIM DA LÓGICA ---
            
        col1, col2 = st.columns(2)
        with col1:
            st.text_input("Nome/Identificação da CCI:", key='op_nome', disabled=campos_desabilitados)
            st.number_input("Volume da Operação (R$):", key='op_volume', format="%.2f", disabled=campos_desabilitados)
            st.selectbox("Sistema de Amortização:", ["SAC", "Price"], key='op_amortizacao', disabled=campos_desabilitados)
            st.date_input("Data de Emissão:", key='op_data_emissao', disabled=campos_desabilitados)
        with col2:
            st.text_input("Código/Série:", key='op_codigo', disabled=campos_desabilitados)
            c1_taxa, c2_taxa = st.columns([1, 2])
            with c1_taxa: st.selectbox("Indexador:", ["IPCA +", "CDI +", "Pré-fixado"], key='op_indexador', disabled=campos_desabilitados)
            with c2_taxa: st.number_input("Taxa (% a.a.):", key='op_taxa', format="%.2f", disabled=campos_desabilitados)
            st.number_input("Prazo Remanescente (meses):", key='op_prazo', step=1, disabled=campos_desabilitados)
            st.date_input(
                "Data de Vencimento:",
                key='op_data_vencimento',
                min_value=st.session_state.op_data_emissao,
                disabled=campos_desabilitados
            )
        
        st.radio("Tipo de Operação:", ["Interna", "Externa"], key='op_tipo', horizontal=True, disabled=campos_desabilitados)
        st.text_input("Emissor da CCI (Ex: Banco, Securitizadora):", key='op_emissor', disabled=campos_desabilitados)

    # --- ABA 1: INPUTS DA ANÁLISE ---
    with tab_inputs:
        st.header("Inputs para o Rating")
        st.info("Estes dados são específicos para esta análise.")
        
        # Campo obrigatório para a referência da análise
        st.text_input(
            "**Referência da Análise (Obrigatório)**", 
            key='analise_ref_atual',
            help="Ex: 2025-Q1, 2024-Q4, etc. Esta será a chave para salvar no histórico."
        )
        st.divider()

        col1, col2 = st.columns(2)
        with col1:
            with st.container(border=True):
                st.subheader("1. LTV (Loan-to-Value)")
                st.number_input("LTV da operação (%)", key='input_ltv', min_value=0.0, max_value=200.0, step=1.0, format="%.2f")
            with st.container(border=True):
                st.subheader("2. Demanda")
                st.number_input("Valor da Demanda (Ex: R$)", key='input_demanda', min_value=0, step=1000)
            with st.container(border=True):
                st.subheader("3. Behavior (Penalização)")
                st.number_input("Qtd. Atrasos 30-60 dias", key='input_behavior_30_60', min_value=0, step=1)
                st.number_input("Qtd. Atrasos 60-90 dias", key='input_behavior_60_90', min_value=0, step=1)
                st.number_input("Qtd. Atrasos >90 dias", key='input_behavior_90_mais', min_value=0, step=1)
        with col2:
            with st.container(border=True):
                st.subheader("4. Comprometimento de Renda")
                st.number_input("Comprometimento de Renda (%)", key='input_comprometimento', min_value=0.0, max_value=100.0, step=0.5, format="%.2f")
            with st.container(border=True):
                st.subheader("5. Inadimplência (Penalização)")
                st.number_input("Qtd. Inad. 30-60 dias", key='input_inad_30_60', min_value=0, step=1)
                st.number_input("Qtd. Inad. 60-90 dias", key='input_inad_60_90', min_value=0, step=1)
                st.number_input("Qtd. Inad. >90 dias", key='input_inad_90_mais', min_value=0, step=1)
        
        st.divider()
        st.text_area("Justificativa e comentários finais (opcional):", height=100, key='justificativa_final')
        st.divider()
        
        # Botão de Salvar
        if st.button("Calcular e Salvar Análise", use_container_width=True, type="primary"):
            callback_calcular_e_salvar()
            # Se o callback for bem-sucedido, ele mesmo mudará a página
            # Se falhar (ex: validação), ele mostrará um erro e ficará nesta página

    # --- ABA 2: RESULTADO (PREVIEW) ---
    with tab_res:
        st.header("Resultado da Análise (Preview)")
        st.warning("Este é um preview. Os dados só serão salvos permanentemente quando você clicar em 'Calcular e Salvar Análise' na aba 'Inputs'.")
        
        # Pega os inputs atuais
        inputs_preview = {}
        for key in DEFAULTS_ANALISE.keys():
            if key.startswith('input_'):
                inputs_preview[key] = st.session_state[key]
        
        # Calcula o preview
        scores_preview, resultados_preview = calcular_rating(inputs_preview)
            
        st.subheader("Scorecard Mestre (Preview)")
        
        nomes_inputs = {
            'ltv': '1. LTV', 'demanda': '2. Demanda', 'behavior': '3. Behavior',
            'comprometimento': '4. Comprometimento de Renda', 'inadimplencia': '5. Inadimplência'
        }
        data = []
        for key, nome in nomes_inputs.items():
            nota = float(scores_preview.get(key, 2))
            rating_input = converter_nota_para_rating(nota)
            peso = 0.20
            data.append({
                'Atributo': nome, 'Peso': f"{peso*100:.0f}%", 'Nota (2-10)': nota,
                'Rating': rating_input, 'Score Ponderado': f"{nota * peso:.2f}"
            })
        
        df_scores = pd.DataFrame(data).set_index('Atributo')
        st.table(df_scores)
        st.divider()
        
        nota_media = float(resultados_preview.get('nota_media', 0))
        nota_final = float(resultados_preview.get('nota_final', 0))
        rating_final = resultados_preview.get('rating_final', 'N/A')
        
        st.subheader("Resultado Final Ponderado (Preview)")
        col_gauge, col_metrics = st.columns([2, 1])
        
        with col_gauge:
            st.plotly_chart(create_gauge_chart(nota_media, "Score Médio Ponderado"), use_container_width=True)
        with col_metrics:
            st.metric("Score Médio (0-10)", f"{nota_media:.2f}")
            st.metric("Nota Final (Mais Próxima)", f"{nota_final:.0f}")
            st.metric("Rating Final Atribuído", rating_final)
        
        st.info(f"Somas de Penalização (Referência): Behavior = {int(scores_preview.get('soma_behavior', 0))}, Inadimplência = {int(scores_preview.get('soma_inad', 0))}")
        st.divider()

        st.subheader("⬇️ Download do Relatório (Preview)")
        st.warning("O PDF será gerado com os dados *atualmente em tela* (preview).")
        
        # Atualiza o state com os dados de preview para o PDF
        st.session_state.scores_operacao = scores_preview
        st.session_state.rating_final_operacao = resultados_preview
        
        pdf_data = gerar_relatorio_pdf(st.session_state)
        pdf_nome = f"Relatorio_CCI_{st.session_state.op_nome.replace(' ', '_')}_{st.session_state.analise_ref_atual}.pdf"
        st.download_button(
            label="Baixar Relatório (Preview) em PDF", data=pdf_data,
            file_name=pdf_nome,
            mime="application/pdf", use_container_width=True
        )

    # --- ABA 3: METODOLOGIA ---
    with tab_met:
        st.header("Metodologia de Rating")
        st.markdown("Esta metodologia atribui um rating a uma CCI com base em 5 atributos, cada um com peso igual de 20%.")
        st.subheader("1. Atributos e Pesos")
        st.markdown("- **1. LTV:** (Peso: 20%)\n- **2. Demanda:** (Peso: 20%)\n- **3. Behavior:** (Peso: 20%)\n- **4. Comprometimento de Renda:** (Peso: 20%)\n- **5. Inadimplência:** (Peso: 20%)")
        st.subheader("2. Escala de Notas e Ratings")
        st.markdown("- **Nota 10:** Rating 'A+'\n- **Nota 8:** Rating 'A'\n- **Nota 6:** Rating 'A-'\n- **Nota 4:** Rating 'B'\n- **Nota 2:** Rating 'C'")
        st.subheader("3. Cálculo Final")
        st.markdown("1. A nota de cada um dos 5 atributos (10, 8, 6, 4 ou 2) é calculada.\n2. É calculada a média ponderada das 5 notas (como todas têm 20%, é uma média simples).\n3. A média (ex: 7.8) é então arredondada para a \"Nota Final\" mais próxima da escala (neste caso, 8).\n4. O \"Rating Final\" é atribuído com base nessa \"Nota Final\" (neste caso, 'A').")

        with st.expander("Faixas de Pontuação Detalhadas"):
            st.markdown("""
            **Input 1: LTV**
            - `<=60%`: 10 | `60-70%`: 8 | `70-80%`: 6 | `80-90%`: 4 | `90+%`: 2
            **Input 2: Demanda**
            - `>200000`: 10 | `100000-200000`: 8 | `50000-100000`: 6 | `30000-50000`: 4 | `<30000`: 2
            **Input 3: Behavior**
            - *Soma = (Qtd 30-60 \* 2) + (Qtd 60-90 \* 4) + (Qtd >90 \* 6)*
            - `Soma 0`: 10 | `Soma 2`: 8 | `Soma 4`: 6 | `Soma 6`: 4 | `Soma >6`: 2
            **Input 4: Comprometimento de Renda**
            - `<15%`: 10 | `15-20%`: 8 | `20-25%`: 6 | `25-30%`: 4 | `>30%`: 2
            **Input 5: Inadimplência**
            - *Soma = (Qtd 30-60 \* 2) + (Qtd 60-90 \* 4) + (Qtd >90 \* 6)*
            - `Soma 0`: 10 | `Soma 1-4`: 8 | `Soma 5-6`: 6 | `Soma 7-8`: 4 | `Soma >8`: 2
            """)

# ==============================================================================
# CORPO PRINCIPAL DA APLICAÇÃO (ROTEADOR)
# ==============================================================================
st.set_page_config(layout="wide", page_title="Rating de CCIs")

# Renderização do cabeçalho
col1, col2 = st.columns([1, 3])
with col1:
    if os.path.exists("assets/seu_logo.png"):
        st.image("assets/seu_logo.png", use_container_width=True)
    else:
        st.caption("Seu Logo Aqui")
with col2:
    st.title("Plataforma de Rating de CCIs")
    st.markdown("Ferramenta para análise e gestão de risco de crédito em Cédulas de Crédito Imobiliário (CCI).")
st.divider()

# Inicializa o session_state
inicializar_session_state()

# Roteador de Página
if st.session_state.pagina_atual == "painel":
    renderizar_painel()
elif st.session_state.pagina_atual == "detalhe":
    renderizar_detalhe_operacao()
else:
    renderizar_analise()

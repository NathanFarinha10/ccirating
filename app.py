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

# ==============================================================================
# CONFIGURAÇÃO DO BANCO DE DADOS (FIRESTORE)
# ==============================================================================

# Coleção que usaremos no Firestore
DB_COLLECTION = "cci_operacoes"

# Valores Padrão para um novo formulário
default_emissao = datetime.date(2024, 5, 1)
default_prazo_meses = 120 # 10 anos
DEFAULTS = {
    'op_nome': 'Nova Operação', 'op_codigo': 'CCI-NEW',
    'op_emissor': 'Banco Exemplo S.A.', 'op_volume': 1000000.0,
    'op_taxa': 10.0, 'op_indexador': 'IPCA +', 'op_prazo': default_prazo_meses,
    'op_amortizacao': 'SAC',
    'op_data_emissao': default_emissao,
    'op_data_vencimento': default_emissao + relativedelta(months=+default_prazo_meses),
    'input_ltv': 75.0, 'input_demanda': 150000,
    'input_behavior_30_60': 0, 'input_behavior_60_90': 0, 'input_behavior_90_mais': 0,
    'input_comprometimento': 20.0,
    'input_inad_30_60': 0, 'input_inad_60_90': 0, 'input_inad_90_mais': 0,
    'justificativa_final': '',
    # Chaves para os resultados
    'scores_operacao': {},
    'rating_final_operacao': {},
}

@st.cache_resource
def get_firestore_client():
    """
    Inicializa o Firebase Admin e retorna o cliente Firestore.
    Usa st.cache_resource para garantir que isso seja executado apenas uma vez.
    """
    try:
        # Tenta carregar as credenciais dos Secrets do Streamlit
        # O Streamlit transforma o seu secrets.toml em um dicionário
        creds_json = dict(st.secrets["firebase_service_account"])
        
        # Verifica se o app já foi inicializado
        if not firebase_admin._apps:
            # CORREÇÃO: Passa o dicionário 'creds_json' diretamente.
            cred_obj = credentials.Certificate(creds_json)
            firebase_admin.initialize_app(cred_obj)
            
        return firestore.client()
    
    except Exception as e:
        st.error("Erro ao conectar ao Firestore. Verifique suas credenciais nos Secrets.")
        st.error(e)
        return None

def carregar_db():
    """Carrega os dados do Firestore."""
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
# INICIALIZAÇÃO E FUNÇÕES AUXILIARES
# ==============================================================================

def inicializar_session_state():
    """Garante que todos os valores de input e scores sejam inicializados no st.session_state apenas uma vez."""
    if 'state_initialized_cci' not in st.session_state:
        st.session_state.state_initialized_cci = True
        
        # Controle de página
        st.session_state.pagina_atual = "painel" # 'painel' ou 'analise'
        st.session_state.operacao_selecionada_id = None
        
        # Inicializa os campos do formulário com os padrões
        limpar_formulario_analise()

def limpar_formulario_analise():
    """Reseta o session_state para os valores padrão de um novo formulário."""
    for key, value in DEFAULTS.items():
        st.session_state[key] = value
    st.session_state.operacao_selecionada_id = None

def coletar_dados_da_sessao():
    """Coleta todos os dados relevantes do st.session_state para salvar."""
    dados = {}
    for key in DEFAULTS.keys():
        if key in st.session_state:
            value = st.session_state[key]
            
            # --- CORREÇÃO: CONVERTE DATE PARA DATETIME ANTES DE SALVAR ---
            # O Firestore espera datetime.datetime, não datetime.date
            if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
                # Converte para datetime adicionando a hora 00:00:00
                dados[key] = datetime.datetime.combine(value, datetime.datetime.min.time())
            else:
                dados[key] = value
            # --- FIM DA CORREÇÃO ---
            
    return dados

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
    if nota == 10: return 'A+'
    elif nota == 8: return 'A'
    elif nota == 6: return 'A-'
    elif nota == 4: return 'B'
    elif nota == 2: return 'C'
    else: return "N/A"

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
        self.cell(0, 10, 'Relatório de Rating de CCI', 0, 0, 'C')
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
        
        # Garante que as datas sejam formatadas corretamente, mesmo se ainda não foram convertidas
        data_emissao = ss.op_data_emissao
        if isinstance(data_emissao, datetime.datetime): data_emissao = data_emissao.date()
        
        data_vencimento = ss.op_data_vencimento
        if isinstance(data_vencimento, datetime.datetime): data_vencimento = data_vencimento.date()

        data = {
            "Nome da Operação:": ss.op_nome, "Código/Série:": ss.op_codigo,
            "Volume Emitido:": f"R$ {ss.op_volume:,.2f}", "Taxa:": f"{ss.op_indexador} {ss.op_taxa}% a.a.",
            "Data de Emissão:": data_emissao.strftime('%d/%m/%Y'), "Vencimento:": data_vencimento.strftime('%d/%m/%Y'),
            "Emissor:": ss.op_emissor, "Sistema Amortização:": ss.op_amortizacao,
        }
        for i, (label, value) in enumerate(data.items()):
            if i > 0 and i % 2 == 0: self.ln(line_height)
            self.set_font('Arial', 'B', 10)
            self.cell(col_width, line_height, self._write_text(label), border=1)
            self.set_font('Arial', '', 10)
            self.cell(col_width, line_height, self._write_text(str(value)), border=1)
        self.ln(line_height)
        self.ln(10)

    def TabelaScorecard(self, ss):
        self.set_font('Arial', 'B', 10)
        line_height = self.font_size * 1.5
        col_widths = [self.epw * 0.4, self.epw * 0.15, self.epw * 0.15, self.epw * 0.15, self.epw * 0.15]
        headers = ["Atributo", "Peso", "Nota (2-10)", "Rating", "Score Ponderado"]
        for i, header in enumerate(headers): self.cell(col_widths[i], line_height, header, border=1, align='C')
        self.ln(line_height)
        
        self.set_font('Arial', '', 10)
        scores = ss.scores_operacao
        nomes_inputs = {
            'ltv': '1. LTV',
            'demanda': '2. Demanda',
            'behavior': '3. Behavior',
            'comprometimento': '4. Comprometimento de Renda',
            'inadimplencia': '5. Inadimplência'
        }
        
        for key, nome in nomes_inputs.items():
            nota = scores.get(key, 2) # Default 2 se não calculado
            rating = converter_nota_para_rating(nota)
            peso = 0.20
            row = [nome, f"{peso*100:.0f}%", f"{nota:.0f}", rating, f"{nota * peso:.2f}"]
            for i, item in enumerate(row): self.cell(col_widths[i], line_height, item, border=1, align='C')
            self.ln(line_height)
        self.ln(10)

def gerar_relatorio_pdf(ss):
    """Gera o PDF com os dados do session_state."""
    try:
        pdf = PDF()
        pdf.add_page()
        pdf.chapter_title('1. Dados Cadastrais da Operação')
        pdf.TabelaCadastro(ss)

        pdf.chapter_title('2. Scorecard e Rating Final')
        pdf.TabelaScorecard(ss)

        resultados = ss.rating_final_operacao
        nota_media = resultados.get('nota_media', 0)
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
        return b''

# ==============================================================================
# FUNÇÕES DE CÁLCULO DE SCORE
# ==============================================================================

def calcular_nota_ltv(ltv):
    ltv_perc = ltv
    if ltv_perc <= 60: return 10
    elif ltv_perc <= 70: return 8
    elif ltv_perc <= 80: return 6
    elif ltv_perc <= 90: return 4
    else: return 2

def calcular_nota_demanda(demanda):
    if demanda > 200000: return 10
    elif demanda >= 100000: return 8
    elif demanda >= 50000: return 6
    elif demanda >= 30000: return 4
    else: return 2

def calcular_nota_behavior(soma_behavior):
    if soma_behavior == 0: return 10
    elif soma_behavior == 2: return 8
    elif soma_behavior == 4: return 6
    elif soma_behavior == 6: return 4
    else: return 2 # > 6

def calcular_nota_comprometimento(comprometimento):
    comp_perc = comprometimento
    if comp_perc < 15: return 10
    elif comp_perc <= 20: return 8
    elif comp_perc <= 25: return 6
    elif comp_perc <= 30: return 4
    else: return 2 # > 30

def calcular_nota_inadimplencia(soma_inad):
    if soma_inad == 0: return 10
    elif soma_inad <= 4: return 8 # 0-4 (mas 0 já foi pego, então 1-4)
    elif soma_inad <= 6: return 6 # 4-6 (mas >4, então 5-6)
    elif soma_inad <= 8: return 4 # 6-8 (mas >6, então 7-8)
    else: return 2 # > 8

def calcular_rating():
    """Função principal que calcula todas as notas e o rating final."""
    
    # 1. Calcular Somas de Penalização
    soma_behavior = (st.session_state.input_behavior_30_60 * 2) + \
                    (st.session_state.input_behavior_60_90 * 4) + \
                    (st.session_state.input_behavior_90_mais * 6)
    
    soma_inad = (st.session_state.input_inad_30_60 * 2) + \
                (st.session_state.input_inad_60_90 * 4) + \
                (st.session_state.input_inad_90_mais * 6)

    # 2. Calcular Notas Individuais
    nota_ltv = calcular_nota_ltv(st.session_state.input_ltv)
    nota_demanda = calcular_nota_demanda(st.session_state.input_demanda)
    nota_behavior = calcular_nota_behavior(soma_behavior)
    nota_comp = calcular_nota_comprometimento(st.session_state.input_comprometimento)
    nota_inad = calcular_nota_inadimplencia(soma_inad)

    # 3. Armazenar notas individuais
    st.session_state.scores_operacao = {
        'ltv': nota_ltv,
        'demanda': nota_demanda,
        'behavior': nota_behavior,
        'comprometimento': nota_comp,
        'inadimplencia': nota_inad,
        'soma_behavior': soma_behavior, # Salva para referência
        'soma_inad': soma_inad          # Salva para referência
    }

    # 4. Calcular Média Ponderada (20% cada)
    lista_notas = [nota_ltv, nota_demanda, nota_behavior, nota_comp, nota_inad]
    nota_media = np.mean(lista_notas) # Média simples é igual a ponderada de 20%
    
    # 5. Mapear nota média para a nota final (10, 8, 6, 4, 2)
    possible_scores = np.array([2, 4, 6, 8, 10])
    idx = np.abs(possible_scores - nota_media).argmin()
    nota_final_arredondada = possible_scores[idx]
    
    rating_final = converter_nota_para_rating(nota_final_arredondada)

    # 6. Armazenar resultado final
    st.session_state.rating_final_operacao = {
        'nota_media': nota_media,
        'nota_final': nota_final_arredondada,
        'rating_final': rating_final
    }

# ==============================================================================
# CALLBACKS DE NAVEGAÇÃO E AÇÕES
# ==============================================================================

def callback_voltar_painel():
    """Volta para o painel e limpa o formulário."""
    st.session_state.pagina_atual = "painel"
    limpar_formulario_analise()

def callback_nova_operacao():
    """Prepara o estado para cadastrar uma nova operação."""
    limpar_formulario_analise() # Garante que o formulário esteja limpo
    st.session_state.operacao_selecionada_id = str(uuid.uuid4()) # Gera um novo ID
    st.session_state.pagina_atual = "analise"

def callback_selecionar_operacao(op_id, op_data):
    """Carrega os dados de uma operação existente no session_state para análise."""
    st.session_state.pagina_atual = "analise"
    st.session_state.operacao_selecionada_id = op_id
    
    # Limpa o formulário antes de carregar
    limpar_formulario_analise() 

    # Carrega todos os dados do banco para o session_state
    for key, value in op_data.items():
        
        # --- CORREÇÃO: CONVERTE DATETIME (DO FIRESTORE) PARA DATE (PARA O WIDGET) ---
        # O Firestore retorna datetime.datetime, mas o st.date_input precisa de datetime.date
        if key in ['op_data_emissao', 'op_data_vencimento'] and isinstance(value, datetime.datetime):
            st.session_state[key] = value.date() # Pega apenas a parte da data
        # --- FIM DA CORREÇÃO ---
            
        else:
            st.session_state[key] = value

def callback_deletar_operacao(op_id):
    """Deleta uma operação do banco de dados Firestore."""
    db = get_firestore_client()
    if db is None:
        return
        
    try:
        db.collection(DB_COLLECTION).document(op_id).delete()
        st.toast(f"Operação {op_id} deletada.", icon="🗑️")
    except Exception as e:
        st.error(f"Erro ao deletar operação: {e}")

def callback_calcular_e_salvar():
    """Calcula o rating e salva a operação no banco de dados Firestore."""
    if not st.session_state.operacao_selecionada_id:
        st.error("Erro: ID da operação não definido. Tente novamente.")
        return
        
    # 1. Calcula os scores
    calcular_rating() 
    
    # 2. Coleta dados da sessão (já convertendo datas)
    dados_para_salvar = coletar_dados_da_sessao()
    
    # 3. Carrega o DB, atualiza e salva
    db = get_firestore_client()
    if db is None:
        return
        
    try:
        op_id = st.session_state.operacao_selecionada_id
        db.collection(DB_COLLECTION).document(op_id).set(dados_para_salvar)
        st.success(f"Operação '{st.session_state.op_nome}' salva com sucesso!")
    except Exception as e:
        st.error(f"Erro ao salvar no Firestore: {e}")

# ==============================================================================
# RENDERIZAÇÃO DAS PÁGINAS
# ==============================================================================

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

    st.subheader(f"Operações Cadastradas ({len(db_data)})")
    
    # Define as colunas do painel
    col1, col2, col3, col4, col5 = st.columns([3, 2, 1, 1, 1])
    col1.markdown("**Nome da Operação**")
    col2.markdown("**Código**")
    col3.markdown("**Rating Final**")
    col4.markdown("**Ação**")
    col5.markdown("**Excluir**")

    # Itera e exibe cada operação
    for op_id, op_data in db_data.items():
        op_nome = op_data.get('op_nome', 'Sem Nome')
        op_codigo = op_data.get('op_codigo', 'N/A')
        
        # Pega o rating final calculado, se existir
        rating_info = op_data.get('rating_final_operacao', {})
        rating_final = rating_info.get('rating_final', 'N/A')
        
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
            
    st.divider(key="bottom_divider_painel")


def renderizar_analise():
    """Renderiza a página de análise (abas de cadastro, inputs, resultado)."""
    
    if st.button("⬅️ Voltar ao Painel"):
        callback_voltar_painel()
        st.rerun() # Força o rerender para a página do painel

    # Título da página de análise
    if st.session_state.op_nome == DEFAULTS['op_nome']:
        st.header("Cadastrar Nova Operação")
    else:
        st.header(f"Analisando: {st.session_state.op_nome}")
    
    # --- DEFINIÇÃO DAS ABAS ---
    tab0, tab_inputs, tab_res, tab_met = st.tabs([
        "Cadastro", "Inputs do Rating", "Resultado", "Metodologia"
    ])

    with tab0:
        st.header("Informações Gerais da Operação")
        col1, col2 = st.columns(2)
        with col1:
            st.text_input("Nome/Identificação da CCI:", key='op_nome')
            st.number_input("Volume da Operação (R$):", key='op_volume', format="%.2f")
            st.selectbox("Sistema de Amortização:", ["SAC", "Price"], key='op_amortizacao')
            st.date_input("Data de Emissão:", key='op_data_emissao')
        with col2:
            st.text_input("Código/Série:", key='op_codigo')
            c1_taxa, c2_taxa = st.columns([1, 2])
            with c1_taxa: st.selectbox("Indexador:", ["IPCA +", "CDI +", "Pré-fixado"], key='op_indexador')
            with c2_taxa: st.number_input("Taxa (% a.a.):", key='op_taxa', format="%.2f")
            st.number_input("Prazo Remanescente (meses):", key='op_prazo', step=1)
            st.date_input(
                "Data de Vencimento:",
                key='op_data_vencimento',
                min_value=st.session_state.op_data_emissao
            )
        st.text_input("Emissor da CCI (Ex: Banco, Securitizadora):", key='op_emissor')

    with tab_inputs:
        st.header("Inputs para o Rating")
        st.markdown("Preencha os 5 atributos abaixo para gerar o score.")

        col1, col2 = st.columns(2)
        
        with col1:
            with st.container(border=True):
                st.subheader("1. LTV (Loan-to-Value)")
                st.number_input("LTV da operação (%)", key='input_ltv', min_value=0.0, max_value=200.0, step=1.0, format="%.2f")
                st.caption("<=60%: 10 | 60-70: 8 | 70-80: 6 | 80-90: 4 | 90+: 2")

            with st.container(border=True):
                st.subheader("2. Demanda")
                st.number_input("Valor da Demanda (Ex: R$)", key='input_demanda', min_value=0, step=1000)
                st.caption(">200k: 10 | 100k-200k: 8 | 50k-100k: 6 | 30k-50k: 4 | <30k: 2")

            with st.container(border=True):
                st.subheader("3. Behavior (Penalização)")
                st.number_input("Qtd. Atrasos 30-60 dias", key='input_behavior_30_60', min_value=0, step=1)
                st.number_input("Qtd. Atrasos 60-90 dias", key='input_behavior_60_90', min_value=0, step=1)
                st.number_input("Qtd. Atrasos >90 dias", key='input_behavior_90_mais', min_value=0, step=1)
                st.caption("Penalização: 30-60 (2pts), 60-90 (4pts), >90 (6pts)")
                st.caption("Soma 0: 10 | Soma 2: 8 | Soma 4: 6 | Soma 6: 4 | Soma >6: 2")

        with col2:
            with st.container(border=True):
                st.subheader("4. Comprometimento de Renda")
                st.number_input("Comprometimento de Renda (%)", key='input_comprometimento', min_value=0.0, max_value=100.0, step=0.5, format="%.2f")
                st.caption("<15%: 10 | 15-20%: 8 | 20-25%: 6 | 25-30%: 4 | >30%: 2")

            with st.container(border=True):
                st.subheader("5. Inadimplência (Penalização)")
                st.number_input("Qtd. Inad. 30-60 dias", key='input_inad_30_60', min_value=0, step=1)
                st.number_input("Qtd. Inad. 60-90 dias", key='input_inad_60_90', min_value=0, step=1)
                st.number_input("Qtd. Inad. >90 dias", key='input_inad_90_mais', min_value=0, step=1)
                st.caption("Penalização: 30-60 (2pts), 60-90 (4pts), >90 (6pts)")
                st.caption("Soma 0: 10 | Soma 1-4: 8 | Soma 5-6: 6 | Soma 7-8: 4 | Soma >8: 2")
                st.caption("(Nota: Lógica de soma igual ao Behavior, mas faixas de nota diferentes)")
        
        st.divider()
        if st.button("Calcular e Salvar Rating", use_container_width=True, type="primary", on_click=callback_calcular_e_salvar):
            # Ação já está no callback
            st.success("Rating calculado e salvo! Veja a aba 'Resultado'.")

    with tab_res:
        st.header("Resultado Final e Atribuição de Rating")
        
        if not st.session_state.scores_operacao:
            st.warning("⬅️ Preencha os dados na aba 'Inputs do Rating' e clique em 'Calcular e Salvar Rating'.")
        else:
            scores = st.session_state.scores_operacao
            resultados = st.session_state.rating_final_operacao
            
            st.subheader("Scorecard Mestre")
            
            nomes_inputs = {
                'ltv': '1. LTV', 'demanda': '2. Demanda', 'behavior': '3. Behavior',
                'comprometimento': '4. Comprometimento de Renda', 'inadimplencia': '5. Inadimplência'
            }
            data = []
            for key, nome in nomes_inputs.items():
                nota = scores.get(key, 2)
                rating_input = converter_nota_para_rating(nota)
                peso = 0.20
                data.append({
                    'Atributo': nome, 'Peso': f"{peso*100:.0f}%", 'Nota (2-10)': nota,
                    'Rating': rating_input, 'Score Ponderado': f"{nota * peso:.2f}"
                })
            
            df_scores = pd.DataFrame(data).set_index('Atributo')
            st.table(df_scores)
            st.divider()
            
            nota_media = resultados.get('nota_media', 0)
            nota_final = resultados.get('nota_final', 0)
            rating_final = resultados.get('rating_final', 'N/A')
            
            st.subheader("Resultado Final Ponderado")
            col_gauge, col_metrics = st.columns([2, 1])
            
            with col_gauge:
                st.plotly_chart(create_gauge_chart(nota_media, "Score Médio Ponderado"), use_container_width=True)
                
            with col_metrics:
                st.metric("Score Médio (0-10)", f"{nota_media:.2f}")
                st.metric("Nota Final (Mais Próxima)", f"{nota_final:.0f}")
                st.metric("Rating Final Atribuído", rating_final)
            
            st.info(f"Somas de Penalização (Referência): Behavior = {scores.get('soma_behavior', 0)}, Inadimplência = {scores.get('soma_inad', 0)}")
            st.divider()
            st.text_area("Justificativa e comentários finais (opcional):", height=100, key='justificativa_final')
            st.divider()

            st.subheader("⬇️ Download do Relatório")
            pdf_data = gerar_relatorio_pdf(st.session_state)
            st.download_button(
                label="Baixar Relatório em PDF", data=pdf_data,
                file_name=f"Relatorio_CCI_{st.session_state.op_nome.replace(' ', '_')}.pdf",
                mime="application/pdf", use_container_width=True
            )

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
else:
    renderizar_analise()

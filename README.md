# Insight Invest

> Uma aplicação que transforma dados em decisões financeiras inteligentes.

Plataforma web de apoio à decisão em investimentos que integra dados **fundamentalistas**, **estatísticos** e **macroeconômicos** do mercado brasileiro para gerar carteiras teóricas personalizadas por perfil de risco.

Projeto desenvolvido como Trabalho de Conclusão de Curso (TCC) em Ciência de Dados para Negócios — UFPB, por Maria Carolina.

---

## Sumário

- [Sobre o projeto](#sobre-o-projeto)
- [Arquitetura](#arquitetura)
- [Stack tecnológica](#stack-tecnológica)
- [Estrutura do repositório](#estrutura-do-repositório)
- [Como rodar localmente](#como-rodar-localmente)
- [Comandos de gerenciamento](#comandos-de-gerenciamento)
- [Fontes de dados](#fontes-de-dados)
- [Indicadores calculados (KPIs)](#indicadores-calculados-kpis)
- [Geração de carteiras](#geração-de-carteiras)
- [Estado atual do desenvolvimento](#estado-atual-do-desenvolvimento)
- [Roteiro (TCC II)](#roteiro-tcc-ii)
- [Limitações conhecidas](#limitações-conhecidas)
- [Licença](#licença)

---

## Sobre o projeto

O mercado financeiro oferece diversas metodologias de análise de investimentos, mas essas abordagens costumam estar fragmentadas entre múltiplas fontes de dados e ferramentas — o que torna a tomada de decisão mais lenta e menos fundamentada para grande parte dos investidores.

O **Insight Invest** centraliza essas informações em uma única plataforma, combinando:

- **Análise fundamentalista** — indicadores extraídos das demonstrações financeiras das empresas (CVM);
- **Análise estatística** — comportamento histórico de preços e retorno dos ativos (Yahoo Finance);
- **Análise macroeconômica** — indicadores do cenário econômico brasileiro (Banco Central);

e aplica modelos quantitativos (Markowitz, Máximo Sharpe, Paridade de Risco) para gerar recomendações de carteira compatíveis com o perfil de risco do investidor.

## Arquitetura

```
[Fontes de Dados Externas]
    |
    |---> B3 (cadastro de ativos e tickers)
    |---> CVM (demonstrações financeiras)
    |---> Banco Central (indicadores macroeconômicos)
    |---> Yahoo Finance (séries históricas de preços)
    |
    v
[Módulo de Coleta] --> [Tratamento e Construção de KPIs] --> [Banco de Dados PostgreSQL]
    |
    v
[Motor de Scoring e Otimização]
    |
    |---> Carteira pré-calculada (usuário não autenticado, via cache)
    |---> Carteira interativa (usuário autenticado, otimização em tempo real)
    |
    v
[Interface Web — Django]
```

A aplicação segue o padrão **Model-View-Template (MVT)** do Django, com quatro módulos de coleta independentes (um por fonte de dados), uma camada de KPIs, um motor de *scoring* que combina os indicadores em um score ponderado por perfil de risco, e um mecanismo de otimização de carteira que escolhe entre diferentes modelos quantitativos conforme o apetite de risco/retorno do investidor.

## Stack tecnológica

| Camada | Tecnologia |
|---|---|
| Backend / Framework | Python 3.x + Django |
| Banco de dados | PostgreSQL |
| Coleta de dados | `requests`, `yfinance` |
| Processamento | `pandas`, `numpy` |
| Otimização de carteira | `scipy.optimize` (Markowitz, fronteira eficiente) |
| Estatística | `scipy.stats` |
| Frontend | Django Templates, HTML/CSS/JS |

## Estrutura do repositório

```
INSIGHT_INVEST/
├── analysis/                    # Módulos relacionados às análises dos ativos
├── billing/                     # Planos e regras de acesso
├── content/                     # Conteúdos apresentados na aplicação
├── core/                        # Configurações principais e funcionalidades centrais
├── data/                        # Dados e arquivos utilizados pela aplicação
├── interaction/                 # Interação e funcionalidades relacionadas ao usuário
├── market_data/                 # Dados e indicadores do mercado financeiro
├── ml_engine/                   # Componentes relacionados a modelos e análises de ML
├── portfolio/                   # Carteiras, posições e ativos
├── scoring/                     # Cálculo de scores e classificação dos ativos
├── services/                    # Serviços e módulos de extração e processamento
├── users/                       # Autenticação e gerenciamento de usuários
│
├── manage.py                    # Gerenciador do projeto Django
├── db.sqlite3                   # Banco de dados local utilizado no desenvolvimento
├── requirements.txt             # Dependências do projeto
├── verificar_setup.py           # Verificação da configuração do ambiente
├── diagnostico.py               # Rotinas de diagnóstico
├── corrigir_b3_extractor.py     # Script auxiliar para ajustes no extrator da B3
├── corrigir_calcular_score.py   # Script auxiliar para ajustes no cálculo do score
├── corrigir_tickers.py          # Script auxiliar para correção/mapeamento de tickers
├── LICENSE                      # Licença do projeto
└── README.md                    # Documentação do projeto
```

## Como rodar localmente

### Pré-requisitos

- Python 3.11+
- PostgreSQL 14+
- pip / virtualenv

### Passo a passo

```bash
# 1. Clone o repositório
git clone <url-do-repositorio>
cd insight_invest

# 2. Crie e ative o ambiente virtual
python -m venv venv
venv\Scripts\activate        # Windows
source venv/bin/activate     # Linux/macOS

# 3. Instale as dependências
pip install -r requirements.txt

# 4. Configure as variáveis de ambiente (.env)
cp .env.example .env
# edite .env com as credenciais do seu Postgres local

# 5. Crie o banco de dados
createdb insightdb

# 6. Aplique as migrations
python manage.py migrate

# 7. Popule a base de ativos (B3 + CVM)
python manage.py shell
>>> from services.extractors.b3_extractor import construir_mapa_ticker_cnpj
>>> construir_mapa_ticker_cnpj()

# 8. Gere as carteiras recomendadas iniciais
python manage.py atualizar_carteiras

# 9. Rode o servidor
python manage.py runserver
```

Acesse em `http://localhost:8000`.

> **Nota:** os passos 7 e 8 fazem requisições a APIs externas (B3, CVM) e podem levar de alguns minutos até a maior parte de uma hora, dependendo da estabilidade da API da B3. Os módulos de extração têm checkpoint automático — se o processo for interrompido, rodar de novo retoma de onde parou.

## Comandos de gerenciamento

| Comando | Descrição |
|---|---|
| `python manage.py migrate` | Aplica as migrations pendentes |
| `python manage.py atualizar_carteiras` | Recalcula e armazena em cache as carteiras recomendadas por perfil de risco |
| `python manage.py createsuperuser` | Cria um usuário administrador para o Django Admin |

## Fontes de dados

| Fonte | Natureza do dado | Formato de acesso | Frequência |
|---|---|---|---|
| **B3** | Cadastral (tickers, empresas) | API JSON (`GetInitialCompanies` / `GetDetail`) | Contínua |
| **CVM** | Contábil (Balanço Patrimonial, DRE) | Arquivo CSV compactado (DFP anual) | Trimestral/anual |
| **Banco Central (BCB)** | Macroeconômico | API JSON (SGS) | Diária/mensal |
| **Yahoo Finance** | Preços e volume | Biblioteca `yfinance` | Diária |

O cruzamento entre as fontes é feito pelo código CVM (B3 ↔ CVM) e pelo CNPJ normalizado (CVM ↔ base interna de ativos).

## Indicadores calculados (KPIs)

**Fundamentalistas** (a partir do BP e DRE da CVM): Liquidez Corrente/Seca/Imediata/Geral, Dívida Bruta/Líquida, Dívida Líquida/EBITDA, Dívida/Patrimônio Líquido, ROE, ROA, Margem Bruta/EBITDA/Líquida, Giro do Ativo, Crescimento de Receita e Lucro, P/L, P/VP, EV/EBITDA, Dividend Yield.

**Estatísticos** (a partir de séries de preços do Yahoo Finance): retorno médio, retornos em janelas de 1/3/12 meses, volatilidade anualizada, beta em relação ao Ibovespa, matriz de correlação, assimetria (*skewness*), curtose, RSI, médias móveis.

**Macroeconômicos** (a partir do Banco Central): taxa Selic (implementado); IPCA, IGP-M, IBC-Br, PIB trimestral, exportações/importações e taxa de desemprego (já extraídos, incorporação aos KPIs prevista para o TCC II).

## Geração de carteiras

O sistema oferece dois fluxos de geração de carteira:

1. **Carteira pré-calculada** (usuário não autenticado) — calculada em lote pelo comando `atualizar_carteiras`, armazenada em cache e exibida na home como demonstração do potencial analítico da plataforma.
2. **Carteira interativa** (usuário autenticado) — recalculada em tempo real conforme os filtros de risco/retorno escolhidos pelo usuário, alternando entre quatro modelos de otimização:

| Risco \ Retorno | Baixo | Médio | Alto |
|---|---|---|---|
| **Baixo** | Paridade de Risco | Markowitz | Markowitz |
| **Médio** | Markowitz | Máximo Sharpe | Máximo Sharpe |
| **Alto** | Máximo Sharpe | Máximo Sharpe | Alocação por Score |

## Estado atual do desenvolvimento

✅ Módulos de coleta das quatro fontes (B3, CVM, BCB, Yahoo Finance)
✅ Mapeamento ticker ↔ CNPJ
✅ Cálculo de KPIs fundamentalistas e estatísticos
✅ Motor de scoring por perfil de risco
✅ Otimização de carteira (Markowitz, Sharpe, Risk Parity, Score)
✅ Geração de carteira pré-calculada (home) e interativa (usuário autenticado)
🚧 Módulo de *suitability* (questionário de perfil de risco)
🚧 Expansão dos KPIs macroeconômicos (além da Selic)
🚧 Modelo GJR-GARCH para previsão de volatilidade
🚧 Backtest e validação empírica das carteiras recomendadas

## Roteiro (TCC II)

1. **Módulo de Suitability** — questionário de perfil de risco alinhado à Resolução CVM nº 30/2021.
2. **Expansão dos KPIs Macroeconômicos** — incorporar IPCA, PIB, câmbio e balança comercial.
3. **Modelos de Previsão de Volatilidade** — implementar GJR-GARCH em substituição à volatilidade histórica.
4. **Avaliação e Validação das Carteiras** — backtest comparando a carteira combinada com carteiras isoladas por camada, frente ao Ibovespa.
5. **Refinamento de Interface e Visualizações**.
6. **Testes e consolidação do sistema.**

A análise de sentimento de mercado (notícias, redes sociais) está fora do escopo do TCC I/II e permanece como extensão prevista para trabalhos futuros.

## Limitações conhecidas

- A API da B3 (`GetDetail`) não é documentada oficialmente e pode apresentar instabilidade/timeouts durante extrações em massa — os extractors implementam retry automático e checkpoint.
- Nem todos os ativos mapeados possuem histórico de preços suficiente no Yahoo Finance (ativos deslistados, IPOs recentes), o que pode limitar o universo elegível para otimização de Markowitz em alguns perfis.
- A camada de KPIs macroeconômicos está restrita à taxa Selic nesta etapa.
- A previsão de volatilidade ainda utiliza volatilidade histórica realizada como proxy, não um modelo condicional (GARCH).

## Licença

Projeto acadêmico desenvolvido para fins de Trabalho de Conclusão de Curso. Uso e distribuição sujeitos à definição do autor.
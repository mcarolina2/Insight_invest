"""
Extrator de sentimento do mercado financeiro.

Fluxo:
  1. Scraping de manchetes (InfoMoney, Valor Econômico, Reuters Brasil)
  2. Busca de posts via Reddit API (r/investimentos, r/BrasilFinancas)
  3. Análise de sentimento com modelo NLP (FinBERT-PT-BR ou fallback VADER)
  4. Persistência em SentimentoMercado com score entre -1 e +1

Instalação:
  pip install requests beautifulsoup4 transformers torch praw

Configuração (variáveis de ambiente):
  REDDIT_CLIENT_ID      → app registrado em reddit.com/prefs/apps
  REDDIT_CLIENT_SECRET  → secret do app Reddit
  REDDIT_USER_AGENT     → ex: "InsightInvest/1.0 by u/seu_usuario"

Modelos NLP disponíveis (em ordem de preferência):
  1. lucas-leme/FinBERT-PT-BR   (português, domínio financeiro) ← ideal
  2. neuralmind/bert-base-portuguese-cased (português genérico)
  3. ProsusAI/finbert            (inglês, domínio financeiro)   ← fallback
"""

import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configurações de scraping
# ---------------------------------------------------------------------------

HEADERS_SCRAPING = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Accept": "text/html,application/xhtml+xml",
}

DELAY_ENTRE_REQUESTS = 2  # segundos — respeita rate limit dos sites

# Tickers mais comuns: ajuste conforme os ativos cadastrados
TICKERS_MONITORADOS = [
    "PETR4", "VALE3", "ITUB4", "BBDC4", "ABEV3",
    "WEGE3", "MGLU3", "B3SA3", "BBAS3", "RENT3",
]

# ---------------------------------------------------------------------------
# Estrutura de dados de uma manchete/post coletado
# ---------------------------------------------------------------------------

@dataclass
class NoticiaColetada:
    titulo:    str
    fonte:     str
    url:       str
    data:      date
    ticker:    Optional[str] = None  # None = sentimento geral do mercado
    texto:     Optional[str] = None  # corpo completo (quando disponível)


# ---------------------------------------------------------------------------
# SCRAPERS POR FONTE
# ---------------------------------------------------------------------------

class InfoMoneyScraper:
    """
    Scraper do InfoMoney — maior portal de finanças do Brasil.
    Coleta manchetes da seção de mercados e busca por ticker.
    """
    BASE_URL   = "https://www.infomoney.com.br"
    MERCADOS   = "https://www.infomoney.com.br/mercados/"
    BUSCA_URL  = "https://www.infomoney.com.br/?s={ticker}"

    def coletar_manchetes_gerais(self, limite: int = 20) -> list[NoticiaColetada]:
        """Coleta manchetes gerais da seção de mercados."""
        noticias = []
        try:
            resp = requests.get(self.MERCADOS, headers=HEADERS_SCRAPING, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            # InfoMoney usa <article> ou <div class="card-news"> para cada manchete
            artigos = soup.find_all("article", limit=limite)
            if not artigos:
                artigos = soup.find_all("div", class_=re.compile(r"card|news|article"), limit=limite)

            for artigo in artigos:
                titulo_tag = artigo.find(["h2", "h3", "h4"])
                link_tag   = artigo.find("a", href=True)

                if not titulo_tag or not link_tag:
                    continue

                titulo = titulo_tag.get_text(strip=True)
                url    = link_tag["href"]
                if not url.startswith("http"):
                    url = self.BASE_URL + url

                noticias.append(NoticiaColetada(
                    titulo=titulo,
                    fonte="infomoney",
                    url=url,
                    data=date.today(),
                ))

            logger.info(f"InfoMoney: {len(noticias)} manchetes coletadas")

        except Exception as e:
            logger.warning(f"InfoMoney scraper falhou: {e}")

        return noticias

    def buscar_por_ticker(self, ticker: str, limite: int = 10) -> list[NoticiaColetada]:
        """Busca notícias específicas de um ticker."""
        noticias = []
        try:
            url  = self.BUSCA_URL.format(ticker=ticker)
            resp = requests.get(url, headers=HEADERS_SCRAPING, timeout=15)
            soup = BeautifulSoup(resp.text, "html.parser")

            artigos = soup.find_all("article", limit=limite)
            for artigo in artigos:
                titulo_tag = artigo.find(["h2", "h3", "h4"])
                link_tag   = artigo.find("a", href=True)
                if not titulo_tag or not link_tag:
                    continue

                link = link_tag["href"]
                if not link.startswith("http"):
                    link = self.BASE_URL + link

                noticias.append(NoticiaColetada(
                    titulo=titulo_tag.get_text(strip=True),
                    fonte="infomoney",
                    url=link,
                    data=date.today(),
                    ticker=ticker,
                ))

            time.sleep(DELAY_ENTRE_REQUESTS)

        except Exception as e:
            logger.warning(f"InfoMoney busca {ticker} falhou: {e}")

        return noticias


class ValorEconomicoScraper:
    """
    Coleta manchetes do Valor Econômico (reuters.com/brasil como alternativa
    gratuita, já que Valor tem paywall).
    """
    REUTERS_BR = "https://br.reuters.com/business/markets"

    def coletar_manchetes(self, limite: int = 20) -> list[NoticiaColetada]:
        noticias = []
        try:
            resp = requests.get(self.REUTERS_BR, headers=HEADERS_SCRAPING, timeout=15)
            soup = BeautifulSoup(resp.text, "html.parser")

            # Reuters BR usa <a data-testid="Heading"> para manchetes
            links = soup.find_all("a", {"data-testid": re.compile(r"[Hh]eading")}, limit=limite)

            for link in links:
                texto = link.get_text(strip=True)
                href  = link.get("href", "")
                if not href.startswith("http"):
                    href = "https://br.reuters.com" + href

                if len(texto) > 15:  # filtra labels de navegação
                    noticias.append(NoticiaColetada(
                        titulo=texto,
                        fonte="reuters_br",
                        url=href,
                        data=date.today(),
                    ))

            logger.info(f"Reuters BR: {len(noticias)} manchetes")

        except Exception as e:
            logger.warning(f"Reuters BR scraper falhou: {e}")

        return noticias


class RedditScraper:
    """
    Coleta posts dos subreddits de finanças brasileiras via Reddit API.
    Requer app registrado em reddit.com/prefs/apps (gratuito).

    Subreddits relevantes:
      r/investimentos        → maior comunidade de investimentos BR
      r/BrasilFinancas       → discussões gerais de finanças
      r/acoesbrasil          → foco em ações da B3
    """
    TOKEN_URL     = "https://www.reddit.com/api/v1/access_token"
    API_URL       = "https://oauth.reddit.com"
    SUBREDDITS_BR = ["investimentos", "BrasilFinancas", "acoesbrasil"]

    def __init__(self):
        self.client_id     = os.getenv("REDDIT_CLIENT_ID")
        self.client_secret = os.getenv("REDDIT_CLIENT_SECRET")
        self.user_agent    = os.getenv("REDDIT_USER_AGENT", "InsightInvest/1.0")
        self._token        = None

    def _obter_token(self) -> Optional[str]:
        """Autenticação OAuth2 Reddit (client credentials)."""
        if not self.client_id or not self.client_secret:
            logger.warning("Reddit: REDDIT_CLIENT_ID e REDDIT_CLIENT_SECRET não configurados.")
            return None

        resp = requests.post(
            self.TOKEN_URL,
            auth=(self.client_id, self.client_secret),
            data={"grant_type": "client_credentials"},
            headers={"User-Agent": self.user_agent},
            timeout=10,
        )
        resp.raise_for_status()
        self._token = resp.json()["access_token"]
        return self._token

    def _headers(self) -> dict:
        if not self._token:
            self._obter_token()
        return {
            "Authorization": f"Bearer {self._token}",
            "User-Agent": self.user_agent,
        }

    def coletar_posts(
        self,
        subreddit: str = "investimentos",
        limite: int = 25,
        ordenacao: str = "hot",  # hot | new | top
    ) -> list[NoticiaColetada]:
        """Coleta posts recentes de um subreddit."""
        noticias = []
        try:
            url  = f"{self.API_URL}/r/{subreddit}/{ordenacao}?limit={limite}"
            resp = requests.get(url, headers=self._headers(), timeout=15)
            resp.raise_for_status()

            posts = resp.json()["data"]["children"]
            for post in posts:
                dados  = post["data"]
                titulo = dados.get("title", "")
                texto  = dados.get("selftext", "")[:500]  # limita corpo

                # Identifica se o post menciona algum ticker monitorado
                ticker_mencionado = None
                titulo_upper = titulo.upper()
                for t in TICKERS_MONITORADOS:
                    if t in titulo_upper or t in texto.upper():
                        ticker_mencionado = t
                        break

                ts = dados.get("created_utc", 0)
                data_post = datetime.utcfromtimestamp(ts).date() if ts else date.today()

                noticias.append(NoticiaColetada(
                    titulo=titulo,
                    fonte="reddit",
                    url=f"https://reddit.com{dados.get('permalink', '')}",
                    data=data_post,
                    ticker=ticker_mencionado,
                    texto=texto,
                ))

            time.sleep(DELAY_ENTRE_REQUESTS)
            logger.info(f"Reddit r/{subreddit}: {len(noticias)} posts")

        except Exception as e:
            logger.warning(f"Reddit scraper r/{subreddit} falhou: {e}")

        return noticias

    def coletar_todos_subreddits(self, limite_por_sub: int = 25) -> list[NoticiaColetada]:
        """Coleta de todos os subreddits configurados."""
        todas = []
        for sub in self.SUBREDDITS_BR:
            todas.extend(self.coletar_posts(sub, limite=limite_por_sub))
        return todas


# ---------------------------------------------------------------------------
# ANÁLISE DE SENTIMENTO (NLP)
# ---------------------------------------------------------------------------

class AnalisadorSentimento:
    """
    Analisa o sentimento de textos financeiros usando FinBERT.

    Modelo preferido: lucas-leme/FinBERT-PT-BR
      → Treinado em textos financeiros brasileiros
      → Labels: POSITIVE, NEGATIVE, NEUTRAL
      → Score de confiança por label

    Fallback: análise por palavras-chave (sem GPU necessária)
    """

    MODELO_PADRAO = "lucas-leme/FinBERT-PT-BR"

    def __init__(self, usar_modelo: bool = True):
        self.pipeline = None

        if usar_modelo:
            try:
                from transformers import pipeline
                self.pipeline = pipeline(
                    "text-classification",
                    model=self.MODELO_PADRAO,
                    return_all_scores=True,
                    truncation=True,
                    max_length=512,
                )
                logger.info(f"Modelo NLP carregado: {self.MODELO_PADRAO}")
            except Exception as e:
                logger.warning(f"FinBERT não disponível, usando fallback: {e}")

    def analisar(self, texto: str) -> dict:
        """
        Analisa o sentimento de um texto.

        Returns:
            dict com:
              score_sentimento: float entre -1.0 (negativo) e +1.0 (positivo)
              label:            'POSITIVE' | 'NEGATIVE' | 'NEUTRAL'
              confianca:        float entre 0 e 1
        """
        if not texto or len(texto.strip()) < 5:
            return {"score_sentimento": 0.0, "label": "NEUTRAL", "confianca": 0.0}

        if self.pipeline:
            return self._analisar_com_modelo(texto)
        else:
            return self._analisar_por_palavras(texto)

    def _analisar_com_modelo(self, texto: str) -> dict:
        """Análise usando FinBERT-PT-BR."""
        try:
            resultados = self.pipeline(texto[:512])[0]

            # resultados = [{"label": "POSITIVE", "score": 0.87}, ...]
            por_label = {r["label"].upper(): r["score"] for r in resultados}

            score_positivo = por_label.get("POSITIVE", 0)
            score_negativo = por_label.get("NEGATIVE", 0)
            score_neutro   = por_label.get("NEUTRAL", 0)

            # Converte para escala -1 a +1
            score_final = score_positivo - score_negativo

            label_dominante = max(por_label, key=por_label.get)
            confianca = por_label[label_dominante]

            return {
                "score_sentimento": round(score_final, 4),
                "label":            label_dominante,
                "confianca":        round(confianca, 4),
            }

        except Exception as e:
            logger.warning(f"Erro na análise NLP: {e}")
            return self._analisar_por_palavras(texto)

    def _analisar_por_palavras(self, texto: str) -> dict:
        """
        Fallback léxico: dicionário de palavras positivas e negativas.
        Menos preciso que o modelo, mas funciona sem GPU.
        """
        POSITIVAS = {
            "alta", "subiu", "subindo", "valorização", "valorizou", "lucro",
            "crescimento", "recorde", "dividendo", "supera", "forte",
            "otimismo", "recuperação", "compra", "recomendação de compra",
            "resultado positivo", "acima do esperado", "bom desempenho",
            "expansão", "contratação", "aprovado", "ganho",
        }
        NEGATIVAS = {
            "queda", "caiu", "caindo", "desvalorização", "prejuízo",
            "perda", "crise", "risco", "rebaixamento", "vende", "vender",
            "abaixo do esperado", "decepcionou", "fraco", "pessimismo",
            "default", "calote", "investigação", "multa", "déficit",
            "demissão", "corte", "redução", "cancelamento", "negativo",
        }

        texto_lower = texto.lower()
        pos = sum(1 for p in POSITIVAS if p in texto_lower)
        neg = sum(1 for p in NEGATIVAS if p in texto_lower)
        total = pos + neg

        if total == 0:
            return {"score_sentimento": 0.0, "label": "NEUTRAL", "confianca": 0.3}

        score = (pos - neg) / total
        label = "POSITIVE" if score > 0.1 else ("NEGATIVE" if score < -0.1 else "NEUTRAL")

        return {
            "score_sentimento": round(score, 4),
            "label":            label,
            "confianca":        round(abs(score), 4),
        }

    def analisar_lote(self, textos: list[str]) -> list[dict]:
        """Analisa uma lista de textos em lote (mais eficiente com GPU)."""
        if self.pipeline and len(textos) > 1:
            try:
                # Batch inference — muito mais rápido com GPU
                truncados   = [t[:512] for t in textos]
                resultados  = self.pipeline(truncados)
                processados = []

                for resultado in resultados:
                    por_label = {r["label"].upper(): r["score"] for r in resultado}
                    score = por_label.get("POSITIVE", 0) - por_label.get("NEGATIVE", 0)
                    label = max(por_label, key=por_label.get)
                    processados.append({
                        "score_sentimento": round(score, 4),
                        "label":            label,
                        "confianca":        round(por_label[label], 4),
                    })

                return processados
            except Exception:
                pass

        # fallback: analisa um por um
        return [self.analisar(t) for t in textos]


# ---------------------------------------------------------------------------
# PIPELINE COMPLETO: coleta + análise + persistência
# ---------------------------------------------------------------------------

def executar_pipeline_sentimento(
    tickers: list[str] = None,
    salvar_no_banco: bool = True,
    usar_modelo_nlp: bool = True,
) -> list[dict]:
    """
    Executa o pipeline completo de coleta e análise de sentimento.

    Args:
        tickers:          Lista de tickers a monitorar (None = usa TICKERS_MONITORADOS)
        salvar_no_banco:  Se True, salva em SentimentoMercado
        usar_modelo_nlp:  Se False, usa dicionário de palavras (mais rápido)

    Returns:
        Lista de dicts prontos para SentimentoMercado.objects.bulk_create()
    """
    tickers_alvo   = tickers or TICKERS_MONITORADOS
    analisador     = AnalisadorSentimento(usar_modelo=usar_modelo_nlp)

    # 1. Coleta de manchetes
    logger.info("Coletando manchetes...")
    noticias: list[NoticiaColetada] = []

    noticias.extend(InfoMoneyScraper().coletar_manchetes_gerais(limite=30))
    noticias.extend(ValorEconomicoScraper().coletar_manchetes(limite=20))
    noticias.extend(RedditScraper().coletar_todos_subreddits(limite_por_sub=25))

    # Busca específica por ticker no InfoMoney
    for ticker in tickers_alvo[:5]:  # limita para não sobrecarregar
        time.sleep(DELAY_ENTRE_REQUESTS)
        noticias.extend(InfoMoneyScraper().buscar_por_ticker(ticker, limite=5))

    logger.info(f"Total de textos coletados: {len(noticias)}")

    # 2. Análise de sentimento em lote
    textos = [n.titulo + " " + (n.texto or "") for n in noticias]
    sentimentos = analisador.analisar_lote(textos)

    # 3. Monta registros para o banco
    registros = []
    for noticia, sentimento in zip(noticias, sentimentos):
        registros.append({
            "ticker":           noticia.ticker,   # None = sentimento geral
            "data_ref":         noticia.data,
            "fonte":            noticia.fonte,
            "score_sentimento": sentimento["score_sentimento"],
            "volume_mencoes":   1,
            "resumo_nlp":       f"[{sentimento['label']} {sentimento['confianca']:.0%}] {noticia.titulo[:200]}",
            "url_origem":       noticia.url,
        })

    # 4. Agrupa por ticker+fonte+data (média dos scores)
    registros_agrupados = _agregar_por_ticker_fonte_data(registros)

    # 5. Persiste no banco
    if salvar_no_banco:
        _salvar_sentimentos(registros_agrupados, tickers_alvo)

    return registros_agrupados


def _agregar_por_ticker_fonte_data(registros: list[dict]) -> list[dict]:
    """
    Agrega múltiplas notícias da mesma fonte/ticker/data em um único score médio.
    Também soma o volume de menções.
    """
    grupos = {}

    for r in registros:
        chave = (r["ticker"], r["fonte"], r["data_ref"])
        if chave not in grupos:
            grupos[chave] = {"scores": [], "mencoes": 0, "resumos": []}

        grupos[chave]["scores"].append(r["score_sentimento"])
        grupos[chave]["mencoes"] += 1
        grupos[chave]["resumos"].append(r["resumo_nlp"])

    agregados = []
    for (ticker, fonte, data), dados in grupos.items():
        score_medio = round(sum(dados["scores"]) / len(dados["scores"]), 4)
        agregados.append({
            "ticker":           ticker,
            "data_ref":         data,
            "fonte":            fonte,
            "score_sentimento": score_medio,
            "volume_mencoes":   dados["mencoes"],
            "resumo_nlp":       " | ".join(dados["resumos"][:3]),
        })

    return agregados


def _salvar_sentimentos(registros: list[dict], tickers_alvo: list[str]):
    """Persiste os registros em SentimentoMercado via Django ORM."""
    try:
        from market_data.models import SentimentoMercado, Ativo

        # Mapa ticker → objeto Ativo
        ativos = {a.ticker: a for a in Ativo.objects.filter(ticker__in=tickers_alvo)}

        salvos = 0
        for r in registros:
            ativo_obj = ativos.get(r["ticker"]) if r["ticker"] else None

            SentimentoMercado.objects.update_or_create(
                ativo=ativo_obj,
                fonte=r["fonte"],
                data_ref=r["data_ref"],
                defaults={
                    "score_sentimento": r["score_sentimento"],
                    "volume_mencoes":   r["volume_mencoes"],
                    "resumo_nlp":       r["resumo_nlp"],
                },
            )
            salvos += 1

        logger.info(f"SentimentoMercado: {salvos} registros salvos")

    except Exception as e:
        logger.error(f"Erro ao salvar sentimentos: {e}")
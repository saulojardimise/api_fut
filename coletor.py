#!/usr/bin/env python3
"""
Coletor de ajustes diários de contratos futuros da B3 -> Supabase.

Fonte: "Pesquisa por pregão" da B3, Boletim de Negociação Simplificado de
Derivativos (SPRD, BVBG.187). Se ele não estiver disponível, usa o boletim
completo (PR, BVBG.086). Os dois são ZIPs aninhados com XML dentro.

Uso:
    python coletor.py                      # últimos 5 dias úteis (padrão)
    python coletor.py --data 2026-11-12    # um pregão específico
    python coletor.py --inicio 2026-01-02 --fim 2026-11-12   # carga histórica
    python coletor.py --data 2026-11-12 --dry-run            # não grava; mostra amostra
    python coletor.py --arquivo SPRD261112.zip --dry-run     # lê um ZIP local

Variáveis de ambiente (para gravar):
    SUPABASE_URL          ex.: https://abcd1234.supabase.co
    SUPABASE_SECRET_KEY   chave secreta (sb_secret_...) ou service_role (JWT)
"""

from __future__ import annotations

import argparse
import csv
import io
import logging
import os
import re
import sys
import time
import zipfile
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from lxml import etree

log = logging.getLogger("coletor")

URL_B3 = "https://www.b3.com.br/pesquisapregao/download?filelist={prefixo}{data}.zip"
# Boletim completo (PR, BVBG.086) primeiro: traz volume em contratos e financeiro.
# O simplificado (SPRD) é a reserva.
PREFIXOS = tuple(os.environ.get("BOLETINS", "PR,SPRD").split(","))
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
)

# Futuro padrão B3: raiz de 3 caracteres + letra do mês + ano com 2 dígitos (BGIZ26)
RE_FUTURO = re.compile(r"^([A-Z0-9]{3})([FGHJKMNQUVXZ])(\d{2})$")
MESES = {c: i for i, c in enumerate("FGHJKMNQUVXZ", start=1)}

CAMPOS_XML = {
    "AdjstdQt": "ajuste",
    "PrvsAdjstdQt": "ajuste_anterior",
    "AdjstdQtTax": "taxa_ajuste",
    "PrvsAdjstdQtTax": "taxa_ajuste_anterior",
    "VartnPts": "variacao_pts",
    "OpnIntrst": "contratos_abertos",
    "NtlFinVol": "volume_financeiro",
    "FrstPric": "abertura",
    "MinPric": "minima",
    "MaxPric": "maxima",
    "LastPric": "fechamento",
    "TradAvrgPric": "preco_medio",
    "FinInstrmQty": "contratos_negociados",
    "RglrTxsQty": "negocios",
}

TAMANHO_LOTE = 1000


# ----------------------------------------------------------------------------
# Download
# ----------------------------------------------------------------------------
def baixar_zip(dia: date, sessao: requests.Session) -> bytes | None:
    """Baixa o boletim do dia. Retorna None se não houver pregão/arquivo."""
    data_str = dia.strftime("%y%m%d")
    for prefixo in PREFIXOS:
        url = URL_B3.format(prefixo=prefixo, data=data_str)
        for tentativa in range(3):
            try:
                resp = sessao.get(url, timeout=(10, 60))
                break
            except requests.RequestException as e:
                log.warning("Falha ao baixar %s (tentativa %d): %s", url, tentativa + 1, e)
                time.sleep(2 * (tentativa + 1))
        else:
            continue
        if resp.status_code == 200 and _zip_tem_xml(resp.content):
            log.info("%s: baixado %s (%d KB)", dia, prefixo, len(resp.content) // 1024)
            return resp.content
        log.debug("%s: %s indisponível (HTTP %s, %d bytes)",
                  dia, prefixo, resp.status_code, len(resp.content))
    return None


def _zip_tem_xml(conteudo: bytes) -> bool:
    if len(conteudo) < 1024:  # a B3 devolve um arquivo minúsculo quando não há dados
        return False
    try:
        return extrair_xml(conteudo) is not None
    except (zipfile.BadZipFile, OSError, ValueError):
        return False


def extrair_xml(conteudo_zip: bytes) -> bytes | None:
    """Abre ZIPs aninhados e devolve o último XML encontrado (o mais recente)."""
    xmls: list[tuple[str, bytes]] = []

    def varrer(dados: bytes) -> None:
        with zipfile.ZipFile(io.BytesIO(dados)) as z:
            for nome in z.namelist():
                conteudo = z.read(nome)
                if nome.lower().endswith(".zip"):
                    varrer(conteudo)
                elif nome.lower().endswith(".xml"):
                    xmls.append((nome, conteudo))

    varrer(conteudo_zip)
    if not xmls:
        return None
    xmls.sort(key=lambda x: x[0])
    return xmls[-1][1]


# ----------------------------------------------------------------------------
# Parse
# ----------------------------------------------------------------------------
def _local(tag) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _num(texto: str | None):
    if texto is None or texto.strip() == "":
        return None
    try:
        valor = float(texto)
    except ValueError:
        return None
    return int(valor) if valor.is_integer() and "." not in texto else valor


def parsear_xml(xml: bytes) -> list[dict]:
    """Extrai apenas contratos futuros com preço de ajuste."""
    parser = etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False,
        huge_tree=True, recover=True, remove_comments=True,
    )
    raiz = etree.fromstring(xml, parser=parser)
    linhas: dict[tuple, dict] = {}

    for rpt in raiz.iter("{*}PricRpt"):
        dados: dict[str, str] = {}
        for el in rpt.iter():
            nome = _local(el.tag)
            if not el.text:
                continue
            if nome == "TckrSymb":
                dados["ticker"] = el.text.strip()
            elif nome == "Dt" and _local(el.getparent().tag) == "TradDt":
                dados["data_pregao"] = el.text.strip()
            elif nome in CAMPOS_XML:
                dados[CAMPOS_XML[nome]] = el.text.strip()

        ticker = dados.get("ticker", "")
        m = RE_FUTURO.match(ticker)
        if not m or "data_pregao" not in dados:
            continue  # opções, ações, spreads, rolagens etc.
        if _num(dados.get("ajuste")) is None:
            continue  # sem ajuste => não é futuro com ajuste diário

        raiz_cod, letra, ano = m.groups()
        linha = {
            "data_pregao": dados["data_pregao"][:10],
            "ticker": ticker,
            "mercadoria": raiz_cod,
            "mes_vencimento": date(2000 + int(ano), MESES[letra], 1).isoformat(),
        }
        for col in CAMPOS_XML.values():
            linha[col] = _num(dados.get(col))
        if linha["variacao_pts"] is None and linha["ajuste_anterior"] is not None:
            linha["variacao_pts"] = round(linha["ajuste"] - linha["ajuste_anterior"], 6)
        linhas[(linha["data_pregao"], ticker)] = linha  # dedup

    return sorted(linhas.values(), key=lambda r: (r["mercadoria"], r["mes_vencimento"]))


# ----------------------------------------------------------------------------
# Supabase (PostgREST)
# ----------------------------------------------------------------------------
def gravar_supabase(linhas: list[dict], sessao: requests.Session) -> None:
    url_base = os.environ.get("SUPABASE_URL", "").rstrip("/")
    chave = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url_base or not chave:
        sys.exit("Defina SUPABASE_URL e SUPABASE_SECRET_KEY (ou use --dry-run).")

    headers = {
        # User-Agent próprio: o Supabase recusa a chave secreta quando o UA parece um navegador
        "User-Agent": "ajustes-b3-coletor/1.0 (python-requests)",
        "apikey": chave,
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    if not chave.startswith("sb_"):  # chave legada (JWT) também vai no Authorization
        headers["Authorization"] = f"Bearer {chave}"

    url = f"{url_base}/rest/v1/ajustes?on_conflict=data_pregao,ticker"
    agora = datetime.now(ZoneInfo("UTC")).isoformat()
    for i in range(0, len(linhas), TAMANHO_LOTE):
        lote = [{**r, "atualizado_em": agora} for r in linhas[i:i + TAMANHO_LOTE]]
        resp = sessao.post(url, json=lote, headers=headers, timeout=60)
        if resp.status_code >= 300:
            sys.exit(f"Erro no Supabase (HTTP {resp.status_code}): {resp.text[:500]}")
    log.info("Gravadas %d linhas no Supabase", len(linhas))


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def dias_uteis(inicio: date, fim: date) -> list[date]:
    dias, d = [], inicio
    while d <= fim:
        if d.weekday() < 5:  # feriados são detectados pela ausência de arquivo
            dias.append(d)
        d += timedelta(days=1)
    return dias


# ----------------------------------------------------------------------------
# Monitoramento
# ----------------------------------------------------------------------------
MERCADORIAS_OBRIGATORIAS = ("BGI", "CCM", "DI1", "DOL", "IND")
MINIMO_CONTRATOS = 300


def eh_dia_de_pregao(dia: date) -> bool:
    """Dia útil da B3 (fins de semana, feriados da bolsa, 24/12 e 31/12 fechados)."""
    import holidays

    if dia.weekday() >= 5 or (dia.month, dia.day) in ((12, 24), (12, 31)):
        return False
    # Se 31/12 cai no fim de semana, a B3 fecha no último dia útil do ano
    if dia.month == 12 and dia.day >= 29 and date(dia.year, 12, 31).weekday() >= 5:
        if all((dia + timedelta(days=k)).weekday() >= 5 for k in range(1, 31 - dia.day + 1)):
            return False
    return dia not in holidays.financial_holidays("BVMF", years=dia.year)


def verificar(linhas: list[dict], dia: date) -> list[str]:
    """Checagens de qualidade para o pregão `dia`. Retorna a lista de problemas."""
    if not eh_dia_de_pregao(dia):
        log.info("%s não é dia de pregão: nada a verificar", dia)
        return []
    do_dia = [r for r in linhas if r["data_pregao"] == dia.isoformat()]
    if not do_dia:
        return [f"Nenhum ajuste coletado para o pregão de {dia:%d/%m/%Y}"]
    problemas = []
    if len(do_dia) < MINIMO_CONTRATOS:
        problemas.append(f"Só {len(do_dia)} contratos em {dia} (esperado >= {MINIMO_CONTRATOS})")
    presentes = {r["mercadoria"] for r in do_dia}
    faltando = [m for m in MERCADORIAS_OBRIGATORIAS if m not in presentes]
    if faltando:
        problemas.append(f"Mercadorias ausentes em {dia}: {', '.join(faltando)}")
    sem_ajuste = [r["ticker"] for r in do_dia if not r["ajuste"] or r["ajuste"] <= 0]
    if sem_ajuste:
        problemas.append(f"{len(sem_ajuste)} contratos com ajuste inválido (ex.: {sem_ajuste[:5]})")
    return problemas


def registrar_coleta(status: str, linhas: int, ultimo_pregao: str | None, mensagem: str) -> None:
    """Grava uma linha na tabela `coletas` (melhor esforço: nunca derruba a coleta)."""
    url_base = os.environ.get("SUPABASE_URL", "").rstrip("/")
    chave = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url_base or not chave:
        return
    headers = {"User-Agent": "ajustes-b3-coletor/1.0", "apikey": chave,
               "Content-Type": "application/json", "Prefer": "return=minimal"}
    if not chave.startswith("sb_"):
        headers["Authorization"] = f"Bearer {chave}"
    corpo = {"status": status, "linhas": linhas, "ultimo_pregao": ultimo_pregao,
             "mensagem": mensagem[:2000], "execucao": os.environ.get("GITHUB_RUN_ID")}
    try:
        r = requests.post(f"{url_base}/rest/v1/coletas", json=corpo, headers=headers, timeout=30)
        if r.status_code >= 300:
            log.warning("Não registrei a coleta (HTTP %s): %s", r.status_code, r.text[:200])
    except requests.RequestException as e:
        log.warning("Não registrei a coleta: %s", e)


def avisar_healthcheck(ok: bool, mensagem: str) -> None:
    """Ping para healthchecks.io (ou similar), se HEALTHCHECK_URL estiver definido."""
    url = os.environ.get("HEALTHCHECK_URL", "").rstrip("/")
    if not url:
        return
    try:
        requests.post(url if ok else f"{url}/fail", data=mensagem.encode()[:10000], timeout=15)
    except requests.RequestException as e:
        log.warning("Falha ao avisar healthcheck: %s", e)


def tickers_ativos(hoje: date, sessao: requests.Session) -> set[str]:
    """Contratos presentes no pregão mais recente disponível (até 10 dias atrás)."""
    for i in range(10):
        dia = hoje - timedelta(days=i)
        if dia.weekday() >= 5:
            continue
        conteudo = baixar_zip(dia, sessao)
        if conteudo:
            return {r["ticker"] for r in parsear_xml(extrair_xml(conteudo))}
    sys.exit("Não encontrei nenhum pregão recente para definir os contratos ativos.")


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=date.fromisoformat)
    ap.add_argument("--inicio", type=date.fromisoformat)
    ap.add_argument("--fim", type=date.fromisoformat)
    ap.add_argument("--dias", type=int, default=5, help="reprocessar os últimos N dias corridos (padrão 5)")
    ap.add_argument("--somente-ativos", action="store_true",
                    help="grava só os contratos que estão em negociação no último pregão")
    ap.add_argument("--arquivo", help="ZIP local em vez de baixar da B3")
    ap.add_argument("--dry-run", action="store_true", help="não grava; imprime amostra")
    ap.add_argument("--csv", help="salva o resultado também em CSV")
    ap.add_argument("--verificar", action="store_true",
                    help="falha (exit 1) e avisa se o pregão de hoje não foi coletado corretamente")
    ap.add_argument("--mostrar", help="imprime as linhas destes tickers (separados por vírgula)")
    ap.add_argument("-v", "--verbose", action="store_true")
    return ap.parse_args()


def executar(args) -> tuple[list[dict], date]:
    sessao = requests.Session()
    sessao.headers["User-Agent"] = USER_AGENT
    todas: list[dict] = []
    hoje = datetime.now(ZoneInfo("America/Sao_Paulo")).date()

    if args.arquivo:
        with open(args.arquivo, "rb") as f:
            xml = extrair_xml(f.read())
        if xml is None:
            sys.exit("Nenhum XML dentro do arquivo.")
        todas = parsear_xml(xml)
    else:
        if args.data:
            dias = [args.data]
        elif args.inicio:
            dias = dias_uteis(args.inicio, args.fim or hoje)
        else:
            dias = dias_uteis(hoje - timedelta(days=args.dias), hoje)

        ativos: set[str] | None = None
        if args.somente_ativos:
            ativos = tickers_ativos(hoje, sessao)
            log.info("Filtrando %d contratos ativos", len(ativos))

        for dia in dias:
            conteudo = baixar_zip(dia, sessao)
            if conteudo is None:
                log.info("%s: sem pregão ou arquivo ainda não publicado", dia)
                continue
            linhas = parsear_xml(extrair_xml(conteudo))
            # o boletim completo também traz negócios do after-market com a data D+1: descartar
            linhas = [r for r in linhas if r["data_pregao"] == dia.isoformat()]
            if ativos is not None:
                linhas = [r for r in linhas if r["ticker"] in ativos]
            log.info("%s: %d contratos futuros", dia, len(linhas))
            if linhas and not args.dry_run:
                gravar_supabase(linhas, sessao)
            todas.extend(linhas)
            if len(dias) > 1:
                time.sleep(1)  # gentileza com o servidor da B3

    if args.csv and todas:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(todas[0].keys()))
            w.writeheader()
            w.writerows(todas)
        log.info("CSV salvo em %s", args.csv)

    if args.dry_run or args.arquivo:
        for r in todas[:15]:
            print(r)
        print(f"... total: {len(todas)} linhas")
        if args.arquivo and not args.dry_run:
            gravar_supabase(todas, sessao)

    if args.mostrar:
        alvo = set(args.mostrar.split(","))
        for r in todas:
            if r["ticker"] in alvo:
                print(r)

    if not todas:
        log.warning("Nenhum dado coletado.")
    return todas, hoje


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    gravar = not args.dry_run
    try:
        todas, hoje = executar(args)
    except BaseException as e:  # inclui sys.exit de erros do Supabase
        if isinstance(e, KeyboardInterrupt):
            raise
        msg = f"Coleta falhou: {e}"
        log.error(msg)
        if gravar:
            registrar_coleta("erro", 0, None, msg)
        if args.verificar:
            avisar_healthcheck(False, msg)
        sys.exit(1)

    ultimo = max((r["data_pregao"] for r in todas), default=None)
    verificar_agora = args.verificar
    agora = datetime.now(ZoneInfo("America/Sao_Paulo"))
    if verificar_agora and agora.hour < 20 and not any(r["data_pregao"] == hoje.isoformat() for r in todas):
        log.info("Antes das 20h e boletim de hoje ainda não saiu: verificação fica para a próxima execução")
        verificar_agora = False
    problemas = verificar(todas, hoje) if verificar_agora else []
    if problemas:
        msg = "; ".join(problemas)
        log.error("Verificação falhou: %s", msg)
        if gravar:
            registrar_coleta("alerta", len(todas), ultimo, msg)
        avisar_healthcheck(False, msg)
        sys.exit(1)

    msg = f"{len(todas)} linhas; último pregão {ultimo}"
    if gravar:
        registrar_coleta("ok", len(todas), ultimo, msg)
    if verificar_agora:
        avisar_healthcheck(True, msg)
    log.info("OK: %s", msg)


if __name__ == "__main__":
    main()

import io
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import coletor  # noqa: E402

XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:bvmf.052.01.xsd">
 <BizGrp>
  <Document xmlns="urn:bvmf.217.01.xsd">
   <PricRpts>
    <PricRpt>
     <TradDt><Dt>2026-11-12</Dt></TradDt>
     <SctyId><TckrSymb>BGIZ26</TckrSymb></SctyId>
     <FinInstrmAttrbts>
      <NtlFinVol Ccy="BRL">1500000.50</NtlFinVol>
      <OpnIntrst>12345</OpnIntrst>
      <FrstPric>320.00</FrstPric><MinPric>319.5</MinPric><MaxPric>322.0</MaxPric><LastPric>321.6</LastPric><FinInstrmQty>850</FinInstrmQty>
      <AdjstdQt Ccy="BRL">321.45</AdjstdQt>
      <PrvsAdjstdQt Ccy="BRL">320.10</PrvsAdjstdQt>
      <VartnPts>1.35</VartnPts>
     </FinInstrmAttrbts>
    </PricRpt>
    <PricRpt>
     <TradDt><Dt>2026-11-12</Dt></TradDt>
     <SctyId><TckrSymb>DI1F28</TckrSymb></SctyId>
     <FinInstrmAttrbts>
      <AdjstdQt Ccy="BRL">88123.45</AdjstdQt>
      <AdjstdQtTax>13.215</AdjstdQtTax>
      <PrvsAdjstdQt Ccy="BRL">88100.00</PrvsAdjstdQt>
      <PrvsAdjstdQtTax>13.230</PrvsAdjstdQtTax>
     </FinInstrmAttrbts>
    </PricRpt>
    <PricRpt>  <!-- opcao sobre futuro: deve ser ignorada -->
     <TradDt><Dt>2026-11-12</Dt></TradDt>
     <SctyId><TckrSymb>BGIZ26C320</TckrSymb></SctyId>
     <FinInstrmAttrbts><LastPric>5.1</LastPric></FinInstrmAttrbts>
    </PricRpt>
    <PricRpt>  <!-- unit/FII com formato parecido, sem ajuste: ignorar -->
     <TradDt><Dt>2026-11-12</Dt></TradDt>
     <SctyId><TckrSymb>KLBN11</TckrSymb></SctyId>
     <FinInstrmAttrbts><LastPric>20.1</LastPric></FinInstrmAttrbts>
    </PricRpt>
   </PricRpts>
  </Document>
 </BizGrp>
</Document>
"""


def _zip_aninhado(xml: bytes) -> bytes:
    interno = io.BytesIO()
    with zipfile.ZipFile(interno, "w") as z:
        z.writestr("SPRD261112_1.xml", b"<velho/>")
        z.writestr("SPRD261112_2.xml", xml)
    externo = io.BytesIO()
    with zipfile.ZipFile(externo, "w") as z:
        z.writestr("SPRD261112.zip", interno.getvalue())
    return externo.getvalue()


def test_extrai_ultimo_xml_de_zip_aninhado():
    assert coletor.extrair_xml(_zip_aninhado(XML)) == XML


def test_parse_filtra_somente_futuros_com_ajuste():
    linhas = coletor.parsear_xml(XML)
    assert [r["ticker"] for r in linhas] == ["BGIZ26", "DI1F28"]

    boi = linhas[0]
    assert boi["mercadoria"] == "BGI"
    assert boi["mes_vencimento"] == "2026-12-01"
    assert boi["ajuste"] == 321.45
    assert boi["ajuste_anterior"] == 320.10
    assert boi["contratos_abertos"] == 12345
    assert boi["taxa_ajuste"] is None
    assert (boi["abertura"], boi["minima"], boi["maxima"], boi["fechamento"]) == (320.0, 319.5, 322.0, 321.6)
    assert boi["contratos_negociados"] == 850
    assert linhas[1]["abertura"] is None  # sem negócios no dia

    di = linhas[1]
    assert di["mes_vencimento"] == "2028-01-01"
    assert di["taxa_ajuste"] == 13.215
    assert di["taxa_ajuste_anterior"] == 13.230


def test_calendario_b3():
    from datetime import date
    assert coletor.eh_dia_de_pregao(date(2026, 11, 12))
    assert not coletor.eh_dia_de_pregao(date(2026, 11, 20))   # Consciência Negra
    assert not coletor.eh_dia_de_pregao(date(2026, 12, 24))   # véspera de Natal
    assert not coletor.eh_dia_de_pregao(date(2026, 11, 14))   # sábado


def test_verificar_detecta_problemas():
    from datetime import date
    dia = date(2026, 11, 12)
    assert coletor.verificar([], dia) == ["Nenhum ajuste coletado para o pregão de 12/11/2026"]
    assert coletor.verificar([], date(2026, 11, 20)) == []  # feriado: sem alarme

    ok = [{"data_pregao": "2026-11-12", "ticker": f"{m}X{i}", "mercadoria": m, "ajuste": 10.0}
          for i in range(70) for m in coletor.MERCADORIAS_OBRIGATORIAS]
    assert coletor.verificar(ok, dia) == []

    ruim = [r for r in ok if r["mercadoria"] != "BGI"][:100]
    problemas = coletor.verificar(ruim, dia)
    assert any("Só 100" in p for p in problemas)
    assert any("BGI" in p for p in problemas)


def test_ultimo_dia_util_do_ano():
    from datetime import date
    assert not coletor.eh_dia_de_pregao(date(2022, 12, 30))   # 31/12/2022 foi sábado
    assert not coletor.eh_dia_de_pregao(date(2023, 12, 29))   # 31/12/2023 foi domingo
    assert coletor.eh_dia_de_pregao(date(2023, 12, 28))

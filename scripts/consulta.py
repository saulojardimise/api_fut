#!/usr/bin/env python3
"""Consulta paginada na API do Supabase e imprime CSV. Uso: python scripts/consulta.py "ajustes?mercadoria=eq.BGI" """
import csv, os, sys
import requests

caminho = sys.argv[1]
url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/" + caminho
chave = os.environ["SUPABASE_SECRET_KEY"]
h = {"apikey": chave, "User-Agent": "ajustes-b3-consulta/1.0"}
if not chave.startswith("sb_"):
    h["Authorization"] = f"Bearer {chave}"
linhas, passo = [], 1000
while True:
    r = requests.get(url, headers={**h, "Range": f"{len(linhas)}-{len(linhas) + passo - 1}"}, timeout=60)
    r.raise_for_status()
    lote = r.json()
    linhas += lote
    if len(lote) < passo:
        break
if linhas:
    w = csv.DictWriter(sys.stdout, fieldnames=list(linhas[0].keys()))
    w.writeheader(); w.writerows(linhas)
print(f"# {len(linhas)} linhas", file=sys.stderr)

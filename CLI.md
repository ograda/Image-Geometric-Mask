# Crystal Filter — CLI

```powershell
py .\crystal_filter.py                               # lote sem JSON de diagnóstico
py .\crystal_filter.py --single "Terra sobre o Magitek na neve"
py .\crystal_filter.py --preview                     # formas sobre fundo branco
py .\crystal_filter.py --preview --debug             # branco + JSONs
py .\crystal_filter.py --debug                       # lote + JSONs por variação
py .\crystal_filter.py --single "ff01" --debug       # arte individual + JSONs
py .\crystal_filter.py --batch --seed 100 --count 10
py .\crystal_filter.py --input-dir ".\images" --batch
py .\crystal_filter.py --template hexagon --cell-width 115 --cell-height 130
```

`--debug` pode ser combinado com `--preview`, `--single` ou `--batch`. Sem `--preview`, a arte é processada normalmente, e `--debug` apenas habilita a gravação de JSONs. Por compatibilidade, `mode: "white"` no `config.json` ainda seleciona branco na ausência de flags de modo.

`batch.retry_on_error` é um inteiro >= 0: `0` não repete; `3` permite três repetições após a tentativa inicial. Todas as tentativas de uma mesma imagem utilizam seeds idênticas. Resultados antigos de outras seeds são preservados.

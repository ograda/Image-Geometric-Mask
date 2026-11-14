# Terminal

```powershell
py .\crystal_filter.py
py .\crystal_filter.py --batch --count 10
py .\crystal_filter.py --single "final-fantasy-i"
py .\crystal_filter.py --debug --template rhombus --seed 100
py .\crystal_filter.py --single "final-fantasy-i" --seed 100 --count 1
py .\crystal_filter.py --template square --cell-width 110
py .\crystal_filter.py --config ".\config.json" --input-dir ".\images"
```

`--single` aceita um nome sem extensão, um nome com `.png` ou um caminho completo. Os resultados de cada imagem são colocados na subpasta com o mesmo nome da arte original, dentro de `output.directory`.

`--debug` utiliza a imagem branca e salva diagramas de malha e foco em `results/_preview` (ou o diretório definido em `output.directory`). A seed é aleatória quando `--seed` não é informado.

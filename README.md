# JulioOS Crystal Filter

Filtro procedural para transformar ilustrações em composições facetadas. Gera recortes geométricos, áreas transparentes e variações de luz a partir de uma malha configurável, sem redesenhar a imagem original.

## Instalação

py -m pip install -r instal.requirements

## Comandos


### Execução básica

py .\crystal_filter.py : Processa todas as imagens PNG da pasta configurada.

### Flags de execução

--help : Exibe todos os argumentos disponíveis.
--batch : Força o processamento em lote, independentemente do modo definido no JSON.
--preview : Gera templates para visualizar as geometrias, malha e áreas protegidas.
--debug : Atalho alternativo para o modo preview.
--single "image.png" : Processa somente a imagem especificada.

### Parâmetros adicionais

--config "config.json" : Seleciona o arquivo de configuração.
--input-dir "images" : Define a pasta de entrada para o processamento em lote.
--count 12 : Define a quantidade de variações por imagem.
--seed 100 : Utiliza uma seed específica para reproduzir os resultados.
--template `geometry.shapes`: Seleciona a geometria utilizada no filtro
--cell-width 135 : Define a largura das células geométricas.
--cell-height 197 : Define a altura das células geométricas.
--unit px : Define a unidade das dimensões: px ou relative.

### `geometry.shapes`

Define as **dimensões de cada tipo de forma**, independentemente do template escolhido em `geometry.template`.

- `rhombus`: diagonal horizontal (`width`) e vertical (`height`) dos losangos.
- `square`: tamanho do lado (`side`).
- `rectangle`: largura (`width`) e altura (`height`) dos retângulos.
- `triangle`: triangulos isósceles de largura (`width`) e altura (`height`).
- `hexagon`: largura e altura totais dos hexágonos - \[
\text{height} = \frac{2}{\sqrt{3}} \times \text{width}
\].
- `free`: largura e altura da célula que contém os polígonos personalizados definidos em `geometry.free`.

Em cada forma, `unit: "px"` interpreta `width` e `height` como pixels **na resolução de trabalho** (`output.work_width`), enquanto `unit: "relative"` interpreta esses valores como proporções da largura e altura da imagem, respectivamente.

**Formas menores** produzem mais facetas e divisões; **formas maiores** produzem menos facetas e recortes mais amplos. As dimensões não alteram a resolução final do arquivo.

## Configurações

### `silhouette`

Define a forma em que realizaremos a avaliação para o recorte da borda geometrica das imagens; 

opções de `shape` disponíveis:

"crystal_superellipse" : uma figura semelhante a um retângulo com cantos arredondados matematicamente.


"radius": controla o raio da elipse.
"power": controla o arredondamento da borda da elipse, quanto menor mais elíptica.
"outer_prune": controla quantos recortes serão sorteados e removidos da borda.

"allow_detached_facets": permite a criação de subdiviões na imagem final - com o comando ativo ao realizar os sorteios e recortes, podemos chegar em uma situação em que teremos duas imagens sem nenhum recorte conectado, formando assim subdivisões da imagem.

### `protected_areas`

Aqui definimos elipses de proteção para areas específicas da imagem, podemos ter multiplas em caso de proteger certos personagens de terem elementos apagados.

### `facets`

Aqui definimos o efeito de escurecimento e clareamento dos recortes da imagem E `minimum_hole_separation_cells` nos deixa escolher qual a distancia minima entre efeitos nos recortes.

`subtle_variation` define uma pequena variação para os recortes que não foram selecionados para sofrer variações

### `finish`

aqui aplicamos alterações para toda a imagem, após finalizar os recortes.

# JulioOS Crystal Filter

Filtro geométrico procedural para imagens PNG. Usa peças repetidas para produzir uma silhueta recortada, facetas claras/escuras e espaços transparentes, sem regenerar a arte original. Pode processar uma imagem, todas as imagens PNG de uma pasta ou gerar testes brancos.

## Instalação

```powershell
py -m pip install -r instal.requirements
```

## Comandos

| Comando | Ação |
| --- | --- |
| `py .\crystal_filter.py` | Processa os PNGs na pasta configurada em `batch.input_directory`. |
| `py .\crystal_filter.py --batch` | Processa explicitamente todas as imagens PNG da pasta. |
| `py .\crystal_filter.py --single "image"` | Processa somente `image.png` (aceita nome com extensão/caminho). |
| `py .\crystal_filter.py --debug` | Cria imagens de teste brancas, grades e mapa das áreas protegidas. |
| `py .\crystal_filter.py --count 10` | Gera dez variações por imagem. |
| `py .\crystal_filter.py --seed 100` | Utiliza uma seed específica e reproduzível. |
| `py .\crystal_filter.py --template hexagon` | Escolhe outro template sem alterar o JSON. |
| `py .\crystal_filter.py --help` | Lista todos os comandos e argumentos. |

Argumentos adicionais: `--config`, `--input-dir`, `--cell-width`, `--cell-height`, `--unit`.

## Geometrias e dimensões

A forma ativa é escolhida em `geometry.template`; suas medidas ficam em `geometry.shapes`.

| Forma | Medidas |
| --- | --- |
| `rhombus` | `width` e `height`: diagonais horizontal e vertical. |
| `square` | `side`: comprimento do lado. |
| `rectangle` | `width` e `height`: lados independentes. |
| `triangle` | `width` e `height`: base e altura de triângulos isósceles. |
| `hexagon` | `width` e `height`: dimensões totais do hexágono. |
| `free` | `polygons`: vértices normalizados; `step_x`, `step_y` e `row_shift` controlam a repetição. |

As medidas usam `unit: "px"` ou `unit: "relative"`. Para `free`, `width`, `height` e `unit` são opcionais; se omitidos, a escala é herdada de `rhombus`. Você pode adicioná-los ao próprio bloco `free` para especificar dimensões independentes.

## Organização do config.json

- `output`: pasta de resultados, formatos, dimensões finais, quantidade e comparativos.
- `work_size`: resolução de processamento antes da exportação.
- `geometry`: tipo, deslocamento, margem e dimensões das peças.
- `silhouette`: superelipse e remoções periféricas.
- `protected_areas`: elipses onde não podem ser feitos vazios.
- `facets`: quantidades, brilho/sombra e **`facets.geometry.lines`** para linhas comuns ao filtro e ao debug.
- `finish`: granulação, contraste e saturação globais.
- `debug_white`: cores usadas somente nos testes brancos.
- `batch`: pasta de entrada e comportamento diante de erros.

No modo `--debug`, as linhas utilizam `facets.geometry.lines`; `debug_white.draw_grid` apenas mostra ou oculta a malha. O arquivo original nunca é modificado.

### Notas

- JSON exige aspas nos nomes das propriedades, por exemplo, `"resize": true`.
- O formato correto é `"jpeg"`, não `"jpge"`.
- JPEG não preserva transparência; as regiões transparentes são compostas sobre branco.
- Para a versão mostrada neste pacote, `mode` continua no JSON. `--debug`, `--single` e `--batch` prevalecem sobre ele.

# JulioOS Crystal Filter

Filtro procedural para transformar ilustrações em composições facetadas. Gera recortes geométricos, áreas transparentes e variações de luz a partir de uma malha configurável, sem redesenhar a imagem original.

## `geometry.shapes`

Define as **dimensões de cada tipo de forma**, independentemente do template escolhido em `geometry.template`.

- `rhombus`: diagonal horizontal (`width`) e vertical (`height`) dos losangos.
- `square`: lado horizontal e vertical; os dois valores devem ser iguais.
- `rectangle`: largura e altura dos retângulos.
- `triangle`: base e altura dos triângulos.
- `hexagon`: largura e altura totais dos hexágonos.
- `free`: largura e altura da célula que contém os polígonos personalizados definidos em `geometry.free`.

Em cada forma, `unit: "px"` interpreta `width` e `height` como pixels **na resolução de trabalho** (`output.work_width`), enquanto `unit: "relative"` interpreta esses valores como proporções da largura e altura da imagem, respectivamente.

**Formas menores** produzem mais facetas e divisões; **formas maiores** produzem menos facetas e recortes mais amplos. As dimensões não alteram a resolução final do arquivo.

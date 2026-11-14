#!/usr/bin/env python3
"""JulioOS Crystal Filter V3: tesselações selecionáveis e tamanhos configuráveis.

Requer: pillow e numpy. Uma configuração JSON compartilhada controla o motor.
O código não utiliza IA nem altera os arquivos de entrada.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import sys
import unicodedata
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps

RESAMPLE = Image.Resampling.LANCZOS
TEMPLATES = ('rhombus', 'square', 'rectangle', 'triangle', 'hexagon', 'free')


def selected_shape_dimensions(geometry):
    """Retorna dimensões da forma selecionada; mantém suporte a config V3."""
    template = geometry.get('template', 'rhombus')
    return (geometry.get('size')
            or geometry.get('shapes', {}).get(template)
            or geometry.get('sizes', {}).get(template))


def check_config(cfg):
    if cfg['mode'] not in ('white', 'artwork'):
        raise ValueError('mode precisa ser white ou artwork')
    out = cfg['output']
    w, h = int(out['width']), int(out['height'])
    if w < 100 or h < 100 or w * 4 != h * 5:
        raise ValueError('A saída precisa ter proporção 5:4 e pelo menos 100px')
    if int(out['work_width']) < w:
        raise ValueError('output.work_width deve ser >= output.width')
    if int(cfg['variations']['count']) < 1:
        raise ValueError('variations.count precisa ser >= 1')
    g = cfg['geometry']
    if g.get('template', 'rhombus') not in TEMPLATES:
        raise ValueError(f'geometry.template deve ser um de: {", ".join(TEMPLATES)}')
    size = selected_shape_dimensions(g)
    if size:
        if size.get('unit') not in ('px', 'relative'):
            raise ValueError('geometry.shapes.<template>.unit deve ser px ou relative')
        if float(size['width']) <= 0 or float(size['height']) <= 0:
            raise ValueError('Dimensões da célula devem ser positivas')
        if g.get('template') == 'square' and size['unit'] == 'px' and abs(float(size['width']) - float(size['height'])) > 1e-5:
            raise ValueError('square exige width = height em pixels')
    elif 'horizontal_diagonal' not in g or 'vertical_diagonal' not in g:
        raise ValueError('Defina geometry.shapes.<template> ou as diagonais legadas')
    if g.get('template') == 'free':
        free = g.get('free', {})
        polys = free.get('polygons', [free['vertices']] if 'vertices' in free else [])
        if not polys:
            raise ValueError('geometry.free.polygons deve conter uma ou mais formas')
        for polygon in polys:
            if len(polygon) < 3 or any(len(point) != 2 for point in polygon):
                raise ValueError('Cada polígono livre precisa de >=3 pontos [x,y]')
            if not all(math.isfinite(float(value)) for point in polygon for value in point):
                raise ValueError('Pontos inválidos no template livre')
        if float(free.get('step_x', 1)) <= 0 or float(free.get('step_y', 1)) <= 0:
            raise ValueError('Os passos de repetição do template livre devem ser positivos')
    sil = cfg['silhouette']
    if sil['shape'] != 'crystal_superellipse':
        raise ValueError('Somente silhouette.shape=crystal_superellipse está implementado')
    if any(float(sil[key]) <= 0 for key in ('radius_x','radius_y','power_x','power_y')):
        raise ValueError('Raio e expoentes da silhueta precisam ser positivos')
    if int(cfg['facets']['minimum_hole_separation_cells']) < 0:
        raise ValueError('facets.minimum_hole_separation_cells precisa ser >= 0')
    if not 0 <= int(sil['outer_prune_min']) <= int(sil['outer_prune_max']):
        raise ValueError('Valores inválidos de remoção periférica')
    f = cfg['facets']
    for name in ('holes', 'dark', 'light'):
        if int(f[name]) < 0:
            raise ValueError(f'facets.{name} precisa ser >= 0')
    if not cfg['focus']['ellipses']:
        raise ValueError('Inclua ao menos uma elipse em focus.ellipses')
    for cx, cy, rx, ry in cfg['focus']['ellipses']:
        if not (0 <= cx <= 1 and 0 <= cy <= 1 and 0 < rx <= 1 and 0 < ry <= 1):
            raise ValueError('Coordenadas de foco devem estar no intervalo [0,1]')
    if cfg['mode'] == 'artwork' and not cfg['input']:
        raise ValueError('Em mode=artwork, informe o caminho da imagem-base')


def dimensions(w, h, cfg):
    g = cfg['geometry']
    size = selected_shape_dimensions(g)
    if size is None:  # Compatibilidade com config.json V2
        return w * float(g['horizontal_diagonal']), h * float(g['vertical_diagonal'])
    if size['unit'] == 'px':
        return float(size['width']), float(size['height'])
    return w * float(size['width']), h * float(size['height'])


def candidate_polygons(w, h, cfg):
    """Produz células fechadas em coordenadas do canvas, antes da silhueta.

    O template livre aceita uma ou muitas formas por célula, expressas em
    coordenadas normalizadas de 0..1. É do autor garantir tesselação se quiser
    uma superfície sem espaços.
    """
    g = cfg['geometry']
    template = g.get('template', 'rhombus')
    cw, ch = dimensions(w, h, cfg)
    if cw < 8 or ch < 8:
        raise ValueError('Células pequenas demais: use dimensões >= 8 px')
    ox, oy = w * float(g.get('origin_x', 0)), h * float(g.get('origin_y', 0))
    result = {}
    if template == 'rhombus':
        # Mantém a transformação da malha aprovada anteriormente.
        corners_uv = [((x-ox)/cw + (y-oy)/ch, (x-ox)/cw - (y-oy)/ch)
                      for x in (-cw, w+cw) for y in (-ch, h+ch)]
        min_i = math.floor(min(v[0] for v in corners_uv)) - 2
        max_i = math.ceil(max(v[0] for v in corners_uv)) + 2
        min_j = math.floor(min(v[1] for v in corners_uv)) - 2
        max_j = math.ceil(max(v[1] for v in corners_uv)) + 2
        for i in range(min_i, max_i + 1):
            for j in range(min_j, max_j + 1):
                uv = ((i,j), (i+1,j), (i+1,j+1), (i,j+1))
                result[(i,j)] = [(ox+(u+v)*cw/2, oy+(u-v)*ch/2) for u,v in uv]
    elif template in ('square', 'rectangle'):
        if template == 'square' and abs(cw-ch) > .001:
            raise ValueError('Para quadrados use dimensões fisicamente iguais (em pixels)')
        nx = math.ceil(w/cw) + 4
        ny = math.ceil(h/ch) + 4
        for i in range(-2, nx):
            for j in range(-2, ny):
                x, y = ox+i*cw, oy+j*ch
                result[(i,j)] = [(x,y),(x+cw,y),(x+cw,y+ch),(x,y+ch)]
    elif template == 'triangle':
        # Triângulos isósceles: duas peças por paralelogramo.
        # Para triângulos equiláteros: height = width * sqrt(3) / 2.
        nx = math.ceil(w/cw) + math.ceil(h/ch) + 8
        ny = math.ceil(h/ch) + 7
        for j in range(-3, ny):
            for i in range(-nx, nx+1):
                x, y = ox + i*cw + j*cw*.5, oy + j*ch
                a, b, c, d = (x,y),(x+cw,y),(x+cw/2,y+ch),(x+cw*1.5,y+ch)
                result[(i,j,0)] = [a,b,c]
                result[(i,j,1)] = [b,d,c]
    elif template == 'hexagon':
        nx = math.ceil(w/cw) + 8
        ny = math.ceil(h/(ch*.75)) + 8
        rx, ry = cw / math.sqrt(3), ch/2
        for j in range(-4, ny):
            for i in range(-4, nx):
                cx = ox + (i + (j%2)*.5)*cw
                cy = oy + j*(ch*.75)
                result[(i,j)] = [(cx + rx*math.cos(math.pi/6 + k*math.pi/3),
                                  cy + ry*math.sin(math.pi/6 + k*math.pi/3))
                                 for k in range(6)]
    elif template == 'free':
        opts = g.get('free', {})
        shapes = opts.get('polygons', [opts['vertices']] if 'vertices' in opts else [])
        sx, sy = float(opts.get('step_x',1)), float(opts.get('step_y',1))
        shift = float(opts.get('row_shift',0))
        nx = math.ceil(w/(cw*sx)) + 12
        ny = math.ceil(h/(ch*sy)) + 12
        for j in range(-6, ny):
            for i in range(-6, nx):
                x, y = ox + (i*sx + j*shift)*cw, oy + j*sy*ch
                for n, poly in enumerate(shapes):
                    result[(i,j,n)] = [(x + float(px)*cw, y + float(py)*ch)
                                       for px,py in poly]
    return result


def polygon_center(poly):
    return (sum(x for x,y in poly)/len(poly), sum(y for x,y in poly)/len(poly))


def cells_in_canvas(w,h,cfg):
    margin = float(cfg['geometry']['edge_margin'])
    sh = cfg['silhouette']
    cells = {}
    for key, poly in candidate_polygons(w,h,cfg).items():
        if any(x < w*margin or x > w*(1-margin) or y < h*margin or y > h*(1-margin)
               for x,y in poly):
            continue
        cx, cy = polygon_center(poly)
        nx = abs(cx/w - .5) / float(sh['radius_x'])
        ny = abs(cy/h - .5) / float(sh['radius_y'])
        if nx**float(sh['power_x']) + ny**float(sh['power_y']) > 1.0:
            continue
        cells[key] = {'polygon':poly, 'center':(cx,cy)}
    if len(cells) > 18000:
        raise ValueError('Malha muito densa: aumente geometry.shapes.<template>')
    return cells


def adjacency(cells):
    """Identifica vizinhos por arestas compartilhadas; independe da forma.

    Também identifica facetas periféricas por arestas sem vizinho, inclusive
    nos templates livres onde a quantidade de lados varia.
    """
    owners = {}
    neigh = {key:set() for key in cells}
    for key, record in cells.items():
        poly = record['polygon']
        for index in range(len(poly)):
            a, b = poly[index], poly[(index+1)%len(poly)]
            edge = tuple(sorted(((round(a[0],2),round(a[1],2)),
                                 (round(b[0],2),round(b[1],2)))))
            owners.setdefault(edge,[]).append(key)
    for key in cells:
        cells[key]['boundary_edges'] = 0
    for shared in owners.values():
        if len(shared) == 1:
            cells[shared[0]]['boundary_edges'] += 1
        elif len(shared) == 2:
            a,b = shared
            neigh[a].add(b)
            neigh[b].add(a)
        else:
            raise ValueError('Geometria inválida: mais de duas facetas compartilham uma aresta')
    return neigh


def connected(keys, neigh):
    if not keys:
        return False
    queue = deque([next(iter(keys))]); reached = {queue[0]}
    while queue:
        for nxt in neigh[queue.popleft()]:
            if nxt in keys and nxt not in reached:
                reached.add(nxt);queue.append(nxt)
    return len(reached) == len(keys)


def polygon_mask(poly,w,h):
    x0=max(0,math.floor(min(x for x,y in poly))-2)
    y0=max(0,math.floor(min(y for x,y in poly))-2)
    x1=min(w,math.ceil(max(x for x,y in poly))+3)
    y1=min(h,math.ceil(max(y for x,y in poly))+3)
    if x0>=x1 or y0>=y1:return None,None
    m=Image.new('L',(x1-x0,y1-y0),0)
    ImageDraw.Draw(m).polygon([(x-x0,y-y0) for x,y in poly],fill=255)
    return (slice(y0,y1),slice(x0,x1)),np.asarray(m)>0


def protected_cells(cells,w,h,cfg):
    mask=Image.new('L',(w,h),0)
    d=ImageDraw.Draw(mask)
    m=float(cfg['focus']['safety_margin'])
    for cx,cy,rx,ry in cfg['focus']['ellipses']:
        d.ellipse(((cx-rx-m)*w,(cy-ry-m)*h,(cx+rx+m)*w,(cy+ry+m)*h),fill=255)
    focus=np.asarray(mask)>0
    chosen=set()
    for key,cell in cells.items():
        sl,local=polygon_mask(cell['polygon'],w,h)
        if sl is not None and np.any(focus[sl][local]):
            chosen.add(key)
    return chosen


def prune_cells(cells, protected, neighbors, cfg, rng):
    selected = set(cells)
    sil = cfg['silhouette']
    count=int(rng.integers(int(sil['outer_prune_min']),int(sil['outer_prune_max'])+1))
    removed=[]
    for _ in range(count):
        # Aresta externa: faceta sem todos os vizinhos naturais ou adjacente a remoção.
        exposed=[key for key in sorted(selected)
                 if key not in protected and
                 (cells[key]['boundary_edges'] > 0
                  or any(nb not in selected for nb in neighbors[key]))]
        rng.shuffle(exposed)
        exposed.sort(key=lambda k: sum(nb not in selected for nb in neighbors[k])+
                     cells[k]['boundary_edges'],reverse=True)
        chosen=None
        for key in exposed:
            remainder=selected-{key}
            if sil.get('allow_detached_facets',False) or connected(remainder,neighbors):
                chosen=key;break
        if chosen is None:break
        selected.remove(chosen);removed.append(chosen)
    return selected,removed


def choose_states(selected,protected,neighbors,cells,cfg,rng):
    f=cfg['facets']
    # Furos internos: não podem cruzar o foco, bordas da silhueta ou outro furo.
    available=[k for k in sorted(selected) if k not in protected and
               all(n in selected for n in neighbors[k]) and
               cells[k]['boundary_edges'] == 0]
    requested=int(f['holes'])
    separation=int(f['minimum_hole_separation_cells'])
    holes=[]
    # Uma ordem aleatória pode produzir menos vazios que outra; experimentar
    # várias ordens, sem mudar a seed e sem tornar a execução não determinística.
    for _ in range(50 if requested else 0):
        candidates=available.copy()
        rng.shuffle(candidates)
        trial=[]; forbidden=set()
        for key in candidates:
            if key in forbidden:continue
            trial.append(key)
            reached={key};frontier={key}
            for _ in range(separation):
                frontier={n for old in frontier for n in neighbors[old]}-reached
                reached |= frontier
            forbidden |= reached
            if len(trial)==requested:break
        if len(trial)>len(holes):holes=trial
        if len(holes)==requested:break
    if len(holes)<requested:
        raise ValueError(f'Apenas {len(holes)} de {requested} furos são possíveis; reduza facets.holes ou o foco')
    rest=sorted(selected-set(holes));rng.shuffle(rest)
    if len(rest)<int(f['dark'])+int(f['light']):
        raise ValueError('Poucas facetas para dark + light: reduza as contagens ou o tamanho das peças')
    dark=rest[:int(f['dark'])]
    light=rest[int(f['dark']):int(f['dark'])+int(f['light'])]
    return holes,dark,light


def alpha_mask(w,h,selected,cells,holes):
    s=2;im=Image.new('L',(w*s,h*s),0);d=ImageDraw.Draw(im)
    for key in sorted(selected):
        d.polygon([(round(x*s),round(y*s)) for x,y in cells[key]['polygon']],fill=255)
    for key in holes:
        d.polygon([(round(x*s),round(y*s)) for x,y in cells[key]['polygon']],fill=0)
    return np.asarray(im.resize((w,h),RESAMPLE),dtype=np.float32)/255.0


def raster_indices(w,h,cells):
    im=Image.new('I',(w,h),0)
    d=ImageDraw.Draw(im)
    key_to_id={k:i+1 for i,k in enumerate(sorted(cells))}
    for k,i in key_to_id.items():
        d.polygon(cells[k]['polygon'],fill=i)
    return np.asarray(im,dtype=np.int32),key_to_id


def grid_lines(w,h,selected,cells,width,opacity):
    if opacity <= 0:
        return np.zeros((h,w),dtype=np.float32)
    im=Image.new('L',(w,h),0)
    d=ImageDraw.Draw(im)
    for k in selected:
        poly=cells[k]['polygon']
        d.line(poly+[poly[0]], fill=255, width=max(1,round(width)))
    return np.asarray(im,dtype=np.float32)*float(opacity)/255.0


def resize_premultiplied(rgb,alpha,size):
    def down(channel):
        return np.asarray(Image.fromarray(channel.astype(np.float32),'F').resize(size,RESAMPLE),dtype=np.float32)
    a=np.clip(down(alpha),0,1)
    channels=[]
    for n in range(3):
        premult=down(rgb[:,:,n]*alpha)
        channels.append(np.divide(premult,a,out=np.zeros_like(premult),where=a>1e-4))
    return Image.fromarray(np.uint8(np.clip(np.rint(np.stack((*channels,a*255),axis=2)),0,255)),'RGBA')


def render(cfg,cells,selected,holes,darks,lights,protected,source,seed,w,h):
    rng=np.random.default_rng(seed+97610)
    ids,key_to_id=raster_indices(w,h,cells)
    rgb=source[:,:,:3].astype(np.float32).copy()
    debug=cfg['debug_white'];finish=cfg['finish'];fac=cfg['facets']
    if cfg['mode']=='white':
        cmap=np.full((len(key_to_id)+1,3),debug['normal_color'],dtype=np.float32)
        cmap[0]=[255,255,255]
        for k in darks:cmap[key_to_id[k]]=debug['dark_color']
        for k in lights:cmap[key_to_id[k]]=debug['light_color']
        rgb=cmap[ids]
    else:
        modifiers=np.zeros(len(key_to_id)+1,dtype=np.float32)
        darkset=set(darks); lightset=set(lights)
        for k,i in key_to_id.items():
            if k in darkset:modifiers[i]=-fac['dark_strength']*rng.uniform(.65,1)
            elif k in lightset:modifiers[i]=fac['light_strength']*rng.uniform(.60,1)
            else:modifiers[i]=rng.uniform(-fac['subtle_variation'],fac['subtle_variation'])
        strength=np.ones((h,w),np.float32)
        Y,X=np.ogrid[:h,:w]
        for cx,cy,rx,ry in cfg['focus']['ellipses']:
            inside=((X/w-cx)/rx)**2+((Y/h-cy)/ry)**2<=1
            strength[inside]=.25
        gray=(rgb[:,:,0]*.2126+rgb[:,:,1]*.7152+rgb[:,:,2]*.0722)[:,:,None]
        rgb=gray+(rgb-gray)*finish['saturation']
        rgb=(rgb-127.5)*finish['contrast']+127.5
        rgb*=1+modifiers[ids][:,:,None]*strength[:,:,None]
    if cfg['mode']=='white':
        opacity=debug['grid_line_opacity'] if debug['draw_grid'] else 0
        width=debug['grid_line_width_px']
        line_color=[89,112,153]
    else:
        opacity=finish['line_opacity'];width=finish['line_width_px'];line_color=[235,240,255]
    line=grid_lines(w,h,selected,cells,width,opacity)
    rgb=rgb*(1-line[:,:,None])+np.array(line_color,dtype=np.float32)*line[:,:,None]
    if cfg['mode']=='artwork' and finish['grain']>0:
        rgb+=rng.normal(0,finish['grain'],(h,w,1)).astype(np.float32)
    alpha=alpha_mask(w,h,selected,cells,holes)
    alpha*=source[:,:,3].astype(np.float32)/255.0
    if set(holes)&protected:
        raise RuntimeError('Região protegida interceptada por furo')
    return resize_premultiplied(np.clip(rgb,0,255),alpha,
                                (int(cfg['output']['width']),int(cfg['output']['height'])))


def checkerboard(w,h,unit=20):
    y,x=np.mgrid[:h,:w]
    v=np.where(((x//unit+y//unit)%2)==0,243,218).astype(np.uint8)
    return Image.fromarray(np.stack([v,v,v],axis=2),'RGB').convert('RGBA')


def contact_sheet(outputs,path):
    tw,th=375,300
    cols=min(3,len(outputs));rows=math.ceil(len(outputs)/cols)
    sheet=Image.new('RGB',(cols*tw,rows*(th+45)+24),'#eef1f6')
    d=ImageDraw.Draw(sheet)
    for pos,(img,seed,rec) in enumerate(outputs):
        x=(pos%cols)*tw;y=(pos//cols)*(th+45)
        cb=checkerboard(tw,th,22)
        cb.alpha_composite(img.resize((tw,th),RESAMPLE))
        sheet.paste(cb.convert('RGB'),(x,y+26))
        d.text((x+12,y+8),f'SEED {seed} | borda -{len(rec["removed"])} | vazios {len(rec["holes"])}',fill='#15243a')
    d.text((12,sheet.height-19),'Transparência real = quadriculado cinza. Facetas = polígonos completos.',fill='#42546d')
    sheet.save(path)


def diagrams(w,h,cells,protected,folder,template):
    for name,draw_focus in [('geometry-white-grid.png',False),('geometry-focus-map.png',True)]:
        im=Image.new('RGBA',(w,h),(0,0,0,0));d=ImageDraw.Draw(im)
        for k in sorted(cells):
            d.polygon(cells[k]['polygon'],fill=(212,233,251,255) if draw_focus and k in protected else (255,255,255,255))
        for k in sorted(cells):
            p=cells[k]['polygon'];d.line(p+[p[0]],fill=(74,96,145,160),width=2)
        im.save(folder/f'{template}-{name}')


def load_source(cfg,w,h,config_path):
    if cfg['mode']=='white':
        return np.asarray(Image.new('RGBA',(w,h),(255,255,255,255)),dtype=np.uint8),None
    file=Path(cfg['input'])
    if not file.is_absolute():file=config_path.parent/file
    if not file.is_file():raise FileNotFoundError(file)
    with Image.open(file) as im:
        src=ImageOps.fit(im.convert('RGBA'),(w,h),method=RESAMPLE,centering=(.5,.5))
    return np.asarray(src,dtype=np.uint8),str(file)


def run(cfg,config_path):
    check_config(cfg)
    out=cfg['output'];w=int(out['work_width']);h=round(w*4/5)
    folder=Path(out['directory'])
    if not folder.is_absolute():folder=config_path.parent/folder
    folder.mkdir(parents=True,exist_ok=True)
    cells=cells_in_canvas(w,h,cfg)
    if not cells:
        raise ValueError('Nenhuma faceta dentro da silhueta: reduza as dimensões')
    neighbors=adjacency(cells)
    connected_start=connected(set(cells),neighbors)
    if not connected_start and not cfg['silhouette']['allow_detached_facets']:
        raise ValueError('A malha não é conectada; template livre pode precisar de polígonos que tessellem ou allow_detached_facets=true')
    protected=protected_cells(cells,w,h,cfg)
    source,inp=load_source(cfg,w,h,config_path)
    template=cfg['geometry'].get('template','rhombus')
    if out['save_geometry_diagram']:
        diagrams(w,h,cells,protected,folder,template)
    outputs=[]
    for it in range(int(cfg['variations']['count'])):
        seed=int(cfg['variations']['seed'])+it
        rng=np.random.default_rng(seed)
        selected,removed=prune_cells(cells,protected,neighbors,cfg,rng)
        holes,dark,light=choose_states(selected,protected,neighbors,cells,cfg,rng)
        img=render(cfg,cells,selected,holes,dark,light,protected,source,seed,w,h)
        stem=f'{cfg["name"]}-{template}-seed-{seed:06d}'
        img.save(folder/f'{stem}.png',optimize=True)
        if out['webp']:
            img.save(folder/f'{stem}.webp',lossless=True,method=6)
        record={'version':3,'seed':seed,'mode':cfg['mode'],'input':inp,
                'input_sha256':hashlib.sha256(Path(inp).read_bytes()).hexdigest() if inp else None,
                'template':cfg['geometry'].get('template','rhombus'),
                'geometry_size':selected_shape_dimensions(cfg['geometry']) or {'unit':'relative','width':cfg['geometry'].get('horizontal_diagonal'),'height':cfg['geometry'].get('vertical_diagonal')},
                'selected_count':len(selected),'protected_count':len(protected),
                'removed':[list(i) for i in removed],'holes':[list(i) for i in holes],
                'dark':[list(i) for i in dark],'light':[list(i) for i in light]}
        (folder/f'{stem}.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
        outputs.append((img,seed,record))
        print(f'{stem} [{record["template"]}]: {len(selected)} facetas, {len(removed)} removidas, {len(holes)} vazadas',flush=True)
    if out['contact_sheet']:
        contact_sheet(outputs,folder/f'{cfg["name"]}-{template}-comparativo.png')
    print('Concluído:',folder,flush=True)
    return folder


def batch_run(cfg,config_path):
    batch=cfg.get('batch',{})
    input_directory=Path(batch.get('input_directory','.'))
    if not input_directory.is_absolute():input_directory=config_path.parent/input_directory
    input_directory=input_directory.resolve()
    if not input_directory.is_dir():raise NotADirectoryError(input_directory)
    output_directory=Path(cfg['output']['directory'])
    if not output_directory.is_absolute():output_directory=config_path.parent/output_directory
    output_directory=output_directory.resolve()
    if output_directory==input_directory:
        raise ValueError('Diretório de saída precisa ser diferente do diretório das imagens')
    images=sorted((p for p in input_directory.iterdir() if p.is_file() and p.suffix.lower()=='.png'),key=lambda p:p.name.casefold())
    if not images:
        print('Nenhuma imagem PNG em',input_directory);return 0
    print(f'Processamento em lote: {len(images)} PNGs')
    errors=[]
    for num,file in enumerate(images,1):
        print(f'[{num}/{len(images)}] {file.name}',flush=True)
        individual=copy.deepcopy(cfg);individual['mode']='artwork'
        individual['input']=str(file); individual['name']=file.stem
        individual['output']['directory']=str(output_directory/file.stem)
        if batch.get('seed_per_image',True):
            normalized=unicodedata.normalize('NFC',file.stem).casefold()
            digest=hashlib.sha256(normalized.encode('utf-8')).digest()
            offset=int.from_bytes(digest[:4],'big')%10_000_000
            individual['variations']['seed']=int(cfg['variations']['seed'])+offset
        try:run(individual,config_path)
        except Exception as error:
            errors.append((file.name,str(error)))
            print('ERRO:',file.name,error,file=sys.stderr)
            if not batch.get('continue_on_error',True):raise
    print(f'Concluído: {len(images)-len(errors)} arquivos, {len(errors)} erros')
    return len(errors)


def main():
    p=argparse.ArgumentParser(description='Filtro procedural JulioOS: geometrias e dimensões editáveis por config.json')
    p.add_argument('--config',type=Path,default=Path('config.json'))
    p.add_argument('--template',choices=TEMPLATES,help='Seleciona geometria sobrepondo o JSON')
    p.add_argument('--cell-width',type=float,help='Largura de cada célula (conforme --unit)')
    p.add_argument('--cell-height',type=float,help='Altura de cada célula (conforme --unit)')
    p.add_argument('--unit',choices=['px','relative'],help='Unidade das dimensões (px ou fraction da imagem)')
    p.add_argument('--seed',type=int)
    p.add_argument('--variants',type=int)
    group=p.add_mutually_exclusive_group()
    group.add_argument('--input',type=Path,help='Processa uma imagem')
    group.add_argument('--input-dir',type=Path,help='Diretório de imagens PNG em lote')
    group.add_argument('--white',action='store_true',help='Teste sobre fundo branco')
    group.add_argument('--batch',action='store_true',help='Força modo lote')
    args=p.parse_args()
    path=args.config.resolve()
    cfg=json.loads(path.read_text(encoding='utf-8'))
    if args.template is not None:cfg['geometry']['template']=args.template
    if args.cell_width is not None or args.cell_height is not None or args.unit is not None:
        template=cfg['geometry'].get('template','rhombus')
        size=selected_shape_dimensions(cfg['geometry'])
        if size is None:
            size={'unit':'relative','width':cfg['geometry']['horizontal_diagonal'],
                  'height':cfg['geometry']['vertical_diagonal']}
        size=copy.deepcopy(size)
        if args.cell_width is not None:size['width']=args.cell_width
        if args.cell_height is not None:size['height']=args.cell_height
        if args.unit is not None:size['unit']=args.unit
        cfg['geometry']['size']=size
    if args.seed is not None:cfg['variations']['seed']=args.seed
    if args.variants is not None:cfg['variations']['count']=args.variants
    if args.input is not None:
        cfg['mode']='artwork';cfg['input']=str(args.input.resolve())
        cfg.setdefault('batch',{})['enabled']=False
    elif args.white:
        cfg['mode']='white';cfg['input']=None
        cfg.setdefault('batch',{})['enabled']=False
    elif args.batch or args.input_dir is not None:
        cfg.setdefault('batch',{})['enabled']=True
        if args.input_dir is not None:
            cfg['batch']['input_directory']=str(args.input_dir.resolve())
    if cfg.get('batch',{}).get('enabled',False):
        errors=batch_run(cfg,path)
        if errors:raise SystemExit(1)
    else:run(cfg,path)

if __name__=='__main__':
    main()

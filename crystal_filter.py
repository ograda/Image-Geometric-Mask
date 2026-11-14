#!/usr/bin/env python3
"""JulioOS Crystal Filter — configuração consolidada, templates e processamento em lote."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import secrets
import sys
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps

RESAMPLE = getattr(Image, 'Resampling', Image).LANCZOS
TEMPLATES = ('rhombus', 'square', 'rectangle', 'triangle', 'hexagon', 'free')
MAX_SEED = 2**64 - 1

def selected_shape_dimensions(geometry):
    """Retorna dimensões da forma selecionada; mantém suporte a config V3."""
    template = geometry.get('template', 'rhombus')
    return (geometry.get('size')
            or geometry.get('shapes', {}).get(template)
            or geometry.get('sizes', {}).get(template))



def check_config(cfg):
    """Valida a configuração consolidada antes de processar as imagens."""
    if cfg.get('mode', 'artwork') not in ('white', 'artwork'):
        raise ValueError('mode deve ser white ou artwork')
    out = cfg['output']
    final = out['resize']
    work = cfg['work_size']
    for name, part in (('output.resize', final), ('work_size', work)):
        if not isinstance(part.get('resize'), bool):
            raise ValueError(f'{name}.resize deve ser true ou false')
        if int(part['width']) < 1 or int(part['height']) < 1:
            raise ValueError(f'{name}.width e height precisam ser positivos')
    if int(out['count']) < 1:
        raise ValueError('output.count deve ser >= 1')
    types = out.get('image_types', {})
    if 'jpge' in types:
        raise ValueError('Use output.image_types.jpeg (não jpge)')
    if not any(types.get(ext, False) for ext in ('png', 'webp', 'jpeg')):
        raise ValueError('Ative pelo menos um formato em output.image_types')
    pagesize = int(out.get('contact_sheet_page_size', 12))
    if not 1 <= pagesize <= 40:
        raise ValueError('output.contact_sheet_page_size deve estar entre 1 e 40')

    g = cfg['geometry']
    template = g.get('template', 'rhombus')
    if template not in TEMPLATES:
        raise ValueError('geometry.template inválido')
    if not 0 <= float(g.get('canvas_margin', 0)) < 0.5:
        raise ValueError('geometry.canvas_margin deve estar entre 0 e 0.5')
    size = selected_shape_dimensions(g)
    if size is None:
        raise ValueError('Defina geometry.shapes.<template>')
    if template == 'free':
        opts = size
        polys = opts.get('polygons', [opts['vertices']] if 'vertices' in opts else [])
        if not polys or any(len(p) < 3 for p in polys):
            raise ValueError('geometry.shapes.free.polygons precisa de polígonos com >= 3 vértices')
        if float(opts.get('step_x', 1)) <= 0 or float(opts.get('step_y', 1)) <= 0:
            raise ValueError('geometry.shapes.free.step_x/step_y devem ser > 0')
    if template == 'square':
        if float(size.get('side', size.get('width', 0))) <= 0:
            raise ValueError('geometry.shapes.square.side deve ser > 0')
    else:
        # Forma livre sem largura/altura usa as dimensões dos losangos como padrão.
        chosen = size if template != 'free' or ('width' in size and 'height' in size) else g.get('shapes', {}).get('rhombus', {})
        if float(chosen.get('width', 0)) <= 0 or float(chosen.get('height', 0)) <= 0:
            raise ValueError('geometry.shapes: width e height devem ser > 0')
    if template == 'free':
        u = size.get('unit', g.get('shapes', {}).get('rhombus', {}).get('unit', 'px'))
    else:
        u = size.get('unit', 'px')
    if u not in ('px', 'relative'):
        raise ValueError('geometry.shapes.<template>.unit deve ser px ou relative')

    sil = cfg['silhouette']
    if sil['shape'] != 'crystal_superellipse':
        raise ValueError('silhouette.shape inválida')
    if any(float(sil[k][axis]) <= 0 for k in ('radius', 'power') for axis in ('x', 'y')):
        raise ValueError('silhouette.radius e silhouette.power devem ser positivos')
    prune = sil['outer_prune']
    if not 0 <= int(prune['min']) <= int(prune['max']):
        raise ValueError('silhouette.outer_prune: min/max inválidos')

    focus = cfg['protected_areas']
    if float(focus['safety_margin']) < 0:
        raise ValueError('protected_areas.safety_margin deve ser >= 0')
    if not focus['ellipses']:
        raise ValueError('protected_areas.ellipses não pode ser vazio')
    for cx, cy, rx, ry in focus['ellipses']:
        if not (0 <= cx <= 1 and 0 <= cy <= 1 and 0 < rx <= 1 and 0 < ry <= 1):
            raise ValueError('Elipses de protected_areas inválidas')

    f = cfg['facets']
    if any(int(f[k]) < 0 for k in ('holes', 'dark', 'light', 'minimum_hole_separation_cells')):
        raise ValueError('Contagens de facetas devem ser >= 0')
    lines = f['geometry']['lines']
    if not (0 <= float(lines['opacity']) <= 1):
        raise ValueError('facets.geometry.lines.opacity deve estar entre 0 e 1')
    if float(lines['width']) <= 0:
        raise ValueError('facets.geometry.lines.width deve ser positivo')
    if len(lines['color']) != 3 or any(not 0 <= channel <= 255 for channel in lines['color']):
        raise ValueError('facets.geometry.lines.color precisa conter três valores RGB')
    if float(cfg['finish']['grain']) < 0:
        raise ValueError('finish.grain deve ser >= 0')


def validate_seed(seed):
    if seed is None:
        return secrets.randbits(64)
    if not 0<=seed<=MAX_SEED:
        raise ValueError('Seed deve estar entre 0 e 2^64-1')
    return seed


def stable_seed(root, name):
    data=f'{root}|{name}'.encode('utf-8')
    return int.from_bytes(hashlib.sha256(data).digest()[:8],'big')


def normalized_source_size(cfg,path, preview=False):
    work=cfg['work_size']
    if preview or work['resize']:
        return int(work['width']),int(work['height'])
    with Image.open(path) as im:
        return im.size


def finalize_rgba(rgb,alpha,cfg):
    src_h,src_w=alpha.shape
    final=cfg['output']['resize']
    if final['resize']:
        tw,th=int(final['width']),int(final['height'])
    else:
        tw,th=src_w,src_h
    if (src_w,src_h)==(tw,th):
        return Image.fromarray(np.uint8(np.clip(np.rint(np.dstack((rgb,alpha*255))),0,255)),'RGBA')
    # Crop central: mantém proporção sem deformar quando o alvo possui outra proporção.
    current=src_w/src_h; target=tw/th
    if abs(current-target)>1e-8:
        if current>target:
            crop_w=max(1,round(src_h*target));left=(src_w-crop_w)//2
            rgb=rgb[:,left:left+crop_w,:];alpha=alpha[:,left:left+crop_w]
        else:
            crop_h=max(1,round(src_w/target));top=(src_h-crop_h)//2
            rgb=rgb[top:top+crop_h,:,:];alpha=alpha[top:top+crop_h,:]
    return resize_premultiplied(rgb,alpha,(tw,th))

def dimensions(w, h, cfg):
    """Dimensões em px; square usa side, free pode herdar rhombus."""
    g = cfg['geometry']
    template = g.get('template', 'rhombus')
    size = selected_shape_dimensions(g)
    if size is None:
        raise ValueError(f'Sem dimensões para o template {template}')
    if template == 'square':
        width = height = float(size.get('side', size.get('width')))
    elif template == 'free' and not ('width' in size and 'height' in size):
        # Compatível com free composto apenas por step_x/step_y/polygons.
        # Para mudar a escala da forma livre, acrescente width/height/unit ao bloco free.
        fallback = g.get('shapes', {}).get('rhombus', {})
        width, height = float(fallback['width']), float(fallback['height'])
        size = {'unit': fallback.get('unit', 'px')}
    else:
        width, height = float(size['width']), float(size['height'])
    if size.get('unit', 'px') == 'relative':
        if template == 'square':
            return min(w, h) * width, min(w, h) * height
        return w * width, h * height
    return width, height


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
    ox, oy = w * float(g.get('offset_x', 0)), h * float(g.get('offset_y', 0))
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
        opts = g['shapes']['free']
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
    margin = float(cfg['geometry']['canvas_margin'])
    sh = cfg['silhouette']
    cells = {}
    for key, poly in candidate_polygons(w,h,cfg).items():
        if any(x < w*margin or x > w*(1-margin) or y < h*margin or y > h*(1-margin)
               for x,y in poly):
            continue
        cx, cy = polygon_center(poly)
        nx = abs(cx/w - .5) / float(sh['radius']['x'])
        ny = abs(cy/h - .5) / float(sh['radius']['y'])
        if nx**float(sh['power']['x']) + ny**float(sh['power']['y']) > 1.0:
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
    m=float(cfg['protected_areas']['safety_margin'])
    for cx,cy,rx,ry in cfg['protected_areas']['ellipses']:
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
    count=int(rng.integers(int(sil['outer_prune']['min']),int(sil['outer_prune']['max'])+1))
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
    rng=np.random.default_rng((seed+97610)&MAX_SEED)
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
        for cx,cy,rx,ry in cfg['protected_areas']['ellipses']:
            inside=((X/w-cx)/rx)**2+((Y/h-cy)/ry)**2<=1
            strength[inside]=.25
        gray=(rgb[:,:,0]*.2126+rgb[:,:,1]*.7152+rgb[:,:,2]*.0722)[:,:,None]
        rgb=gray+(rgb-gray)*finish['saturation']
        rgb=(rgb-127.5)*finish['contrast']+127.5
        rgb*=1+modifiers[ids][:,:,None]*strength[:,:,None]
    lines = fac['geometry']['lines']
    opacity = float(lines['opacity'])
    if cfg['mode'] == 'white' and not debug.get('draw_grid', True):
        opacity = 0.0
    width = float(lines['width'])
    line_color = lines['color']
    line=grid_lines(w,h,selected,cells,width,opacity)
    rgb=rgb*(1-line[:,:,None])+np.array(line_color,dtype=np.float32)*line[:,:,None]
    if cfg['mode']=='artwork' and finish['grain']>0:
        rgb+=rng.normal(0,finish['grain'],(h,w,1)).astype(np.float32)
    alpha=alpha_mask(w,h,selected,cells,holes)
    alpha*=source[:,:,3].astype(np.float32)/255.0
    if set(holes)&protected:
        raise RuntimeError('Região protegida interceptada por furo')
    return finalize_rgba(np.clip(rgb,0,255),alpha,cfg)


def checkerboard(w,h,unit=20):
    y,x=np.mgrid[:h,:w]
    v=np.where(((x//unit+y//unit)%2)==0,243,218).astype(np.uint8)
    return Image.fromarray(np.stack([v,v,v],axis=2),'RGB').convert('RGBA')


def contact_sheet(outputs,path):
    """Renderiza uma página, composta apenas por miniaturas (evita reter full-res)."""
    tw,th=375,300
    cols=min(3,len(outputs)); rows=math.ceil(len(outputs)/cols)
    sheet=Image.new('RGB',(cols*tw,rows*(th+45)+24),'#eef1f6')
    d=ImageDraw.Draw(sheet)
    for pos,(thumb,seed,record) in enumerate(outputs):
        x=(pos%cols)*tw; y=(pos//cols)*(th+45)
        cb=checkerboard(tw,th,22)
        cb.alpha_composite(thumb)
        sheet.paste(cb.convert('RGB'),(x,y+26))
        d.text((x+12,y+8),f'SEED {seed} | borda -{len(record["removed"])} | vazios {len(record["holes"])}',fill='#15243a')
    d.text((12,sheet.height-19),'Xadrez cinza = transparência real.',fill='#42546d')
    sheet.save(path)

def diagrams(w,h,cells,protected,folder,template,cfg):
    """Diagramas de debug usando a mesma linha configurada para os filtros."""
    lines = cfg['facets']['geometry']['lines']
    opacity = float(lines['opacity']) if cfg['debug_white'].get('draw_grid', True) else 0.0
    line_rgb = tuple(int(x) for x in lines['color'])
    line_a = round(255 * opacity)
    width = max(1, round(float(lines['width'])))
    for name,draw_focus in [('geometry-white-grid.png',False),('geometry-focus-map.png',True)]:
        im=Image.new('RGBA',(w,h),(0,0,0,0));d=ImageDraw.Draw(im)
        for k in sorted(cells):
            d.polygon(cells[k]['polygon'],fill=(212,233,251,255) if draw_focus and k in protected else (255,255,255,255))
        if line_a:
            for k in sorted(cells):
                p=cells[k]['polygon']
                d.line(p+[p[0]],fill=(*line_rgb,line_a),width=width)
        im.save(folder/f'{template}-{name}')


def load_source(cfg,w,h,file=None):
    if cfg['mode']=='white':
        return np.asarray(Image.new('RGBA',(w,h),(255,255,255,255)),dtype=np.uint8),None
    if file is None or not file.is_file():
        raise FileNotFoundError(file)
    with Image.open(file) as im:
        image=im.convert('RGBA')
        if cfg['work_size']['resize']:
            image=ImageOps.fit(image,(w,h),method=RESAMPLE,centering=(.5,.5))
        elif image.size!=(w,h):
            raise RuntimeError('Dimensão inconsistente entre origem e work_size')
    return np.asarray(image,dtype=np.uint8),str(file.resolve())


def save_image(img,folder,stem,types):
    if types.get('png',False):
        img.save(folder/f'{stem}.png',optimize=True)
    if types.get('webp',False):
        img.save(folder/f'{stem}.webp',lossless=True,method=6)
    if types.get('jpeg',False):
        # JPEG não suporta transparência: compõe o resultado sobre branco.
        background=Image.new('RGB',img.size,'white')
        background.paste(img,mask=img.getchannel('A'))
        background.save(folder/f'{stem}.jpg',quality=95,subsampling=0)


def run(cfg,config_path,root_seed,source_path=None,preview=False,folder_override=None):
    mode='white' if preview else 'artwork'
    cfg=copy.deepcopy(cfg);cfg['mode']=mode
    w,h=normalized_source_size(cfg,source_path,preview)
    if w<100 or h<100:
        raise ValueError('Resolução de trabalho deve ter pelo menos 100 px por eixo')
    folder=(folder_override if folder_override is not None else Path(cfg['output']['directory']))
    if not folder.is_absolute(): folder=config_path.parent/folder
    folder.mkdir(parents=True,exist_ok=True)
    cells=cells_in_canvas(w,h,cfg)
    if not cells:raise ValueError('Nenhuma faceta dentro da silhueta')
    neighbors=adjacency(cells)
    if not connected(set(cells),neighbors) and not cfg['silhouette']['allow_detached_facets']:
        raise ValueError('A malha não é conectada; corrija a tesselação livre ou habilite facetas soltas')
    protected=protected_cells(cells,w,h,cfg)
    source,input_name=load_source(cfg,w,h,source_path)
    template=cfg['geometry']['template']
    source_stem=source_path.stem if source_path else 'white'
    name=f'{template}-{source_stem}'
    if preview:
        diagrams(w,h,cells,protected,folder,template,cfg)
    types=cfg['output']['image_types']
    count=int(cfg['output']['count'])
    sheet_enabled=cfg['output'].get('contact_sheet',False)
    sheet_size=int(cfg['output'].get('contact_sheet_page_size',12))
    pages=math.ceil(count/sheet_size)
    thumbnails=[]
    input_sha=hashlib.sha256(source_path.read_bytes()).hexdigest() if source_path else None
    print(f'[{source_stem}] {count} variações; seed base = {root_seed}; {w}x{h} de trabalho',flush=True)
    for idx in range(count):
        seed=(root_seed+idx)&MAX_SEED
        rng=np.random.default_rng(seed)
        selected,removed=prune_cells(cells,protected,neighbors,cfg,rng)
        holes,dark,light=choose_states(selected,protected,neighbors,cells,cfg,rng)
        img=render(cfg,cells,selected,holes,dark,light,protected,source,seed,w,h)
        stem=f'{name}-seed-{seed:020d}'
        save_image(img,folder,stem,types)
        record={'version':cfg.get('version',4),'seed':seed,'root_seed':root_seed,'mode':mode,
                'input':input_name,'input_sha256':input_sha,'template':template,
                'geometry_size':selected_shape_dimensions(cfg['geometry']),
                'selected_count':len(selected),'protected_count':len(protected),
                'removed':[list(k) for k in removed],
                'holes':[list(k) for k in holes],
                'dark':[list(k) for k in dark],
                'light':[list(k) for k in light]}
        (folder/f'{stem}.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
        if sheet_enabled:
            thumbnails.append((img.resize((375,300),RESAMPLE),seed,record))
            if len(thumbnails)>=sheet_size or idx==count-1:
                page_number=idx//sheet_size+1
                suffix=f'-{page_number:02d}' if pages>1 else ''
                contact_sheet(thumbnails,folder/f'{name}-comparativo{suffix}.png')
                thumbnails.clear()
        if idx%10==0 or idx==count-1:
            print(f'  {idx+1}/{count} | seed {seed}',flush=True)
    return folder


def batch_run(cfg,config_path,root_seed,input_dir=None):
    batch=cfg.get('batch',{})
    inp=Path(input_dir) if input_dir is not None else Path(batch.get('input_directory','.'))
    if not inp.is_absolute():inp=config_path.parent/inp
    inp=inp.resolve()
    if not inp.is_dir(): raise NotADirectoryError(inp)
    out=Path(cfg['output']['directory'])
    if not out.is_absolute():out=config_path.parent/out
    out=out.resolve()
    if out==inp:raise ValueError('Diretório de saída precisa ser diferente do diretório de entrada')
    inputs=sorted((p for p in inp.iterdir() if p.is_file() and p.suffix.lower()=='.png'),key=lambda p:p.name.casefold())
    if not inputs:
        print('Nenhuma imagem PNG encontrada em',inp);return 0
    errors=[]
    print(f'Processamento em lote: {len(inputs)} PNG(s). Seed da execução: {root_seed}',flush=True)
    for i,path in enumerate(inputs,1):
        print(f'[{i}/{len(inputs)}] {path.name}',flush=True)
        file_seed=stable_seed(root_seed,path.name)
        try:
            run(cfg,config_path,file_seed,path,folder_override=out/path.stem)
        except Exception as exc:
            errors.append((path.name,str(exc)))
            print(f'ERRO {path.name}: {exc}',file=sys.stderr)
            if not batch.get('continue_on_error',True):raise
    print(f'Concluído: {len(inputs)-len(errors)} arquivo(s), {len(errors)} erro(s).')
    return len(errors)


def load_config(config_path):
    try:return json.loads(config_path.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:raise SystemExit(f'Config não encontrado: {config_path}')
    except json.JSONDecodeError as exc:
        lines=exc.doc.splitlines(); lo=max(1,exc.lineno-3);hi=min(len(lines),exc.lineno+3)
        ctxt='\n'.join(f'{i:4} | {lines[i-1]}' for i in range(lo,hi+1))
        raise SystemExit(f'JSON inválido: {config_path}\nLinha {exc.lineno}, coluna {exc.colno}: {exc.msg}\n{ctxt}') from None



def resolve_single_image(value: Path, config_path: Path, cfg: dict, input_dir: Path | None = None) -> Path:
    """Resolve --single by explicit PNG path or by PNG stem (case insensitive)."""
    # Try the path as supplied, then the configured input folder.
    base = input_dir if input_dir is not None else Path(cfg.get('batch', {}).get('input_directory', '.'))
    if not base.is_absolute():
        base = config_path.parent / base
    directories = [Path.cwd()]
    if base.resolve() not in [d.resolve() for d in directories]:
        directories.append(base)
    candidates = []
    has_png_ext = value.suffix.lower() == '.png'
    for directory in directories:
        lookup = value if value.is_absolute() else directory / value
        # Exact file path always takes precedence over stem search.
        if lookup.is_file() and lookup.suffix.lower() == '.png':
            return lookup.resolve()
        parent = lookup.parent
        if not parent.is_dir():
            continue
        stem = lookup.stem if has_png_ext else lookup.name
        for file in parent.iterdir():
            if file.is_file() and file.suffix.lower() == '.png' and file.stem.casefold() == stem.casefold():
                path = file.resolve()
                if path not in candidates:
                    candidates.append(path)
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise FileNotFoundError(f'PNG não encontrado para --single {str(value)!r}. Procure em: {", ".join(map(str, directories))}')
    raise ValueError(f'Nome ambíguo para --single {str(value)!r}: {", ".join(map(str, candidates))}. Informe o caminho completo.')


def main():
    p=argparse.ArgumentParser(description='JulioOS Crystal Filter: mosaicos de polígonos, variações e lote')
    p.add_argument('--config',type=Path,default=Path('config.json'))
    group=p.add_mutually_exclusive_group()
    group.add_argument('--debug',action='store_true',dest='preview',help='Gera testes sobre fundo branco e diagramas de foco/malha')
    group.add_argument('--single',type=Path,metavar='IMAGE',help='Processa um PNG por nome (sem .png) ou caminho')
    group.add_argument('--batch',action='store_true',help='Processa todos os PNGs da pasta de entrada')
    p.add_argument('--input-dir',type=Path,help='Pasta de entrada para lote')
    p.add_argument('--seed',type=int,help='Seed fixa de 0 a 2^64-1; se omitida, gera seed aleatória')
    p.add_argument('--count','--variants',type=int,help='Número de variações, substitui output.count')
    p.add_argument('--template',choices=TEMPLATES)
    p.add_argument('--cell-width',type=float)
    p.add_argument('--cell-height',type=float)
    p.add_argument('--unit',choices=['px','relative'])
    args=p.parse_args()
    path=args.config.resolve();cfg=load_config(path)
    if args.template is not None:cfg['geometry']['template']=args.template
    if args.cell_width is not None or args.cell_height is not None or args.unit is not None:
        template=cfg['geometry']['template']
        size=copy.deepcopy(selected_shape_dimensions(cfg['geometry']))
        if template == 'square':
            if args.cell_width is not None and args.cell_height is not None and args.cell_width != args.cell_height:
                raise SystemExit('ERRO: quadrado exige --cell-width e --cell-height iguais')
            side = args.cell_width if args.cell_width is not None else args.cell_height
            if side is not None: size['side'] = side
        else:
            if args.cell_width is not None: size['width']=args.cell_width
            if args.cell_height is not None: size['height']=args.cell_height
            if template == 'free' and ('width' in size) != ('height' in size):
                fallback=cfg['geometry']['shapes']['rhombus']
                size.setdefault('width', fallback['width'])
                size.setdefault('height', fallback['height'])
        if args.unit is not None: size['unit']=args.unit
        cfg['geometry']['size']=size
    if args.count is not None:cfg['output']['count']=args.count
    check_config(cfg)
    root_seed=validate_seed(args.seed)
    try:
        if args.preview or (cfg.get('mode')=='white' and args.single is None and not args.batch and args.input_dir is None):
            folder=run(cfg,path,root_seed,preview=True,folder_override=Path(cfg['output']['directory'])/'_preview')
            print('Prévia:',folder)
        elif args.single is not None:
            source=resolve_single_image(args.single,path,cfg,args.input_dir)
            out=Path(cfg['output']['directory'])
            folder=run(cfg,path,root_seed,source_path=source,folder_override=out/source.stem)
            print('Saída:',folder)
        else:
            errors=batch_run(cfg,path,root_seed,args.input_dir)
            if errors:raise SystemExit(1)
    except (ValueError,FileNotFoundError,NotADirectoryError) as exc:
        raise SystemExit(f'ERRO: {exc}') from None

if __name__=='__main__':
    main()

#!/usr/bin/env python3
"""JulioOS crystal filter v2 — geometria fixa, silhueta variável por células.

Dependências: pillow, numpy. TODAS as opções permanentes ficam em config.json.
A entrada pode ser uma imagem artística ou uma imagem branca gerada pelo script.
A imagem original nunca é alterada.
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

try:
    RESAMPLE = Image.Resampling.LANCZOS
except AttributeError:
    RESAMPLE = Image.LANCZOS


def check_config(cfg):
    mode = cfg['mode']
    assert mode in ('white', 'artwork'), "mode deve ser white ou artwork"
    out = cfg['output']
    w, h = int(out['width']), int(out['height'])
    assert w >= 100 and h >= 100 and w * 4 == h * 5, 'A saida precisa ser 5:4'
    assert int(out['work_width']) >= w
    assert int(cfg['variations']['count']) > 0
    s = cfg['silhouette']
    assert s['shape'] == 'crystal_superellipse'
    assert 0 <= s['outer_prune_min'] <= s['outer_prune_max']
    f = cfg['facets']
    for key in ('holes', 'dark', 'light'):
        assert isinstance(f[key], int) and f[key] >= 0
    assert cfg['focus']['ellipses'], 'Defina ao menos uma elipse focal'
    for cx,cy,rx,ry in cfg['focus']['ellipses']:
        assert 0 <= cx <= 1 and 0 <= cy <= 1 and 0 < rx <= 1 and 0 < ry <= 1
    if mode == 'artwork':
        assert cfg['input'], 'Em mode=artwork, informe input com a imagem-base.'


def corners(k: int, j: int, w: int, h: int, cfg):
    g = cfg['geometry']
    dx,dy = w*g['horizontal_diagonal'],h*g['vertical_diagonal']
    ox,oy = w*g['origin_x'],h*g['origin_y']
    points = [(ox+(u+v)*dx/2,oy+(u-v)*dy/2) for u,v in
              ((k,j),(k+1,j),(k+1,j+1),(k,j+1))]
    return points


def mid(poly):
    return (sum(x for x,y in poly)/4,sum(y for x,y in poly)/4)


def cells_in_canvas(w,h,cfg):
    g = cfg['geometry']
    dx,dy = w*g['horizontal_diagonal'],h*g['vertical_diagonal']
    ox,oy=w*g['origin_x'],h*g['origin_y']
    uv = [((x-ox)/dx+(y-oy)/dy, (x-ox)/dx-(y-oy)/dy)
          for x in (-dx,w+dx) for y in (-dy,h+dy)]
    min_k = math.floor(min(x for x,y in uv))-2
    max_k = math.ceil(max(x for x,y in uv))+2
    min_j = math.floor(min(y for x,y in uv))-2
    max_j = math.ceil(max(y for x,y in uv))+2
    margin = g['edge_margin']
    shape = cfg['silhouette']
    ret={}
    for k in range(min_k,max_k+1):
        for j in range(min_j,max_j+1):
            poly=corners(k,j,w,h,cfg)
            # Todos os vértices dentro do canvas. Losangos INTEIROS.
            if any(x < w*margin or x > w*(1-margin) or
                   y < h*margin or y > h*(1-margin) for x,y in poly):
                continue
            cx,cy=mid(poly)
            nx=abs(cx/w-.5)/shape['radius_x']
            ny=abs(cy/h-.5)/shape['radius_y']
            if nx**shape['power_x']+ny**shape['power_y'] > 1.0:
                continue
            ret[(k,j)] = {'polygon': poly, 'center':(cx,cy)}
    return ret


def neighbors(key):
    k,j=key
    return ((k-1,j),(k+1,j),(k,j-1),(k,j+1))


def connected(keys):
    if not keys: return False
    q=deque([next(iter(keys))]); visited={q[0]}
    while q:
        for t in neighbors(q.popleft()):
            if t in keys and t not in visited:
                visited.add(t);q.append(t)
    return len(keys)==len(visited)


def polygon_bounds_mask(poly,w,h):
    x0=max(0,math.floor(min(p[0] for p in poly))-2)
    y0=max(0,math.floor(min(p[1] for p in poly))-2)
    x1=min(w,math.ceil(max(p[0] for p in poly))+3)
    y1=min(h,math.ceil(max(p[1] for p in poly))+3)
    if x0>=x1 or y0>=y1:return None,None
    m=Image.new('L',(x1-x0,y1-y0),0)
    ImageDraw.Draw(m).polygon([(x-x0,y-y0) for x,y in poly],fill=255)
    return (slice(y0,y1),slice(x0,x1)),np.asarray(m)>0


def protected_cells(cells, w,h,cfg):
    mask=Image.new('L',(w,h),0)
    d=ImageDraw.Draw(mask)
    margin=cfg['focus']['safety_margin']
    for cx,cy,rx,ry in cfg['focus']['ellipses']:
        d.ellipse(((cx-rx-margin)*w,(cy-ry-margin)*h,
                   (cx+rx+margin)*w,(cy+ry+margin)*h), fill=255)
    mask=np.asarray(mask)>0
    protected=set()
    for key,c in cells.items():
        b,m=polygon_bounds_mask(c['polygon'],w,h)
        if b is not None and np.any(mask[b][m]):
            protected.add(key)
    return protected


def trim_silhouette(cells, protected, cfg, rng):
    selected=set(cells)
    sil=cfg['silhouette']
    count=int(rng.integers(sil['outer_prune_min'],sil['outer_prune_max']+1))
    removed=[]
    for _ in range(count):
        exposed=[key for key in sorted(selected)
                 if key not in protected and any(n not in selected for n in neighbors(key))]
        rng.shuffle(exposed)
        # Dê leve preferência às pontas; não quebre a conectividade.
        exposed.sort(key=lambda c: sum(n not in selected for n in neighbors(c)),reverse=True)
        chosen=None
        for key in exposed:
            test=selected-{key}
            if sil['allow_detached_facets'] or connected(test):
                chosen=key; break
        if chosen is None:break
        selected.remove(chosen); removed.append(chosen)
    return selected,removed


def choose_states(selected, protected, cfg,rng):
    f=cfg['facets']
    # Furo somente em uma célula interna que tenha os quatro vizinhos visíveis.
    # Garante que os furos sejam independentes das bordas e não cortem o foco.
    holes_candidates=[key for key in sorted(selected) if key not in protected
                      and all(n in selected for n in neighbors(key))]
    holes=[]
    while len(holes)<f['holes'] and holes_candidates:
        rng.shuffle(holes_candidates)
        key=holes_candidates[0];holes.append(key)
        d=f['minimum_hole_separation_cells']
        holes_candidates=[p for p in holes_candidates[1:]
                          if abs(p[0]-key[0])+abs(p[1]-key[1])>d]
    if len(holes) < f['holes']:
        raise ValueError(f"Apenas {len(holes)} furos possíveis com o foco atual; "
                         'reduza facets.holes ou safety_margin.')
    # Exposições exclusivas: uma faceta não pode ser clara e escura.
    remainder=sorted(selected-set(holes))
    rng.shuffle(remainder)
    if len(remainder)<f['dark']+f['light']:
        raise ValueError('Facetas insuficientes para dark + light')
    dark=remainder[:f['dark']]
    light=remainder[f['dark']:f['dark']+f['light']]
    return holes,dark,light


def make_alpha(w,h,selected,cells,holes):
    s=2
    im=Image.new('L',(w*s,h*s),0)
    d=ImageDraw.Draw(im)
    for key in sorted(selected):
        points=cells[key]['polygon']
        d.polygon([(round(x*s),round(y*s)) for x,y in points],fill=255)
    for key in holes:
        points=cells[key]['polygon']
        d.polygon([(round(x*s),round(y*s)) for x,y in points],fill=0)
    return np.asarray(im.resize((w,h),RESAMPLE),dtype=np.float32)/255.0


def lattice_idx(w,h,cfg):
    g=cfg['geometry']
    y,x=np.mgrid[:h,:w].astype(np.float32)
    ox,oy=w*g['origin_x'],h*g['origin_y']
    dx,dy=w*g['horizontal_diagonal'],h*g['vertical_diagonal']
    u=(x-ox)/dx+(y-oy)/dy
    v=(x-ox)/dx-(y-oy)/dy
    return np.floor(u).astype(np.int16),np.floor(v).astype(np.int16),u,v


def resize_unmatted(rgb,alpha,size):
    # Premultiply before resize; prevents halos at transparency boundaries.
    def down(ch):
        return np.asarray(Image.fromarray(ch.astype('float32'),'F').resize(size,RESAMPLE),dtype=np.float32)
    aa=np.clip(down(alpha),0,1)
    rgbout=[]
    for i in range(3):
        p=down(rgb[:,:,i]*alpha)
        rgbout.append(np.divide(p,aa,out=np.zeros_like(p),where=aa>1e-4))
    rgba=np.stack((*rgbout,aa*255),axis=2)
    return Image.fromarray(np.uint8(np.clip(np.rint(rgba),0,255)),'RGBA')


def render(cfg,cells,selected,holes,darks,lights,protection,source,seed,w,h):
    rng=np.random.default_rng(seed+97610)
    ik,ij,u,v=lattice_idx(w,h,cfg)
    sh=np.zeros((h,w),np.float32)
    rgb=source[:,:,:3].astype(np.float32)
    debug=cfg['debug_white'];finish=cfg['finish'];facet=cfg['facets']
    darks=set(darks);lights=set(lights)
    # Aplicar efeito por célula (sem alterar geometria).
    for key in selected:
        region=(ik==key[0]) & (ij==key[1])
        if not region.any():continue
        if cfg['mode']=='white':
            if key in darks:rgb[region]=debug['dark_color']
            elif key in lights:rgb[region]=debug['light_color']
            else:rgb[region]=debug['normal_color']
        else:
            if key in darks:strength=-facet['dark_strength']*rng.uniform(.65,1.0)
            elif key in lights:strength=facet['light_strength']*rng.uniform(.60,1.0)
            else:strength=rng.uniform(-facet['subtle_variation'],facet['subtle_variation'])
            sh[region]=strength
    if cfg['mode']=='artwork':
        strength=np.ones((h,w),np.float32)
        # Moderação na região focal, sem fazer cortes ou apagar elementos.
        for cx,cy,rx,ry in cfg['focus']['ellipses']:
            y,x=np.ogrid[:h,:w]
            strength[((x/w-cx)/rx)**2+((y/h-cy)/ry)**2<=1]=.25
        sat=finish['saturation'];co=finish['contrast']
        gray=(rgb[:,:,0]*.2126+rgb[:,:,1]*.7152+rgb[:,:,2]*.0722)[:,:,None]
        rgb=gray+(rgb-gray)*sat
        rgb=(rgb-127.5)*co+127.5
        rgb*=1+sh[:,:,None]*strength[:,:,None]
    # Grades delicadas por pixel (em modo teste, mais visíveis).
    line_op=(debug['grid_line_opacity'] if debug['draw_grid'] else 0.0) if cfg['mode']=='white' else finish['line_opacity']
    width=debug['grid_line_width_px'] if cfg['mode']=='white' else finish['line_width_px']
    g=cfg['geometry'];dx=w*g['horizontal_diagonal'];dy=h*g['vertical_diagonal']
    dist=np.minimum.reduce((u-np.floor(u),1-(u-np.floor(u)),
                            v-np.floor(v),1-(v-np.floor(v)))) / math.sqrt((1/dx)**2+(1/dy)**2)
    line=np.exp(-(dist/(width*.6))**2).astype(np.float32)*line_op
    line_color=np.array([89,112,153] if cfg['mode']=='white' else [235,240,255],np.float32)
    rgb=rgb*(1-line[:,:,None])+line_color[None,None,:]*line[:,:,None]
    if cfg['mode']=='artwork' and finish['grain']>0:
        rgb+=rng.normal(0,finish['grain'],(h,w,1)).astype(np.float32)
    rgb=np.clip(rgb,0,255)
    alpha=make_alpha(w,h,selected,cells,holes)
    alpha*=source[:,:,3].astype(np.float32)/255.0
    # Validação: nenhum polígono removido pode cruzar uma faceta protegida.
    if set(holes)&set(protection):
        raise RuntimeError('Proteção de foco violada!')
    return resize_unmatted(rgb,alpha,(cfg['output']['width'],cfg['output']['height']))


def checkerboard(w,h,unit=20):
    y,x=np.mgrid[:h,:w];p=((x//unit+y//unit)%2).astype('uint8')
    v=np.where(p==0,243,218).astype('uint8')
    return Image.fromarray(np.stack([v,v,v],axis=-1),'RGB').convert('RGBA')


def contact_sheet(outputs,path):
    tw,th=450,360
    cols=min(3,len(outputs));rows=math.ceil(len(outputs)/cols)
    sheet=Image.new('RGB',(cols*tw,rows*(th+45)+24),'#eef1f6')
    d=ImageDraw.Draw(sheet)
    for i,(img,seed,rec) in enumerate(outputs):
        x=(i%cols)*tw; y=(i//cols)*(th+45)
        cb=checkerboard(tw,th,unit=22)
        cb.alpha_composite(img.resize((tw,th),RESAMPLE))
        sheet.paste(cb.convert('RGB'),(x,y+28))
        d.text((x+14,y+10),f'SEED {seed}   |   borda -{len(rec["removed"])}   |   vazados {len(rec["holes"])}',fill='#15243a')
    d.text((12,sheet.height-21),'Transparencia: quadrados cinza. Todos os contornos usam losangos inteiros.',fill='#42546d')
    sheet.save(path)


def geometry_diagram(w,h,cells,cfg,path):
    selected=set(cells)
    base=Image.new('RGBA',(w,h),(0,0,0,0))
    d=ImageDraw.Draw(base)
    for key in sorted(selected):
        pts=cells[key]['polygon']
        d.polygon(pts,fill=(255,255,255,255))
    # Linhas por cima da geometria, somente onde há losangos.
    for key in sorted(selected):
        pts=cells[key]['polygon'];d.line(pts+[pts[0]],fill=(74,96,145,160),width=2)
    base.save(path)


def focus_diagram(w,h,cells,protected,path):
    """Mapa de inspeção: facetas protegidas em azul (não integra o asset final)."""
    img=Image.new('RGBA',(w,h),(0,0,0,0))
    d=ImageDraw.Draw(img)
    for key in sorted(cells):
        p=cells[key]['polygon']
        d.polygon(p,fill=(212,233,251,255) if key in protected else (255,255,255,255))
    for key in sorted(cells):
        p=cells[key]['polygon']
        d.line(p+[p[0]],fill=(67,98,151,175),width=2)
    img.save(path)


def load_source(cfg,w,h,config_path):
    if cfg['mode']=='white':
        return np.asarray(Image.new('RGBA',(w,h),(255,255,255,255)),np.uint8),None
    path=Path(cfg['input'])
    if not path.is_absolute():path=config_path.parent/path
    if not path.is_file():raise FileNotFoundError(path)
    image=Image.open(path).convert('RGBA')
    work=ImageOps.fit(image,(w,h),method=RESAMPLE,centering=(.5,.5))
    return np.asarray(work,np.uint8),str(path)


def run(cfg,config_path):
    check_config(cfg)
    out=cfg['output'];w=int(out['work_width']);h=round(w*4/5)
    folder=Path(out['directory'])
    if not folder.is_absolute():folder=config_path.parent/folder
    folder.mkdir(parents=True,exist_ok=True)
    source,inp=load_source(cfg,w,h,config_path)
    cells=cells_in_canvas(w,h,cfg)
    protected=protected_cells(cells,w,h,cfg)
    if not connected(set(cells)):
        raise ValueError('Forma-base não é conectada. Ajuste silhouette.radius_x/y ou geometry')
    if out['save_geometry_diagram']:
        geometry_diagram(w,h,cells,cfg,folder/'geometry-white-grid.png')
        focus_diagram(w,h,cells,protected,folder/'geometry-focus-map.png')
    outputs=[]
    for it in range(int(cfg['variations']['count'])):
        seed=int(cfg['variations']['seed'])+it
        rng=np.random.default_rng(seed)
        selected,removed=trim_silhouette(cells,protected,cfg,rng)
        holes,dark,light=choose_states(selected,protected,cfg,rng)
        img=render(cfg,cells,selected,holes,dark,light,protected,source,seed,w,h)
        fn=f"{cfg['name']}-seed-{seed:06d}"
        img.save(folder/f'{fn}.png',optimize=True)
        if out['webp']:
            img.save(folder/f'{fn}.webp',lossless=True,method=6)
        rec={'version':2,'seed':seed,'mode':cfg['mode'],
             'input':inp,'input_sha256':hashlib.sha256(Path(inp).read_bytes()).hexdigest() if inp else None,
             'grid_diagonals':[cfg['geometry']['horizontal_diagonal'],cfg['geometry']['vertical_diagonal']],
             'selected_count':len(selected),'protected_count':len(protected),
             'removed':[list(i) for i in removed], 'holes':[list(i) for i in holes],
             'dark':[list(i) for i in dark], 'light':[list(i) for i in light]}
        (folder/f'{fn}.json').write_text(json.dumps(rec,ensure_ascii=False,indent=2),encoding='utf8')
        outputs.append((img,seed,rec))
        print(f'{fn}: {len(selected)} losangos, {len(removed)} removidos da borda, {len(holes)} vazados')
    if out['contact_sheet']:
        contact_sheet(outputs,folder/f'{cfg["name"]}-comparativo.png')
    print('Concluído:',folder)
    return folder


def batch_run(cfg, config_path):
    """Filtra todos os PNGs do diretório especificado, sem recursividade.

    Cada arquivo recebe results/<nome-da-imagem>/ com suas variações,
    comparativo, configuração sorteada e diagramas de inspeção.
    """
    batch = cfg.get('batch', {})
    input_directory = Path(batch.get('input_directory', '.'))
    if not input_directory.is_absolute():
        input_directory = config_path.parent / input_directory
    input_directory = input_directory.resolve()
    if not input_directory.is_dir():
        raise NotADirectoryError(f'Diretório de entrada inexistente: {input_directory}')

    output_directory = Path(cfg['output']['directory'])
    if not output_directory.is_absolute():
        output_directory = config_path.parent / output_directory
    output_directory = output_directory.resolve()
    # Saída na mesma pasta da entrada causaria releitura dos próprios resultados.
    if output_directory == input_directory:
        raise ValueError('output.directory precisa ser diferente de batch.input_directory')

    images = sorted(
        (p for p in input_directory.iterdir() if p.is_file() and p.suffix.lower() == '.png'),
        key=lambda p: p.name.casefold()
    )
    if not images:
        print(f'Nenhum arquivo PNG encontrado em: {input_directory}')
        return 0

    print(f'Processamento em lote: {len(images)} imagem(ns) em {input_directory}')
    successes = 0
    errors = []
    for index, path in enumerate(images, 1):
        print(f'\n[{index}/{len(images)}] {path.name}', flush=True)
        individual = copy.deepcopy(cfg)
        individual['mode'] = 'artwork'
        individual['input'] = str(path)
        individual['name'] = path.stem
        individual['output']['directory'] = str(output_directory / path.stem)
        # Seed diferente por arquivo, mas estável se os nomes não mudarem.
        # Desacopla o sorteio de facetas da ordem dos arquivos na pasta.
        if batch.get('seed_per_image', True):
            normalized_name = unicodedata.normalize('NFC', path.stem).casefold()
            digest = hashlib.sha256(normalized_name.encode('utf-8')).digest()
            offset = int.from_bytes(digest[:4], 'big') % 10_000_000
            individual['variations']['seed'] = int(cfg['variations']['seed']) + offset
        try:
            run(individual, config_path)
            successes += 1
        except Exception as exc:
            errors.append((path.name, str(exc)))
            print(f'ERRO ao processar {path.name}: {exc}', file=sys.stderr, flush=True)
            if not batch.get('continue_on_error', True):
                raise

    print(f'\nResumo: {successes} imagem(ns) concluída(s), {len(errors)} falha(s).')
    print('Resultados:', output_directory)
    if errors:
        for name, reason in errors:
            print(f'  - {name}: {reason}', file=sys.stderr)
    return len(errors)


def main():
    p=argparse.ArgumentParser(description='Filtro cristalino — config JSON único e seeds reprodutíveis')
    p.add_argument('--config',type=Path,default=Path('config.json'))
    p.add_argument('--seed',type=int,default=None,help='Substitui apenas variations.seed')
    p.add_argument('--variants',type=int,default=None,help='Substitui apenas variations.count')
    group = p.add_mutually_exclusive_group()
    group.add_argument('--input',type=Path,default=None,help='Processa uma única imagem PNG')
    group.add_argument('--input-dir',type=Path,default=None,help='Pasta dos PNGs; ativa modo lote')
    group.add_argument('--white',action='store_true',help='Desativa o lote e gera o teste em branco')
    group.add_argument('--batch',action='store_true',help='Força processamento de todos os PNGs da pasta configurada')
    args=p.parse_args()
    path=args.config.resolve()
    cfg=json.loads(path.read_text(encoding='utf8'))
    if args.seed is not None:cfg['variations']['seed']=args.seed
    if args.variants is not None:cfg['variations']['count']=args.variants
    if args.input is not None:
        cfg['mode']='artwork'
        cfg['input']=str(args.input.resolve())
        cfg.setdefault('batch',{})['enabled']=False
    elif args.white:
        cfg['mode']='white'
        cfg['input']=None
        cfg.setdefault('batch',{})['enabled']=False
    elif args.batch or args.input_dir is not None:
        cfg.setdefault('batch',{})['enabled']=True
        if args.input_dir is not None:
            cfg['batch']['input_directory']=str(args.input_dir.resolve())
    if cfg.get('batch',{}).get('enabled',False):
        failures=batch_run(cfg,path)
        if failures:
            raise SystemExit(1)
    else:
        run(cfg,path)


if __name__=='__main__':
    main()

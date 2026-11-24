"""Generate tables, figures, Markdown and optional Word from measured CSV only."""
import argparse
from collections import defaultdict
import csv
import json
from pathlib import Path
import statistics as stats

METHODS = ('FF', 'BF', 'WF', 'HF', 'M5', 'M6-BS')
TITLE = 'Informe_Calidad_Algoritmos_ISO25023_Andro_Quispe'


def number(value):
    return float(value) if value not in ('', None) else None


def summarize(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row['case_id'], row['engine_code']].append(row)
    output = []
    fields = ['placed_units', 'total_units', 'coverage_pct', 'aprovechamiento_pct', 'merma_mm2',
              'retazo_recuperable_mm2', 'planchas_usadas', 'retazos_usados', 'total_fuentes']
    for (case, method), values in sorted(groups.items()):
        assert len({v['input_sha256'] for v in values}) == 1
        result = dict(case_id=case, engine_code=method, ejecuciones=len(values),
            completas=sum(v['status']=='COMPLETO' for v in values),
            parciales=sum(v['placed_units'] not in ('',None) and v['status']!='COMPLETO' for v in values),
            fallidas=sum(v['placed_units'] in ('',None) for v in values),
            deterministic=len({v['layout_sha256'] for v in values})==1,
            all_valid=all(v['valid']=='True' for v in values))
        for field in fields:
            numbers = [number(v[field]) for v in values if number(v[field]) is not None]
            result[field] = stats.median(numbers) if numbers else None
        times = [number(v['computation_time_ms']) for v in values if number(v['computation_time_ms']) is not None]
        result.update(time_median_ms=stats.median(times) if times else None,
                      time_min_ms=min(times) if times else None, time_max_ms=max(times) if times else None)
        output.append(result)
    for case in {r['case_id'] for r in rows}:
        assert len({r['input_sha256'] for r in rows if r['case_id']==case})==1
    return output


def write_csv(path, rows):
    with path.open('w',newline='',encoding='utf-8') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)


def charts(summary, destination):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    destination.mkdir(exist_ok=True)
    commercial=[r for r in summary if r['case_id'].startswith('CP-')]
    cases=sorted({r['case_id'] for r in commercial})
    specs=[('G1_aprovechamiento','aprovechamiento_pct','Aprovechamiento (%)'),
           ('G2_merma','merma_mm2','Merma (mm²)'),('G3_cobertura','coverage_pct','Cobertura (%)'),
           ('G4_tiempo','time_median_ms','Tiempo mediano (ms)'),('G6_fuentes','total_fuentes','Fuentes físicas')]
    images=[]
    for name,field,label in specs:
        fig,ax=plt.subplots(figsize=(12,5))
        for i,method in enumerate(METHODS):
            values=[next(r[field] for r in commercial if r['case_id']==case and r['engine_code']==method) for case in cases]
            ax.bar([n+(i-2.5)*.13 for n in range(len(cases))],values,width=.13,label=method)
        ax.set_xticks(range(len(cases)),cases); ax.set_ylabel(label); ax.set_ylim(bottom=0)
        if field in ('aprovechamiento_pct','coverage_pct'): ax.set_ylim(0,105)
        ax.set_title(label+' por caso comercial y motor'); ax.legend(ncol=6,fontsize=9)
        ax.grid(axis='y',alpha=.2); fig.tight_layout(); fig.savefig(destination/(name+'.png'),dpi=150); plt.close(fig)
        images.append((name+'.png',label+' por caso comercial; cinco repeticiones por motor.'))
    fig,ax=plt.subplots(figsize=(10,6))
    for method in METHODS:
        values=[r for r in commercial if r['engine_code']==method]
        ax.scatter([r['time_median_ms'] for r in values],[r['aprovechamiento_pct'] for r in values],label=method)
        for r in values: ax.annotate(r['case_id'],(r['time_median_ms'],r['aprovechamiento_pct']),fontsize=6)
    ax.set(xlabel='Tiempo mediano (ms)',ylabel='Aprovechamiento (%)',title='Calidad y coste temporal (casos comerciales)',ylim=(0,105),xlim=(0,None))
    ax.legend(); ax.grid(alpha=.2); fig.tight_layout(); fig.savefig(destination/'G5_calidad_tiempo.png',dpi=150); plt.close(fig)
    images.append(('G5_calidad_tiempo.png','Cada punto representa un caso/motor; ejes lineales desde cero.'))
    return sorted(images)


def fmt(value):
    return 'N/D' if value is None else f'{value:,.3f}'.rstrip('0').rstrip('.')


def build_report(summary, manifest, evidence, images):
    commercial=[r for r in summary if r['case_id'].startswith('CP-')]
    env=manifest['environment']
    text=['# Calidad y algoritmos - Sprint 2', '', 'Andro Joseph Quispe Cesias', '',
          '## Resumen ejecutivo', '',
          f"Se midieron {manifest['measured_executions']} ejecuciones de seis motores productivos en {len(summary)//6} casos, "
          f"con {manifest['warmups']} calentamientos excluidos. La bateria utiliza cinco repeticiones por caso/motor.", '',
          '## Objetivo y alcance', '',
          'Comparar calidad de soluciones y tiempo computacional con entradas equivalentes. No se evaluan escalamiento horizontal, concurrencia de usuarios, seguridad ni latencia HTTP. Los casos comerciales son sinteticos; no representan pedidos de clientes.', '',
          '## ISO 25010 / ISO 25023', '',
          'Medicion alineada metodologicamente con ISO/IEC 25023:2016: tiempo como evidencia de comportamiento temporal; cobertura y validez como evidencia de adecuacion funcional en estos casos. Aprovechamiento y merma son metricas de dominio NewGlass, no formulas prescritas por ISO. No se declara certificacion ni identificadores normativos no comprobados.', '',
          'ISO/IEC 25010:2023 define el modelo de calidad del producto; ISO/IEC 25023:2016 aporta medidas cuantitativas. Se usa esta distincion como marco metodologico, sin equiparar las metricas de vidrio a medidas normativas.', '',
          'Referencias: [ISO/IEC 25023:2016](https://www.iso.org/standard/35747.html), [ISO/IEC 25010:2023](https://www.iso.org/standard/78176.html).', '',
          '## Metricas y formulas', '',
          'A = area completa de las fuentes utilizadas; P = area geometrica colocada; R = recuperable propuesto.', '',
          '- Aprovechamiento = 100 * P / A (%).',
          '- Merma = A - P - R (mm2); merma_pct_derivada = 100 * merma / A.',
          '- Recuperable: un rectangulo principal por fuente, sujeto a minimos; no todo residuo reutilizable.',
          '- Cobertura = 100 * placed_units / total_units.',
          '- Tiempo: computation_time_ms del adaptador, motor + metricas, excluidos callbacks; no CPU ni HTTP.',
          '- Fuentes: planchas, retazos y total de unidades fisicas.', '',
          '## Diseno experimental y ambiente', '',
          f"Backend evaluado: `{env['backend_sha']}`. Python: `{env['python'].splitlines()[0]}`. SO: `{env['system']}`. CPU: {env['cpu']}; {env['visible_cpus']} CPUs visibles.", '',
          'Cada corrida usa un proceso nuevo, un problema reconstruido e identico hash de entrada para los seis motores. El hash incluye minimos residuales y politica externa. Un calentamiento separado por caso/motor; orden de motores rotado entre repeticiones. Limite externo 120 s y 256 MiB. BS-2 y limites nativos productivos sin cambios. Validacion posterior fuera del cronometro.', '',
          'Se acelero el recorrido de bits del verificador tras detectar su coste en el calentamiento multi-plancha; se conserva la misma enumeracion exhaustiva y se prueba equivalencia con el original. Esto ocurre fuera del tiempo medido y no cambia los algoritmos. Los JSON identifican la variante acelerada; los anteriores usaron el oraculo original.', '',
          'Comerciales: raster 1 mm, margen/separacion 3 mm, paso 5 grados, recuperacion minima 10 x 10 mm. Regresiones: JSON historico intacto y minimos 1 x 1 mm. No se mezclan ambos bloques en la tabla global.', '',
          '## Casos de prueba', '',
          'CP-01 pequeno; CP-02 mediano; CP-03 grande acotado (24 piezas); CP-04 formas mixtas; CP-05 multi-plancha; CP-06 plancha y retazo. EXP-SMALL/MEDIUM/COMPLEX-001 son regresiones controladas. Las dimensiones, cantidades y configuracion completas estan en los JSON versionados.', '',
          '| Caso | Piezas | Fuentes disponibles | Margen / separacion mm | Raster mm | Paso grados | Minimo recuperable mm |',
          '|---|---:|---:|---|---:|---:|---|']
    for case_path in sorted((Path(__file__).parent/'cases').glob('*.json')):
        case=json.loads(case_path.read_text()); cfg=case['config']; limits=case['residual_limits']
        text.append(f"| {case['case_id']} | {sum(p['cantidad'] for p in case['pieces'])} | {sum(s['quantity'] for s in case['stock'])} | {cfg['margin_mm']} / {cfg['separation_mm']} | {cfg['resolution_mm']} | {cfg['angular_step_deg']} | {limits['minimum_width_mm']} x {limits['minimum_height_mm']} |")
    text += ['', '## Resultados por caso', '',
          '| Caso | Motor | Piezas | Aprovechamiento % | Merma mm2 | Recuperable mm2 | Fuentes | Tiempo mediano ms |',
          '|---|---|---|---:|---:|---:|---:|---:|']
    for row in summary:
        text.append('| '+ ' | '.join([row['case_id'],row['engine_code'],f"{fmt(row['placed_units'])}/{fmt(row['total_units'])}",
            *[fmt(row[f]) for f in ('aprovechamiento_pct','merma_mm2','retazo_recuperable_mm2','total_fuentes','time_median_ms')]])+' |')
    text+=['','## Resumen global comercial','','Las areas se ponderan por area utilizada sumada sobre un resultado representativo de cada caso. Tiempo: mediana de las medianas de los seis casos; no latencia de una carga agregada. Cobertura: piezas colocadas totales / solicitadas totales. Solo casos comerciales.','',
           '| Motor | Casos completos | Aprovechamiento % | Merma mm2 (suma) | Recuperable mm2 (suma) | Cobertura % | Tiempo mediano ms |','|---|---:|---:|---:|---:|---:|---:|']
    raw=list(csv.DictReader((evidence/'results/raw_results.csv').open()))
    for method in METHODS:
        group=[r for r in commercial if r['engine_code']==method]
        reps=[r for r in raw if r['case_id'].startswith('CP-') and r['engine_code']==method and r['repetition']=='1']
        area=sum(float(r['area_material_total_mm2']) for r in reps)
        pieces=sum(float(r['area_piezas_colocadas_mm2']) for r in reps)
        text.append(f"| {method} | {sum(r['completas']==r['ejecuciones'] for r in group)}/{len(group)} | {fmt(100*pieces/area)} | {fmt(sum(r['merma_mm2'] for r in group))} | {fmt(sum(r['retazo_recuperable_mm2'] for r in group))} | {fmt(100*sum(r['placed_units'] for r in group)/sum(r['total_units'] for r in group))} | {fmt(stats.median(r['time_median_ms'] for r in group))} |")
    text+=['','## Hallazgos y comparacion de motores','',
           f"Resultados funcionalmente deterministas en {sum(r['deterministic'] for r in summary)} de {len(summary)} combinaciones caso/motor. "
           f"Validacion satisfactoria en {sum(r['all_valid'] for r in summary)} de {len(summary)} combinaciones.", '',
           'No se anadio CP-07: CP-01..06 ya proporcionan diferencias de disposicion y merma con entradas comerciales. No se seleccionaron solamente ejecuciones favorables.','']
    equal_utilization = all(len({r['aprovechamiento_pct'] for r in commercial if r['case_id']==case})==1
                            for case in {r['case_id'] for r in commercial})
    if equal_utilization:
        text += ['Los seis motores tienen el mismo aprovechamiento dentro de cada caso comercial. Las diferencias de merma proceden de la disposicion y del recuperable, no de una mejora del aprovechamiento.', '',
                 'En palabras simples: cortar la misma cantidad de vidrio no significa dejar sobrantes igual de utiles.','']
    coincident=[]
    for case in sorted({r['case_id'] for r in commercial}):
        group={r['engine_code']:r for r in commercial if r['case_id']==case}
        if all(group['FF'][key]==group['BF'][key] for key in ('placed_units','aprovechamiento_pct','merma_mm2','retazo_recuperable_mm2','total_fuentes')):
            coincident.append(case)
    text += ['FF y BF coinciden en las metricas de calidad (sin exigir tiempos iguales) en: '+', '.join(coincident)+'.','']
    for case in sorted({r['case_id'] for r in commercial}):
        group=[r for r in commercial if r['case_id']==case]
        ff=next(r for r in group if r['engine_code']=='FF'); beam=next(r for r in group if r['engine_code']=='M6-BS')
        best=min(r['merma_mm2'] for r in group); winners=', '.join(r['engine_code'] for r in group if r['merma_mm2']==best)
        for field, label, choose in (
            ('aprovechamiento_pct', 'Mayor aprovechamiento (%)', max),
            ('retazo_recuperable_mm2', 'Mayor recuperable (mm2)', max),
            ('total_fuentes', 'Menos fuentes', min),
            ('time_median_ms', 'Menor tiempo mediano (ms)', min)):
            value = choose(r[field] for r in group)
            names = ', '.join(r['engine_code'] for r in group if r[field] == value)
            text.append(f'{case} - {label}: {names}, {fmt(value)}.')
        text += [f"### {case}", '', f"Menor merma: {winners}, {fmt(best)} mm2. M6-BS frente a FF: "
                 f"{fmt(beam['aprovechamiento_pct']-ff['aprovechamiento_pct'])} puntos porcentuales de aprovechamiento, "
                 f"{fmt(beam['merma_mm2']-ff['merma_mm2'])} mm2 de diferencia de merma y "
                 f"{fmt(beam['time_median_ms']/ff['time_median_ms'])} veces el tiempo mediano.", '',
                 'En palabras simples: '+('todos completaron las piezas. ' if all(r['completas']==5 for r in group) else 'hay resultados que requieren revisar cobertura. ')+
                 ('Beam dejo menos merma, pero hay que considerar su coste temporal.' if beam['merma_mm2']<ff['merma_mm2'] else
                  'Beam no redujo la merma respecto de First Fit en este caso; un algoritmo mas complejo no garantiza una mejora.'),'']
    text+=['## Graficos','']
    for i,(name,caption) in enumerate(images,1):
        text += [f'![Figura {i}. {caption}](figures/{name})',f'Figura {i}. {caption}','']
    text+=['## Evidencias reales del sistema','','Las capturas y respuestas HTTP del comparador V2 se registran en `screenshots/` y `ui/`. Esta validacion usa PostgreSQL temporal propio, sin confirmacion de cortes. Los tiempos UI no se mezclan con la bateria. El manifiesto UI identifica comparaciones y relacion con casos.','']
    for i,path in enumerate(sorted((evidence/'screenshots').glob('*.png')),len(images)+1):
        text += [f'![{path.stem}](screenshots/{path.name})',f'Figura {i}. {path.stem}. Interfaz real y datos sinteticos.','']
    text += ['## Pruebas y validez','','Cada resultado medido se valida con un oraculo independiente de bits y enumeracion residual. Revisa contencion, solapamiento, material, espesor, margen, separacion, rotacion, areas y conteos. Los logs de regresion y JUnit estan en `tests/`. No confundir una prueba existente con una prueba ejecutada: consultar esos resultados.','',
             '## Limitaciones','','- Nueve casos sinteticos; no estima calidad universal ni optimalidad global.',
             '- Cinco muestras por combinacion: se informa mediana/minimo/maximo, sin p95.',
             '- Un entorno WSL2 compartido con el host; no es hardware dedicado ni tiempo CPU.',
             '- El oraculo reutiliza rasterizacion canonica; no prueba geometria continua exacta.',
             '- Area recuperable conservadora y dependiente de minimos; regresiones separadas.',
             '- Los tiempos enteros en ms pueden ser cero en casos muy pequenos.',
             '- El README general y algunos comentarios historicos describen V1/deadlines antiguos; prevalece el codigo auditado.', '',
             '## Conclusiones','','La seleccion debe considerar validez y cobertura antes de aprovechamiento, merma y tiempo. Las diferencias observadas son por caso: no justifican un mejor motor absoluto. Los resultados no se completaron con datos historicos ni con ejecuciones de otro sistema operativo.','',
             '## En palabras simples','','Se entrego el mismo trabajo a los seis motores. Se conto cuanto vidrio usaron, que parte quedo recuperable y cuanto tardaron. Las tablas permiten elegir segun el caso; mas tiempo de busqueda no asegura menos desperdicio.','',
             '## Anexos y reproduccion','','Protocolo y comandos: `../../../../experiments/sprint2_quality/README.md`. Datos por corrida, hashes, parametros y dependencias: `results/manifest.json`, `results/raw_results.csv` y JSON individuales. Resumen: `summary_results.csv`.']
    import xml.etree.ElementTree as ET
    text += ['', '## Resultado de suites ejecutadas', '']
    for path in sorted((evidence/'tests').glob('*.xml')):
        for suite in ET.parse(path).getroot().iter('testsuite'):
            values = suite.attrib
            text.append(f"- {path.name}: {values['tests']} pruebas; {values['failures']} fallos; "
                        f"{values['errors']} errores; {values['skipped']} omitidas.")
    text += ['', 'Las selecciones unitarias y API/persistencia son disjuntas. Los reintentos no se suman. '
             'Incidencias de preparacion y correcciones de pruebas: `tests/README.md`.', '']
    return '\n'.join(text)+'\n'


def word(markdown, evidence, destination):
    import re
    from docx import Document
    from docx.shared import Inches, Pt
    def plain(value):
        return re.sub(r'\[([^]]+)\]\(([^)]+)\)',r'\1 (\2)',value.replace('`',''))
    document=Document(); document.add_heading('Calidad y algoritmos',0)
    document.add_paragraph('Sprint 2 - NewGlass\nAndro Joseph Quispe Cesias\nInforme experimental alineado con ISO/IEC 25023')
    document.add_page_break()
    table=None; images=0
    for line in markdown.splitlines():
        if line.startswith('|---'): continue
        if line.startswith('|'):
            cells=[v.strip() for v in line.strip('|').split('|')]
            if table is None:
                table=document.add_table(rows=0,cols=len(cells)); table.style='Light Shading Accent 1'
            row=table.add_row()
            for cell,value in zip(row.cells,cells): cell.text=plain(value)
            continue
        table=None
        if line.startswith('!['):
            name=line.split('](')[1].rstrip(')'); path=evidence/name
            selected=('figures/' in name or path.name[:2] in ('02','05','08','09'))
            if images<10 and selected and path.exists():
                from docx.image.image import Image
                dimensions=Image.from_file(str(path))
                width=min(6.2, 7.0*dimensions.px_width/dimensions.px_height)
                document.add_picture(str(path),width=Inches(width)); images+=1
                document.add_paragraph(f'Figura {images}. {path.stem}. '+
                    ('Medicion experimental de los motores.' if 'figures/' in name else 'Interfaz real con datos sinteticos; sin confirmar cortes.'))
        elif line.startswith('Figura '):
            continue
        elif line.startswith('#'):
            level=len(line)-len(line.lstrip('#')); document.add_heading(line.lstrip('# '),min(level,3))
        elif line.startswith('- '): document.add_paragraph(plain(line[2:]),style='List Bullet')
        elif line: document.add_paragraph(plain(line))
    document.styles['Normal'].font.size=Pt(10)
    destination.parent.mkdir(parents=True,exist_ok=True); document.save(destination)


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--word',type=Path); args=parser.parse_args()
    rows=list(csv.DictReader((args.evidence/'results/raw_results.csv').open()))
    summary=summarize(rows); write_csv(args.evidence/'summary_results.csv',summary)
    manifest=json.loads((args.evidence/'results/manifest.json').read_text())
    images=charts(summary,args.evidence/'figures')
    report=build_report(summary,manifest,args.evidence,images)
    (args.evidence/(TITLE+'.md')).write_text(report,encoding='utf-8')
    if args.word: word(report,args.evidence,args.word)


if __name__=='__main__': main()

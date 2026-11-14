"""Decision report generated from the existing measurements; no experiment is run."""
import csv
import hashlib
import json
from pathlib import Path
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / 'docs/evidence/sprint-02/andro-calidad-algoritmos'
sys.path.insert(0, str(Path(__file__).parent))
from report import summarize

TITLE = 'Informe_Calidad_Algoritmos_ISO25023_Andro_Quispe'
METHODS = ['FF', 'BF', 'WF', 'HF', 'M5', 'M6-BS']
raw = list(csv.DictReader((E/'results/raw_results.csv').open(encoding='utf-8', newline='')))
rows = summarize(raw)
saved = list(csv.DictReader((E/'summary_results.csv').open(encoding='utf-8', newline='')))
assert len(raw) == 270 and len(rows) == len(saved) == 54
for row, original in zip(rows, saved):
    assert row['case_id'] == original['case_id'] and row['engine_code'] == original['engine_code']
    for key in ('merma_mm2', 'retazo_recuperable_mm2', 'time_median_ms', 'coverage_pct', 'aprovechamiento_pct', 'total_fuentes'):
        assert abs(float(row[key])-float(original[key])) < 1e-6, (row['case_id'], key)
assert all(r['all_valid'] and r['ejecuciones'] == 5 for r in rows)
cases = [f'CP-{n:02}' for n in range(1,7)]
group = {c:{r['engine_code']:r for r in rows if r['case_id']==c} for c in cases}
for c in cases:
    g=group[c]
    assert all(g[m]['coverage_pct']==100 and g[m]['completas']==5 for m in METHODS)
    assert all(len({g[m][k] for m in METHODS})==1 for k in ('aprovechamiento_pct','total_fuentes'))
    assert len({g[m]['merma_mm2'] for m in METHODS[:-1]})==1
    # Identical input area and placed area establish why R + merma stays constant.
    rr=[r for r in raw if r['case_id']==c]
    assert len({r['area_material_total_mm2'] for r in rr})==1
    assert len({r['area_piezas_colocadas_mm2'] for r in rr})==1

figdir=E/'figures'
blue='#24527a'; grey='#92999f'; green='#638d73'; red='#b76e68'
plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False,'figure.facecolor':'white'})
def save(name):
    plt.tight_layout(); plt.savefig(figdir/name,dpi=180,bbox_inches='tight'); plt.close()
delta=[(group[c]['FF']['merma_mm2']-group[c]['M6-BS']['merma_mm2'])/1e6 for c in cases]
pct=[-100*delta[i]*1e6/group[c]['FF']['merma_mm2'] for i,c in enumerate(cases)]
extra=[(group[c]['M6-BS']['time_median_ms']-group[c]['FF']['time_median_ms'])/1000 for c in cases]
fig,ax=plt.subplots(figsize=(8,4)); ax.barh(cases,delta,color=[green if x>0 else red for x in delta]); ax.axvline(0,color='#444',lw=.8); ax.invert_yaxis(); ax.set(xlabel='Merma evitada por M6 frente a FF (m²)',title='Más merma  ←  M6 frente a FF  →  Menos merma'); ax.grid(axis='x',alpha=.2); save('G2_delta_merma_m6_vs_ff.png')
fig,ax=plt.subplots(figsize=(8,4)); ax.barh(cases,pct,color=[green if x<0 else red for x in pct]); ax.axvline(0,color='#444',lw=.8); ax.invert_yaxis(); ax.set(xlabel='Cambio de merma respecto a FF (%)',title='Reducción  ←  Cambio porcentual  →  Aumento'); ax.grid(axis='x',alpha=.2); save('G3_delta_merma_porcentaje.png')
fig,axes=plt.subplots(2,3,figsize=(10,6));
for ax,c in zip(axes.flat,cases):
    for m,col in [('FF',grey),('M6-BS',blue)]:
        r=group[c][m]; ax.scatter(r['time_median_ms']/1000,r['merma_mm2']/1e6,color=col,label=m,s=45); ax.annotate(m,(r['time_median_ms']/1000,r['merma_mm2']/1e6),xytext=(5,4),textcoords='offset points',fontsize=8)
    ax.set_title(c); ax.set_xlabel('Tiempo (s)'); ax.set_ylabel('Merma (m²)'); ax.grid(alpha=.2)
fig.suptitle('Merma y tiempo: FF frente a M6, por caso'); save('G4_merma_vs_tiempo.png')
fig,ax=plt.subplots(figsize=(8,4.3)); ax.axhline(0,color='#555',lw=.8); ax.scatter(extra,delta,c=[green if x>0 else red for x in delta],s=70)
for x,y,c in zip(extra,delta,cases): ax.annotate(c,(x,y),xytext=(6,5),textcoords='offset points')
ax.set(xlabel='Tiempo adicional de M6 frente a FF (s)',ylabel='Merma evitada (m²)',title='Material preservado frente al tiempo adicional'); ax.grid(alpha=.2); save('G5_material_preservado_vs_tiempo_extra.png')
decisions=[]
for c in cases:
    g=group[c]; best=min(r['merma_mm2'] for r in g.values()); fast=min(r['time_median_ms'] for r in g.values())
    winners='/'.join(m for m in METHODS if g[m]['merma_mm2']==best); fastest='/'.join(m for m in METHODS if g[m]['time_median_ms']==fast)
    decisions.append((c,best,winners,fastest,fast))
fig,ax=plt.subplots(figsize=(10,3)); ax.axis('off'); tbl=ax.table(cellText=[[c,w,f] for c,_,w,f,_ in decisions],colLabels=['Caso','Menor merma','Menor tiempo'],cellLoc='center',loc='center'); tbl.auto_set_font_size(False); tbl.set_fontsize(10); tbl.scale(1,1.6)
for (i,j),cell in tbl.get_celld().items(): cell.set_facecolor('#e4e7e9' if i==0 else ('#f4f5f5' if i%2==0 else 'white'))
save('G6_recomendacion_por_caso.png')
fig,ax=plt.subplots(figsize=(9,2)); ax.axis('off'); ax.text(.02,.7,'270 mediciones     |     9 casos     |     6 motores',fontsize=17,weight='bold'); ax.text(.02,.35,'100 % cobertura comercial     |     54/54 combinaciones válidas',fontsize=14,color=blue); save('G1_resumen_decision.png')
fig,axes=plt.subplots(1,2,figsize=(10,3.4))
for ax,c in zip(axes,cases[3:5]):
    for i,m in enumerate(('FF','M6-BS')):
        r=group[c][m]; sample=next(x for x in raw if x['case_id']==c and x['engine_code']==m)
        placed=float(sample['area_piezas_colocadas_mm2'])/1e6; rec=r['retazo_recuperable_mm2']/1e6; waste=r['merma_mm2']/1e6
        ax.barh(m,placed,color='#bcc1c4',label='Piezas' if i==0 else None)
        ax.barh(m,rec,left=placed,color='#7aa68a',label='Recuperable' if i==0 else None)
        ax.barh(m,waste,left=placed+rec,color='#c8867d',label='Merma' if i==0 else None)
    ax.set_title(c); ax.set_xlabel('Área de fuentes utilizadas (m²)'); ax.invert_yaxis()
axes[0].legend(loc='lower center',bbox_to_anchor=(1.05,-.45),ncol=3,frameon=False)
fig.suptitle('Balance de material medido: FF y M6'); save('G7_balance_casos.png')

L=[]
def add(*lines): L.extend(lines); L.append('')
def fig(name, caption): add(f'![{caption}](figures/{name})',caption)
add('# Calidad y algoritmos — NewGlass', 'Andro Joseph Quispe Cesias · Sprint 2')
add('## 1. Resumen ejecutivo', 'Se analizaron 270 mediciones de seis motores en nueve casos: seis comerciales y tres de regresión. Se excluyeron 54 calentamientos. Las 54 combinaciones caso-motor superaron la validación geométrica; en los seis casos comerciales todos colocaron el 100 % de las piezas.')
add('**Hallazgo clave.** Dentro de cada caso comercial los seis motores tuvieron el mismo aprovechamiento y utilizaron la misma cantidad de fuentes. La diferencia principal estuvo en la organización del material residual, su área recuperable, la merma y el tiempo. El aprovechamiento por sí solo no basta para seleccionar un motor.')
add('M6 redujo la merma frente a FF en cuatro casos y la aumentó en dos, con mayor tiempo mediano en los seis. No hay un ganador universal: la elección depende del escenario y de la prioridad entre material y velocidad.')
add('| Caso | Prioridad material | Prioridad velocidad |', '|---|---|---|', *[f'| {c} | {w} | {f} |' for c,b,w,f,t in decisions])
add('## 2. Cómo leer este informe', '**A** es el área completa de las fuentes utilizadas; **P**, el área de las piezas colocadas; **R**, el área recuperable propuesta. Aprovechamiento = 100 × P / A; merma = A − P − R. Áreas grandes se expresan en m² (1 m² = 1 000 000 mm²).')
add('**Merma no es 100 − aprovechamiento.** Esa diferencia incluye tanto recuperable como merma. Con la misma plancha y las mismas piezas, P y A son iguales: el aprovechamiento empata. Si una distribución deja un rectángulo recuperable mayor y otra fragmenta el sobrante, R cambia y también la merma. Es una explicación conceptual, no un patrón medido adicional.')
add('## 3. Marco de medición ISO 25010 / ISO 25023', 'ISO/IEC 25010 orienta qué características de calidad evaluar; ISO/IEC 25023 aporta medidas para cuantificar calidad. Este informe presenta una evaluación cuantitativa alineada metodológicamente con ISO/IEC 25023. Cobertura y validez aportan evidencia de adecuación funcional, y el tiempo de comportamiento temporal. Aprovechamiento, merma y recuperable son métricas de dominio NewGlass que complementan esa evaluación. ISO no prescribe aquí la fórmula de merma y no se afirma certificación.')
add('| Qué evaluar | Qué medimos | Fórmula / unidad | Para qué sirve |','|---|---|---|---|','| Adecuación funcional | Cobertura | 100 × colocadas / solicitadas (%) | Comprueba si se completa el pedido |','| Validez geométrica | Patrón válido | PASS / FAIL | Comprueba contención, no solapamiento, margen y separación |','| Comportamiento temporal | Tiempo mediano | ms o s | Mide el costo computacional observado |','| Calidad del corte | Merma | A − P − R (mm² o m²) | Material no colocado ni recuperable según el criterio aplicado |','| Reutilización | Recuperable | mm² o m² | Material potencialmente útil para futuros cortes |','| Eficiencia material | Aprovechamiento | 100 × P / A (%) | Proporción del área convertida en piezas |')
add('## 4. Métricas', 'El recuperable es una propuesta conservadora: un rectángulo principal por fuente sujeto a dimensiones mínimas. No equivale a todo el residuo físicamente reutilizable. El tiempo incluye motor y cálculo de métricas en el adaptador, excluye callbacks, validación posterior y latencia HTTP; se informa la mediana de cinco mediciones, no tiempo de CPU.')
add('## 5. Motores evaluados', '| Motor | Idea principal | Cuándo podría aportar |','|---|---|---|','| FF | Primera colocación válida según su orden | Referencia rápida y simple |','| BF | Busca un ajuste más cerrado | Espacios con capacidades distintas |','| WF | Aplica su política de selección de espacios disponibles | Distribuciones con espacios grandes |','| HF | Intenta usar huecos existentes bajo límites | Piezas con huecos aprovechables |','| M5 | Considera el mejor residual libre | Alternativas con residuales distintos |','| M6-BS | Explora varias alternativas mediante Beam Search acotado | Cuando preservar material justifica mayor cómputo |')
add('## 6. Diseño experimental', 'Cada caso/motor tuvo un calentamiento excluido y cinco repeticiones medidas en procesos nuevos, con orden rotado. Se verificaron hashes de entrada y resultados, y la geometría mediante un oráculo independiente de bits y enumeración residual. El ambiente y límites exactos están en results/manifest.json; se evaluó el backend identificado por su hash, sobre WSL2 compartido. La batería comercial usa raster de 1 mm, margen y separación de 3 mm y mínimo recuperable de 10 × 10 mm. No se repitió la batería en esta revisión.')
add('## 7. Casos de prueba', 'CP-01 pequeño; CP-02 mediano; CP-03 grande acotado; CP-04 formas mixtas; CP-05 multi-plancha; CP-06 plancha y retazo. Los tres casos EXP son regresiones históricas y se presentan por separado. Los pedidos comerciales son sintéticos, no una muestra de clientes.')
add('## 8. Resultados para tomar decisiones', '| Caso | Mejor merma (m²) | Motor(es) | Más rápido | Tiempo (s) | Recomendación |','|---|---:|---|---|---:|---|', *[f"| {c} | {b/1e6:.3f} | {w} | {f} | {t/1000:.3f} | {'Material: M6; velocidad: '+f if 'M6-BS' in w else 'Elegir '+f+' en estas métricas'} |" for c,b,w,f,t in decisions])
fig('G2_delta_merma_m6_vs_ff.png','Figura 1. Merma evitada por M6 frente a FF. Valores positivos preservan material; CP-02 y CP-04 muestran que M6 también puede empeorar.')
fig('G3_delta_merma_porcentaje.png','Figura 2. Cambio porcentual de merma: (M6 − FF) / FF × 100. Valores negativos representan reducciones; positivos, aumentos.')
add('| Caso | Cambio M6 vs FF | Tiempo extra vs FF | Decisión |','|---|---:|---:|---|', *[f"| {c} | {p:+.1f} % | +{t:.3f} s / {group[c]['M6-BS']['time_median_ms']/group[c]['FF']['time_median_ms']:.1f}× | {'Considerar M6 por material' if d>0 else 'FF domina en merma y tiempo'} |" for c,p,t,d in zip(cases,pct,extra,delta)])
add('## 9. Merma y recuperabilidad', 'En cada caso comercial A y P son iguales entre motores; por tanto, una diferencia de R corresponde a una diferencia opuesta de merma. La igualdad en número de fuentes no significa igualdad de disposición ni de recuperabilidad. **En palabras simples:** colocar las mismas piezas puede dejar sobrantes de utilidad distinta.')
add('## 10. Costo temporal', 'Las medianas se comparan en segundos y también como multiplicadores, calculados sobre los tiempos de cada caso. No son predicciones de latencia en producción. Cinco repeticiones no permiten estimar percentiles robustos.')
fig('G4_merma_vs_tiempo.png','Figura 3. Merma y tiempo mediano de FF y M6 por caso. Las escalas son independientes entre paneles; CP-02 y CP-04 muestran que esperar más no siempre reduce merma.')
add('## 11. Trade-off material vs tiempo')
fig('G5_material_preservado_vs_tiempo_extra.png','Figura 4. Merma evitada por M6 frente al tiempo adicional respecto de FF. Por encima de cero se preserva material; por debajo aumenta la merma. La conveniencia depende del valor del material y del tiempo disponible.')
add('## 12. ¿Qué motor elegir?', '**Regla práctica propuesta, limitada a los casos evaluados:** exigir primero validez y cobertura; si varios cumplen, comparar merma, recuperable, fuentes y tiempo. Si la diferencia de merma es relevante y el vidrio es costoso, considerar el motor que preserve más material; si es pequeña o inexistente, considerar el más rápido. No es una regla universal.')
fig('G6_recomendacion_por_caso.png','Figura 5. Matriz de decisión por caso: distingue menor merma de menor tiempo; los empates se muestran completos.')
add('FF fue el más rápido en CP-01, CP-02, CP-03, CP-04 y CP-06; HF en CP-05. M6 ofrece la menor merma en CP-01, CP-03, CP-05 y CP-06. En CP-02 y CP-04 no existe ventaja experimental para seleccionar M6 bajo las métricas evaluadas: FF tiene menor merma y tiempo.')
add('## 13. Casos representativos', f"**CP-05, favorable a M6.** HF: {group['CP-05']['HF']['merma_mm2']/1e6:.6f} m² y {group['CP-05']['HF']['time_median_ms']/1000:.3f} s; M6: {group['CP-05']['M6-BS']['merma_mm2']/1e6:.6f} m² y {group['CP-05']['M6-BS']['time_median_ms']/1000:.3f} s. M6 evita {(group['CP-05']['HF']['merma_mm2']-group['CP-05']['M6-BS']['merma_mm2'])/1e6:.6f} m² de merma a cambio de {(group['CP-05']['M6-BS']['time_median_ms']-group['CP-05']['HF']['time_median_ms'])/1000:.3f} s adicionales. Ambos colocaron las mismas piezas, usaron las mismas fuentes y lograron igual aprovechamiento. En palabras simples: esperar aproximadamente 1,2 s preservó casi medio metro cuadrado adicional bajo el criterio de recuperabilidad.")
add('**CP-04, desfavorable a M6.** FF generó 0,166753 m² de merma en 0,106 s; M6 generó 0,238393 m² en 0,842 s. Una búsqueda más compleja no garantiza mejor solución. Estas cifras son medianas o valores del resumen, redondeados.')
fig('G7_balance_casos.png','Figura 6. Balance de áreas medidas en CP-04 y CP-05. A igual área colocada y de fuentes, cambia el recuperable y, en sentido contrario, la merma. Son áreas agregadas, no dibujos de patrones de corte.')
add('## 14. Evidencia real del sistema', 'Las capturas existentes muestran el comparador de seis motores, multi-plancha, plancha con retazo y trazabilidad. Las respuestas UI y su manifiesto están en ui/. Los tiempos de interfaz no se mezclan con las mediciones experimentales.')
for n,cap in [('01-comparacion-seis-motores.png','Comparador real de seis motores con datos sintéticos.'),('06-multi-plancha.png','Vista real del caso multi-plancha.'),('07-plancha-retazo.png','Vista real de plancha y retazo.'),('08-trazabilidad-material.png','Trazabilidad de material en el sistema.')]: add(f'![{cap}](screenshots/{n})',cap)
add('## 15. Pruebas y validez', 'Los registros de tests/ documentan 568 pruebas unitarias y 88 API/persistencia aprobadas. La validación geométrica revisó contención, solapamiento, material, espesor, margen, separación, rotación, áreas y conteos. El oráculo comparte la rasterización canónica, por lo que no prueba geometría continua exacta.')
add('## 16. Limitaciones', 'Nueve casos sintéticos y cinco mediciones por combinación no demuestran optimalidad ni rendimiento universal. FF, BF, WF, HF y M5 coincidieron en aprovechamiento y merma en los seis casos comerciales: esto no demuestra equivalencia general, sino que los casos no los discriminaron en esas métricas. El recuperable depende de mínimos y del rectángulo propuesto. Los tiempos dependen del entorno compartido. No existe modelo económico validado ni estimación de ahorro monetario.')
add('## 17. Conclusiones', 'Los seis motores produjeron soluciones geométricamente válidas; todos completaron los pedidos comerciales. El aprovechamiento no diferenció motores dentro de cada caso, mientras merma y recuperable sí lo hicieron. M6 redujo merma en cuatro casos y la aumentó en dos, con mayor costo temporal. Una reducción puede justificar la espera según el contexto, pero no existe ganador universal. Se necesitan casos que discriminen las cinco heurísticas restantes y, posteriormente, costo por m² según tipo y espesor para estimar impacto económico.')
add('**En palabras simples.** Los seis motores resolvieron los pedidos evaluados. La diferencia estuvo en cómo quedó el vidrio sobrante y cuánto tardaron. M6 conservó más material en varios escenarios, pero en otros tardó más y dejó más merma. La elección debe depender de si se prioriza material o rapidez.')
add('## Anexo A — Resultados detallados', '| Caso | Motor | Piezas | Aprovechamiento % | Merma mm² | Recuperable mm² | Fuentes | Mediana ms |','|---|---|---|---:|---:|---:|---:|---:|', *[f"| {r['case_id']} | {r['engine_code']} | {int(r['placed_units'])}/{int(r['total_units'])} | {r['aprovechamiento_pct']:.3f} | {r['merma_mm2']:.1f} | {r['retazo_recuperable_mm2']:.1f} | {int(r['total_fuentes'])} | {r['time_median_ms']:.0f} |" for r in rows])
add('## Anexo B — Regresiones', 'EXP-SMALL-001, EXP-MEDIUM-001 y EXP-COMPLEX-001 usan sus JSON históricos y mínimos recuperables de 1 × 1 mm. Sus 18 combinaciones se incluyen en el anexo A; no se mezclan con las recomendaciones comerciales.')
add('## Anexo C — Reproducción y archivos', 'Protocolo y casos: experiments/sprint2_quality/README.md. Entradas, hashes, tiempos y resultados por corrida: results/manifest.json, results/raw_results.csv y JSON individuales. Resumen: summary_results.csv. Referencias: https://www.iso.org/standard/78176.html y https://www.iso.org/standard/35747.html.')
markdown='\n'.join(L)+'\n'; (E/(TITLE+'.md')).write_text(markdown,encoding='utf-8')

doc=Document(); sec=doc.sections[0]; sec.top_margin=sec.bottom_margin=Inches(.7); sec.left_margin=sec.right_margin=Inches(.8)
normal=doc.styles['Normal']; normal.font.name='Calibri'; normal.font.size=Pt(10); normal.paragraph_format.space_after=Pt(5)
for name in ('Title','Heading 1','Heading 2','Heading 3'):
    doc.styles[name].font.name='Calibri'; doc.styles[name].font.color.rgb=RGBColor.from_string('202428')
doc.add_heading('Calidad y algoritmos',0); doc.add_paragraph('NEWGLASS · SPRINT 2\nAndro Joseph Quispe Cesias\nEvaluación cuantitativa alineada metodológicamente con ISO/IEC 25023'); doc.add_page_break()
table=None
for line in markdown.splitlines():
    if line.startswith('# Calidad') or line.startswith('Andro Joseph'): continue
    if line.startswith('|---'): continue
    if line.startswith('|'):
        vals=[x.strip() for x in line.strip('|').split('|')]
        if table is None: table=doc.add_table(rows=0,cols=len(vals)); table.autofit=True
        row=table.add_row(); ix=len(table.rows)-1
        trpr=row._tr.get_or_add_trPr(); cant=OxmlElement('w:cantSplit'); trpr.append(cant)
        if ix==0:
            header=OxmlElement('w:tblHeader'); header.set(qn('w:val'),'true'); trpr.append(header)
        for cell,value in zip(row.cells,vals):
            cell.text=value.replace('**',''); shade=OxmlElement('w:shd'); shade.set(qn('w:fill'),'DDE1E3' if ix==0 else ('F3F4F4' if ix%2==0 else 'FFFFFF')); cell._tc.get_or_add_tcPr().append(shade)
            for p in cell.paragraphs:
                for run in p.runs: run.font.size=Pt(8.5); run.bold=(ix==0)
        continue
    table=None
    if line.startswith('!['):
        name=line.split('](')[1].rstrip(')'); path=E/name
        if path.exists(): doc.add_picture(str(path),width=Inches(4.8 if name.startswith('screenshots/') else 6.5))
    elif line.startswith('## '):
        if line.startswith('## Anexo A'): doc.add_page_break()
        doc.add_heading(line[3:],level=1)
    elif line:
        p=doc.add_paragraph(); p.add_run(line.replace('**',''))
        if line.startswith('Figura '): p.style=doc.styles['Caption']
for t in doc.tables:
    if len(t.rows) <= 10:
        for row in t.rows[:-1]:
            for cell in row.cells:
                for p in cell.paragraphs: p.paragraph_format.keep_with_next=True
dest=ROOT/'Informe_Calidad_Algoritmos_ISO25023_Andro_Quispe.docx'; doc.save(dest)
markdown_tables=[[v.strip().replace('**','') for v in line.strip('|').split('|')] for line in markdown.splitlines() if line.startswith('|') and not line.startswith('|---')]
word_tables=[[cell.text for cell in row.cells] for table in Document(dest).tables for row in table.rows]
assert markdown_tables == word_tables, 'Las tablas del Word no coinciden con Markdown'
manifest_path=E/'evidence-manifest.json'
manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
for path in [E/(TITLE+'.md'), *[figdir/name for name in ('G1_resumen_decision.png','G2_delta_merma_m6_vs_ff.png','G3_delta_merma_porcentaje.png','G4_merma_vs_tiempo.png','G5_material_preservado_vs_tiempo_extra.png','G6_recomendacion_por_caso.png','G7_balance_casos.png')]]:
    manifest['files'][path.relative_to(E).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
manifest['figures']=len(list(figdir.glob('*.png')))
manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
assert all((figdir/f'G{i}_{suffix}.png').exists() for i,suffix in [(1,'resumen_decision'),(2,'delta_merma_m6_vs_ff'),(3,'delta_merma_porcentaje'),(4,'merma_vs_tiempo'),(5,'material_preservado_vs_tiempo_extra'),(6,'recomendacion_por_caso')])
assert sum(x>0 for x in delta)==4 and sum(x<0 for x in delta)==2
print('PASS: raw=270, grupos=54, CSV/medianas/áreas/validez verificados; Markdown y Word generados:', dest)

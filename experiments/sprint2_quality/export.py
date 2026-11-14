"""Export a local delivery folder outside Git; destination is always explicit."""
import argparse
import json
from pathlib import Path
import shutil
from report import TITLE, word


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--destination',type=Path,required=True); args=parser.parse_args()
    destination=args.destination.resolve(); repo=Path(__file__).resolve().parents[2]
    assert repo not in destination.parents and destination!=repo, 'Delivery must stay outside Git'
    for name in ('01_Casos','02_Resultados','03_Graficos','04_Capturas_Sistema','05_Pruebas','06_Documento'):
        (destination/name).mkdir(parents=True,exist_ok=True)
    for source,name in ((Path(__file__).parent/'cases','01_Casos'),(args.evidence/'results','02_Resultados'),
                        (args.evidence/'figures','03_Graficos'),(args.evidence/'screenshots','04_Capturas_Sistema'),
                        (args.evidence/'tests','05_Pruebas')):
        shutil.copytree(source,destination/name,dirs_exist_ok=True)
    shutil.copytree(args.evidence/'ui',destination/'04_Capturas_Sistema/ui',dirs_exist_ok=True)
    for name in ('summary_results.csv','evidence-manifest.json'):
        if (args.evidence/name).exists(): shutil.copy2(args.evidence/name,destination/'02_Resultados'/name)
    report=args.evidence/(TITLE+'.md')
    shutil.copy2(report,destination/'06_Documento'/report.name)
    # Preserve relative image links in the local Markdown copy.
    for name in ('figures','screenshots'):
        shutil.copytree(args.evidence/name,destination/'06_Documento'/name,dirs_exist_ok=True)
    word(report.read_text(encoding='utf-8'),args.evidence,destination/'06_Documento'/(TITLE+'.docx'))
    from docx import Document
    document = Document(destination/'06_Documento'/(TITLE+'.docx'))
    assert 6 <= len(document.inline_shapes) <= 10
    assert any('Andro Joseph Quispe Cesias' in p.text for p in document.paragraphs)
    markdown_tables = [[value.strip().replace('`','') for value in line.strip('|').split('|')]
                       for line in report.read_text(encoding='utf-8').splitlines()
                       if line.startswith('|') and not line.startswith('|---')]
    word_tables = [[cell.text for cell in row.cells] for table in document.tables for row in table.rows]
    assert markdown_tables == word_tables, 'Word tables differ from Markdown'
    entries=[('Comparacion de motores','04_Capturas_Sistema/01-comparacion-seis-motores.png','Los seis motores recibieron el mismo caso comercial en la aplicacion real.'),
             ('Datos tecnicos','04_Capturas_Sistema/03-datos-tecnicos.png','Areas y tiempo observados desde el comparador V2.'),
             ('Patron diferenciador','04_Capturas_Sistema/05-patron-diferenciador-M6.png','La disposicion de M6 cambia el recuperable y la merma.'),
             ('Multi-plancha','04_Capturas_Sistema/06-multi-plancha.png','El caso requiere varias fuentes fisicas.'),
             ('Plancha y retazo','04_Capturas_Sistema/07-plancha-retazo.png','Reutilizacion de material con trazabilidad visible.'),
             ('Aprovechamiento','03_Graficos/G1_aprovechamiento.png','Comparacion por caso; las igualdades tambien son resultados.'),
             ('Merma','03_Graficos/G2_merma.png','Residuo no recuperado segun la formula productiva A-P-R.'),
             ('Tiempo mediano','03_Graficos/G4_tiempo.png','Cinco mediciones por caso y motor, sin incluir calentamientos.'),
             ('Calidad y tiempo','03_Graficos/G5_calidad_tiempo.png','Relacion entre aprovechamiento y coste temporal.'),
             ('Resultados reproducibles','02_Resultados/raw_results.csv','Una fila por caso, motor y repeticion con hash de entrada.')]
    lines=[]
    for i,(title,path,description) in enumerate(entries,1):
        assert (destination/path).exists(),path
        lines += [f'{i:02}. {title}',description,path,'']
    (destination/'ORDEN_NOTION.txt').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({'word_exists':(destination/'06_Documento'/(TITLE+'.docx')).exists(),'notion_entries':len(entries)}))


if __name__=='__main__': main()

"""Export the versioned Markdown report and measured charts to Word."""
import argparse
from pathlib import Path

from docx import Document
from docx.shared import Inches, Pt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('report', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    document = Document()
    document.styles['Normal'].font.size = Pt(10)
    document.add_heading('NewGlass: backend y seis motores', 0)
    document.add_paragraph('Informe técnico — resultados y límites observados')
    document.add_page_break()
    table = None
    for line in args.report.read_text(encoding='utf-8').splitlines():
        if not line.startswith('|'):
            table = None
        if line.startswith('# '):
            document.add_heading(line[2:], level=1)
        elif line.startswith('## '):
            document.add_heading(line[3:], level=2)
        elif line.startswith('|'):
            if set(line.replace('|', '').replace('-', '').replace(':', '').strip()) == set():
                continue
            cells = [cell.strip().replace('`', '') for cell in line.strip('|').split('|')]
            if table is None:
                table = document.add_table(rows=0, cols=len(cells))
                table.style = 'Table Grid'
            row = table.add_row()
            for target, value in zip(row.cells, cells):
                target.text = value
        elif line.strip():
            document.add_paragraph(line.replace('**', '').replace('`', ''))
    figures = [('G1_time.png', 'Tiempo mediano de cálculo por cantidad de piezas.'),
               ('G2_rss.png', 'RSS pico del proceso: incluye preparación y validación.'),
               ('G3_cpu.png', 'CPU total del proceso: incluye preparación y validación.'),
               ('G4_waste.png', 'Merma A menos P menos R para las cargas sintéticas.'),
               ('G5_coverage.png', 'Cobertura mediana por motor y escala.'),
               ('G6_status.png', 'Número de resultados por estado y carga.'),
               ('G7_concurrencia_recursos.png', 'Un servidor frente a dos: tiempo, CPU y RSS de procesos locales. Una observación por escenario.'),
               ('G8_capacity.png', 'Proporción completa observada en el fixture evaluado.')]
    document.add_heading('Gráficos medidos', level=1)
    for name, caption in figures:
        folder = 'concurrency' if name.startswith('G7_') else 'results'
        document.add_picture(str(args.report.parent / folder / name), width=Inches(6.2))
        document.add_paragraph(caption)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    document.save(args.output)


if __name__ == '__main__':
    main()

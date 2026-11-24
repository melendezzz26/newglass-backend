"""Export the versioned partial Markdown report and measured charts to Word."""
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
    document.add_paragraph('Informe de avance — estado parcial')
    document.add_page_break()
    for line in args.report.read_text(encoding='utf-8').splitlines():
        if line.startswith('# '):
            document.add_heading(line[2:], level=1)
        elif line.startswith('## '):
            document.add_heading(line[3:], level=2)
        elif line.startswith('|'):
            if set(line.replace('|', '').replace('-', '').replace(':', '').strip()) == set():
                continue
            document.add_paragraph(' | '.join(cell.strip() for cell in line.strip('|').split('|')))
        elif line.strip():
            document.add_paragraph(line.replace('**', '').replace('`', ''))
    figures = [('G1_time.png', 'Tiempo mediano de cálculo por cantidad de piezas.'),
               ('G2_rss.png', 'RSS pico del proceso: incluye preparación y validación.'),
               ('G3_cpu.png', 'CPU total del proceso: incluye preparación y validación.'),
               ('G4_waste.png', 'Merma A menos P menos R para las cargas sintéticas.'),
               ('G5_coverage.png', 'Cobertura mediana por motor y escala.'),
               ('G6_status.png', 'Número de resultados por estado y carga.'),
               ('G8_capacity.png', 'Proporción completa observada en el fixture evaluado.')]
    document.add_heading('Gráficos medidos', level=1)
    for name, caption in figures:
        document.add_picture(str(args.report.parent / 'results' / name), width=Inches(6.2))
        document.add_paragraph(caption)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    document.save(args.output)


if __name__ == '__main__':
    main()

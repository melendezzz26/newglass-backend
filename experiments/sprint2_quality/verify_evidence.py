"""Verify saved evidence without executing engines again."""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from decimal import Decimal
from pathlib import Path


def verify(directory):
    manifest=json.loads((directory/'manifest.json').read_text())
    for name,expected in manifest['files'].items():
        assert hashlib.sha256((directory/name).read_bytes()).hexdigest()==expected,name
    rows=list(csv.DictReader((directory/'raw_results.csv').open()))
    assert len(rows)==manifest['measured_executions']==270
    keys = [(r['case_id'], r['engine_code'], r['repetition']) for r in rows]
    assert len(keys) == len(set(keys)), 'Duplicate measured execution'
    assert {r['repetition'] for r in rows} == {'1','2','3','4','5'}
    counts=Counter((row['case_id'],row['engine_code']) for row in rows)
    assert len(counts)==54 and set(counts.values())=={5}
    hashes=defaultdict(set)
    for row in rows:
        hashes[row['case_id']].add(row['input_sha256'])
        saved=json.loads((directory/f"{row['case_id']}_{row['engine_code']}_{row['repetition']}.json").read_text())
        assert saved['validation']['valid'] is True
        assert saved['row']['input_sha256']==row['input_sha256']
        assert str(saved['row']['computation_time_ms'])==row['computation_time_ms']
        assert saved['snapshot']['result']['computation_time_ms']==saved['row']['computation_time_ms']
        assert row['backend_sha']==manifest['environment']['backend_sha']
        for key, value in saved['row'].items():
            assert str(value) == row[key], (key, row['case_id'])
        a, p, r, waste = (Decimal(row[k]) for k in ('area_material_total_mm2',
            'area_piezas_colocadas_mm2', 'retazo_recuperable_mm2', 'merma_mm2'))
        assert a-p-r == waste
        assert abs(Decimal(row['aprovechamiento_pct'])-100*p/a) <= Decimal('.001')
        assert abs(Decimal(row['merma_pct_derivada'])-100*waste/a) < Decimal('.000001')
        assert Decimal(row['coverage_pct']) == 100*Decimal(row['placed_units'])/Decimal(row['total_units'])
        assert int(row['total_fuentes']) == int(row['planchas_usadas'])+int(row['retazos_usados'])
    assert len(hashes)==9 and all(len(values)==1 for values in hashes.values())
    assert len(list(directory.glob('*_0.json')))==manifest['warmups']==54
    return dict(measured=len(rows),warmups=54,cases=9,engines=6,repetitions=5,
                equivalent_inputs=True,hashes_valid=True,all_results_valid=True)


def verify_delivery(evidence):
    """Cross-check generated artifacts, saved UI values and reproducible hashes."""
    from experiments.sprint2_quality.report import summarize, TITLE
    import xml.etree.ElementTree as ET
    result = verify(evidence/'results')
    experiment = Path(__file__).resolve().parent
    for source in (experiment.parent/'optimization/cases').glob('EXP-*.json'):
        assert source.read_bytes() == (experiment/'cases'/source.name).read_bytes()
    raw = list(csv.DictReader((evidence/'results/raw_results.csv').open()))
    expected = summarize(raw)
    actual = list(csv.DictReader((evidence/'summary_results.csv').open()))
    assert actual == [{k: str(v) if v is not None else '' for k,v in r.items()} for r in expected]
    report = (evidence/(TITLE+'.md')).read_text(encoding='utf-8')
    figures = sorted((evidence/'figures').glob('*.png'))
    assert len(figures) >= 6
    ui = json.loads((evidence/'ui/manifest.json').read_text())
    assert 6 <= len(ui['captures']) <= 10 and not ui['confirmed'] and not ui['page_errors']
    for name in ui['captures']:
        assert (evidence/'screenshots'/name).is_file()
        assert name in report
    for path in figures:
        assert path.name in report and path.stat().st_size > 1000
    for item in ui['comparisons']:
        assert item['quality_matches'] and item['inventory_unchanged']
        saved = json.loads((evidence/'ui'/f"{item['case_id']}.json").read_text())
        for execution in saved['comparison']['executions']:
            row = next(r for r in raw if r['case_id']==item['case_id'] and
                       r['engine_code']==execution['method'] and r['repetition']=='1')
            assert execution['placed_units'] == int(row['placed_units'])
            for key in ('aprovechamiento_pct','merma_mm2','retazo_recuperable_mm2'):
                assert Decimal(str(execution['metrics'][key])) == Decimal(row[key])
    suites=[]
    for path in sorted((evidence/'tests').glob('*.xml')):
        for suite in ET.parse(path).getroot().iter('testsuite'):
            assert int(suite.attrib['failures']) == int(suite.attrib['errors']) == 0
            suites.append(dict(file=path.name, **suite.attrib))
    assert suites
    files = {str(p.relative_to(evidence)).replace('\\','/'): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(evidence.rglob('*')) if p.is_file() and p.name != 'evidence-manifest.json'}
    result.update(figures=len(figures),captures=len(ui['captures']),tests=suites,files=files)
    (evidence/'evidence-manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    return {k:v for k,v in result.items() if k != 'files'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('directory',type=Path)
    parser.add_argument('--delivery',action='store_true'); args=parser.parse_args()
    print(json.dumps((verify_delivery if args.delivery else verify)(args.directory),indent=2))

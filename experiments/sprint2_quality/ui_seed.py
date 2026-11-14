"""Seed one measured case ONLY into the owned tests.e2e_server loopback DB.

Run after starting python -m tests.e2e_server. Never accepts DATABASE_URL.
"""
import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile

from sqlalchemy import create_engine, select, update, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('case_id'); args=parser.parse_args()
    state=json.loads((Path(tempfile.gettempdir())/'newglass-orders-e2e-8017.json').read_text())
    root=Path(state['root']).resolve()
    assert root.parent==Path(tempfile.gettempdir()).resolve() and root.name.startswith('newglass-orders-e2e-')
    assert (root/'data/postmaster.pid').exists(), 'Owned test PostgreSQL must be running'
    url=make_url(state['database_url']); assert url.host=='127.0.0.1' and url.username=='r1_test'
    os.environ['DATABASE_URL'] = state['database_url']
    from app.models import Configuracion, Plancha, Retazo, Usuario, TipoVidrio, TipoVidrioEspesor
    from app.modules.orders.infrastructure.repositories import SQLAlchemyOrderRepository
    from experiments.optimization.loader import area
    case=json.loads((Path(__file__).parent/'cases'/f'{args.case_id}.json').read_text())
    now=datetime.now(timezone.utc)
    with Session(create_engine(url)) as session:
        # Demo migrations insert fixed IDs. Align their sequences only in this
        # verified disposable database before creating synthetic evidence rows.
        from app.shared.database import Base
        for table in Base.metadata.sorted_tables:
            columns = list(table.primary_key.columns)
            if len(columns) != 1:
                continue
            column = columns[0]
            sequence = session.scalar(text('SELECT pg_get_serial_sequence(:t, :c)'),
                                      {'t': table.name, 'c': column.name})
            if sequence:
                from sqlalchemy import func
                maximum = session.scalar(select(func.max(column)))
                if maximum is not None:
                    session.execute(text('SELECT setval(:sequence, :value, true)'),
                                    {'sequence': sequence, 'value': maximum})
        actor=session.scalar(select(Usuario).where(Usuario.usuario=='A70000003'))
        assert actor is not None
        session.execute(update(Plancha).values(estado=False)); session.execute(update(Retazo).values(estado=False))
        previous=list(session.scalars(select(Configuracion)))
        for row in previous: row.vigente=False
        cfg=case['config']; limits=case['residual_limits']
        session.add(Configuracion(version=max((c.version for c in previous),default=0)+1,
            margen_mm=cfg['margin_mm'],separacion_mm=cfg['separation_mm'],resolucion_raster_mm=cfg['resolution_mm'],
            paso_angular_grados=cfg['angular_step_deg'],ancho_min_retazo_mm=limits['minimum_width_mm'],
            alto_min_retazo_mm=limits['minimum_height_mm'],vigente=True,id_usuario_creacion=actor.id_usuario,fecha_creacion=now))
        glass=TipoVidrio(nombre=f'Calidad {args.case_id}',estado=True)
        session.add(glass); session.flush()
        session.add(TipoVidrioEspesor(id_tipo_vidrio=glass.id_tipo_vidrio,espesor_mm=Decimal(6)))
        session.flush()
        stock_map=[]
        for rank,row in enumerate(case['stock']):
            common=dict(id_tipo_vidrio=glass.id_tipo_vidrio,espesor_mm=6,estado=True,fecha_registro=now-timedelta(seconds=rank))
            if row['source']=='PLANCHA':
                source=Plancha(ancho_mm=row['geometry']['width_mm'],alto_mm=row['geometry']['height_mm'],cantidad=row['quantity'],**common)
            else:
                source=Retazo(codigo=f'QUALITY-{args.case_id}-{rank}',geometria=row['geometry'],area_mm2=area(row['geometry']),**common)
            session.add(source); session.flush()
            stock_map.append(dict(original=row['material_id'],source=row['source'],
                                  persisted=source.id_plancha if row['source']=='PLANCHA' else source.id_retazo))
        session.commit()
        pieces=[]
        for row in case['pieces']:
            geometry=row['geometria']
            pieces.append(dict(tipo_forma=row['tipo_forma'],cantidad=row['cantidad'],id_tipo_vidrio=glass.id_tipo_vidrio,
                espesor_mm=6,geometria=geometry,dimensiones=None if geometry['type']=='POLIGONO_CONVEXO'
                else {k:v for k,v in geometry.items() if k!='type'},area_mm2=area(geometry)))
        order=SQLAlchemyOrderRepository(session).create_order(actor.id_usuario,pieces,id_cliente=41)
        print(json.dumps(dict(case_id=args.case_id,order_id=order,stock_map=stock_map,glass_id=glass.id_tipo_vidrio)))


if __name__=='__main__': main()

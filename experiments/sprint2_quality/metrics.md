# Metricas y alineacion metodologica

A = area completa de fuentes fisicas utilizadas (no area util tras margen).
P = area geometrica de piezas colocadas. R = recuperable propuesto.

| Medida | Formula / fuente | Unidad | Interpretacion |
|---|---|---|---|
| Aprovechamiento | 100 * P / A; calculate_metrics | % | Calidad de solucion del dominio |
| Merma | A - P - R | mm2 | Residuo no recuperado por la politica actual |
| Merma derivada | 100 * merma_mm2 / A | % | Campo merma_pct_derivada; no sustituye merma |
| Recuperable | retazo_recuperable_mm2 | mm2 | Un rectangulo principal por fuente, sujeto a minimos |
| Piezas | placed_units / total_units | unidades | Cobertura observada |
| Cobertura | 100 * placed_units / total_units | % | Adecuacion funcional en casos evaluados |
| Tiempo | computation_time_ms | ms | Motor + metricas, excluidos callbacks |
| Fuentes | planchas, retazos, total fisico | unidades | Contexto de consumo de material |
| Validez | Oraculo independiente posterior | pasa/falla | Correccion del resultado raster |

Merma NO es 100 - aprovechamiento. El recuperable no representa todos los
residuos geometricamente reutilizables. Sin colocaciones las metricas de area
pueden ser nulas: no se convierten a cero. Tiempo es reloj transcurrido, no CPU,
latencia HTTP ni tiempo total del usuario. Tiene resolucion entera de 1 ms.

## ISO/IEC 25010 / ISO/IEC 25023

Se utiliza lenguaje de alineacion, no certificacion. ISO/IEC 25023:2016 aporta
medicion cuantitativa de calidad en relacion con el modelo de ISO/IEC 25010.
No se atribuyen identificadores normativos no verificados ni umbrales ISO.

| Evidencia | Relacion metodologica |
|---|---|
| Tiempo computacional | Eficiencia de desempeno / comportamiento temporal |
| Cobertura y validez | Evidencia cuantitativa de adecuacion funcional en estos casos |
| Aprovechamiento, merma, recuperable | Metricas de dominio NewGlass; ISO no prescribe estas formulas |

Cobertura de piezas no equivale por si sola a completitud funcional del producto.
El experimento no evalua todas las caracteristicas del modelo de calidad.

Fuente oficial consultada: https://www.iso.org/standard/35747.html
No se presupone equivalencia entre ediciones de ISO/IEC 25010 ni se declara
conformidad normativa a partir de esta bateria limitada.

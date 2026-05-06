Resumen-- En las organizaciones data-intensive, los datos
atraviesan decenas de transformaciones antes de llegar a reportes
y dashboards. Con el tiempo, este flujo se vuelve tan complejo que
nadie puede responder dónde originan los números críticos. El
Data Lineage resuelve esto rastreando el origen de cada dato a
nivel columna. Este trabajo propone un sistema de análisis estático
de código fuente que reconstruye automáticamente el linaje de
datos y lo mantiene actualizado de forma continua a través de
integración con repositorios Git. El enfoque híbrido combina un
parser determinístico (sqlglot) para SQL estándar con un modelo
de lenguaje (LLM) que interpreta casos complejos: queries
dinámicas, transformaciones en pandas/PySpark y
procedimientos almacenados. El resultado se almacena en una
base de datos de grafos (Neo4j) y se expone a través de una interfaz
web y API REST. La validación demuestra que el enfoque híbrido
mejora significativamente la cobertura y precisión del lineage
respecto al parsing determinístico solo, resolviendo el vacío
tecnológico entre herramientas comerciales inaccesibles y
soluciones open-source incompletas.
Palabras clave--: data lineage, análisis estático de código,
inteligencia artificial, cumplimiento regulatorio, trazabilidad de
datos, bases de datos de grafos, SQL, Python, BCBS 239, SOX.
I. INTRODUCCIÓN
En las organizaciones modernas, los datos son un activo crítico.
Sin embargo, comprender el origen de un dato específico — por
ejemplo, cómo se calcula un KPI regulatorio — se ha
convertido en una tarea casi imposible. Los datos pasan por
docenas de transformaciones en múltiples etapas: se extraen
desde fuentes heterogéneas, se limpian en procesos
intermedios, se combinan con otros datasets, se reprovesan con
lógica de negocio compleja y finalmente llegan a un reporte o
dashboard. A medida que las pipelines de datos crecen en
volumen y complejidad, surge una pregunta fundamental que
nadie en la organización puede responder con certeza: ¿de
dónde proviene realmente este número?
El concepto de Data Lineage (linaje de datos) apunta a resolver
exactamente este problema. Se define como la capacidad de
rastrear la procedencia de un dato, identificar todos los pasos de
transformación que sufrió a lo largo de su ciclo de vida y
visualizar la cadena completa de origen hasta su presentación
final. En la industria financiera, esta trazabilidad no es una
conveniencia técnica: es una obligación regulatoria.
Normativas como BCBS 239 (principios de agregación de datos
de riesgo del Comité de Basilea), SOX (Sarbanes-Oxley Act) y
MiFID II (Markets in Financial Instruments Directive) exigen
explícitamente que las instituciones financieras demuestren la
procedencia de los datos utilizados en reportes regulatorios.
Incumplimiento genera multas de millones de dólares,
sanciones administrativas e incluso pérdida de licencia
operativa.
Hoy, las organizaciones resuelven esta obligación de dos modos
insatisfactorios. El primero es documentación manual: los
equipos crean spreadsheets o wikis que describen dónde
originan los datos y cómo se transforman. El problema es que
esta documentación inevitablemente se desactualiza: cada
cambio en el código requiere actualizar manualmente la
documentación, lo que nunca ocurre consistentemente. El
segundo modo es adquirir herramientas comerciales complejas
— Collibra, Atlan, Informatica — que automaticen la
trazabilidad. Estas soluciones funcionan bien pero cuestan entre
USD 100.000 y USD 500.000 anuales, disponible solo para
grandes empresas con presupuestos de TI significativos.
Entre estos dos extremos existe un vacío tecnológico que este
proyecto propone ocupar.
II. JUSTIFICACIÓN
Tres factores hacen que este proyecto sea relevante y
oportuno:
A. Obligación regulatoria sin solución accesible
Regulaciones como BCBS 239 (principios de agregación de
datos de riesgo del Comité de Basilea), SOX (Sarbanes-Oxley
Act) y MiFID II (Markets in Financial Instruments Directive)
exigen que las instituciones financieras demuestren la
procedencia de los datos utilizados en reportes regulatorios.
Incumplimiento genera multas de millones de dólares,
sanciones administrativas e incluso pérdida de licencia
operativa. Hoy, las organizaciones resuelven esta obligación
con documentación manual — proceso lento, propenso a
errores y que se desactualiza con cada cambio de código. Las
Data Lineage Estático con IA: Reconstrucción
Automática y Continua del Linaje de Datos a
Nivel Columna mediante Análisis de Código
Fuente
Shanon Samora
Profesora: Ana Darcacha – Universidad de Palermo
2
herramientas comerciales que automaticen la trazabilidad a
nivel columna (Collibra, Atlan, Informatica, Governance
Catalog) tienen costos entre USD 100.000 y USD 500.000
anuales, disponible solo para grandes empresas. Una solución
autoalojable y de bajo costo resolvería una necesidad urgente
que millones de equipos de datos en el mundo enfrentan hoy.
B. Vacio tecnológico concreto
Existe una brecha entre las herramientas open-source
(OpenLineage, Marquez, dbt) que solo cubren lineage en
tiempo de ejecución, y las comerciales que cubren lineage
estático pero a costos prohibitivos. El problema es que el código
legacy — scripts SQL, notebooks de Jupyter, transformaciones
en Python con queries dinámicas, procedimientos almacenados
— representa la mayoría del código real de una organización,
pero queda completamente invisible para las herramientas de
lineage runtime. Ninguna solución open-source combina
parsing determinístico con interpretación por IA para extraer
linaje de código fuente sin ejecución. Este proyecto apunta a
cubrir ese espacio intermedio.
C. Valor de negocio tangible más allá del
compliance
El linaje continuo habilita tres capacidades inmediatas por
las cuales una organización invertiría: (a) análisis de impacto
automatizado — antes de modificar una columna en una tabla
fuente, saber instantáneamente qué reportes, dashboards y
pipelines downstream se ven afectados, reduciendo incidentes
por cambios no evaluados; (b) reducción del tiempo de
debugging — cuando un KPI muestra un valor incorrecto,
trazar la cadena completa de origen en segundos en lugar de
horas de investigación manual; y (c) documentación siempre
actualizada — el linaje se regenera con cada cambio de código,
eliminando la necesidad de mantener documentación manual
que inevitablemente se desactualiza.
III. ESTADO DEL ARTE
El Data Lineage se consolidó como una capacidad central en
arquitecturas modernas de datos, ya que permite identificar el
origen, las transformaciones y el consumo de la información a
lo largo de pipelines analíticos y regulatorios. En términos
prácticos, su valor no se limita a la documentación técnica:
también habilita análisis de impacto, auditoría, cumplimiento
normativo y resolución más rápida de incidentes en entornos
donde los datos se transforman en múltiples etapas antes de
llegar a reportes o dashboards.
Los enfoques existentes pueden agruparse, en primer lugar,
en soluciones de observación en runtime. Herramientas como
OpenLineage, Marquez y distintos conectores de orquestación
capturan eventos durante la ejecución de procesos, lo que
permite obtener trazabilidad operativa en tiempo real. Sin
embargo, su cobertura depende de que los pipelines estén
instrumentados. Esto deja fuera una parte importante del
ecosistema real de una organización: scripts legacy, notebooks
ejecutados manualmente, procesos ad hoc y transformaciones
dispersas en Python o PySpark.
Una segunda familia está compuesta por plataformas de
gobierno y catálogo de datos, como Collibra, Atlan, Informatica
o Microsoft Purview. Estas soluciones integran metadatos,
catálogo y visualización de lineage, y suelen ofrecer
capacidades útiles para trazabilidad e impacto de cambios. No
obstante, su adopción suele verse limitada por el costo, la
complejidad de implementación y la dependencia de
ecosistemas propietarios, lo que reduce su accesibilidad para
equipos medianos o proyectos que requieren una alternativa
autoalojable.
En paralelo, existen propuestas de lineage estático basadas
en parsers SQL y análisis de dependencias del código fuente.
Este enfoque mejora la cobertura sobre repositorios sin
necesidad de ejecutar pipelines, pero presenta límites cuando
aparecen SQL dinámico, macros, procedimientos almacenados
poco documentados o transformaciones escritas en
Python/PySpark. En esos casos, la extracción del linaje a nivel
columna pierde precisión justamente en los escenarios que más
valor tienen para auditoría y análisis de impacto.
Más recientemente, comenzaron a explorarse modelos de
lenguaje (LLM) para comprensión de código, extracción
semántica y asistencia en documentación técnica. Estos avances
abren la posibilidad de interpretar patrones que los parsers
determinísticos no resuelven con facilidad, especialmente en
lógica compleja o parcialmente implícita. En paralelo, las bases
de datos orientadas a grafos —como Neo4j— se consolidaron
como una representación adecuada para modelar relaciones
entre tablas, columnas, transformaciones y activos de datos,
facilitando consultas de recorrido e impacto con mayor
expresividad.
A partir de este relevamiento, se identifica un vacío
concreto: no se observa una solución accesible que combine de
forma robusta análisis estático multi-lenguaje, lineage a nivel
columna, cobertura de casos no instrumentados y actualización
continua integrada al flujo de desarrollo. En ese espacio se
posiciona este trabajo, proponiendo un enfoque híbrido que
combina parsing determinístico e interpretación asistida por IA
para mejorar la cobertura y la precisión sin depender de la
ejecución en producción.
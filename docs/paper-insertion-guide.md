# Guía de inserción — Secciones nuevas para el paper

> Este documento indica **exactamente qué texto agregar y dónde** en tu paper IEEE.
> Todo el contenido nuevo respeta el estilo académico en español del paper existente.
> Los números de sección (V.A, V.B, etc.) siguen la numeración IEEE que ya usás.

---

## Mapa de cambios

| Sección | Estado actual en el PDF | Acción |
|---------|------------------------|--------|
| I. Introducción | ✅ Completa | No tocar |
| II. Justificación | ✅ Completa | No tocar |
| III. Marco Teórico | ✅ Completa | No tocar |
| IV. Estado del Arte | ✅ Completa | No tocar |
| V. Desarrollo (intro) | ⚠️ Reescribir primer párrafo | Reemplazar |
| V.A. Arquitectura general | ⚠️ Reescribir | Reemplazar |
| V.B – V.L | ❌ No existen | **Agregar (nuevo)** |
| VI. Conclusiones | ❌ No existe | **Agregar (nuevo)** |
| VII. Líneas Futuras | ❌ No existe | **Agregar (nuevo)** |
| VIII. Glosario | ❌ No existe | **Agregar (nuevo)** |
| Referencias | ⚠️ Solo [1]–[15] | **Agregar [16]–[22]** |

---

## PASO 1 — Reemplazar el párrafo introductorio de V. DESARROLLO

**Borrá** el párrafo que actualmente dice:

> *"La solución propuesta se diseña como una aplicación web conectada al ciclo de desarrollo de software..."*

**Reemplazalo por:**

La solución propuesta se diseña como una aplicación que se conecta al ciclo de desarrollo de software, con capacidad para analizar uno o varios repositorios de código y reconstruir el linaje de datos de forma continua. A diferencia de una herramienta que se ejecuta una sola vez sobre un proyecto cerrado, este sistema se integra con GitHub para revisar cambios en cada pull request, actualizar el grafo de lineage y exponer los impactos potenciales antes de que el código llegue a producción. De este modo, el linaje deja de ser un artefacto estático y pasa a funcionar como una capacidad operativa del proceso de desarrollo.

---

## PASO 2 — Reemplazar V.A. Arquitectura general

**Borrá** el párrafo actual de Arquitectura general (el que menciona solo tres capas) y **reemplazalo por:**

*A. Arquitectura general*

La arquitectura se organiza en cuatro capas: ingesta, análisis, almacenamiento y exposición.

La capa de ingesta se encarga de obtener el contenido de los repositorios, identificar archivos relevantes (SQL, Python, JSON de Azure Data Factory) y detectar cuándo ocurre un cambio sobre una rama o un pull request. El sistema soporta análisis local de directorios, análisis de repositorios remotos clonados vía Git y análisis multi-repo donde se combinan grafos de distintos orígenes.

La capa de análisis procesa cada archivo detectado mediante un motor híbrido que decide, según el tipo de archivo y la complejidad del código, si aplica parsing determinístico o interpretación por LLM. El resultado de cada análisis es un fragmento del grafo de lineage (nodos y aristas) que se fusiona con el grafo global.

La capa de almacenamiento persiste el grafo completo en Neo4j, una base de datos orientada a grafos que permite consultas de recorrido, ancestros, descendientes e impacto transitivo de forma nativa.

La capa de exposición presenta el lineage a través de una API REST (FastAPI) con endpoints para análisis, consulta de grafo, búsqueda, impacto y validación de pull requests, y una interfaz web para visualización interactiva del grafo.

---

## PASO 3 — Agregar subsecciones V.B a V.L (después de V.A)

Insertá cada una de estas subsecciones **a continuación de V.A**, manteniendo el orden.

---

### V.B — Modelo de datos

*B. Modelo de datos*

El grafo de lineage se modela con dos primitivas: nodos (LineageNode) y aristas (LineageEdge).

Cada nodo tiene un identificador único, un nombre descriptivo, un tipo (NodeType) y un campo opcional source_repo que indica de qué repositorio proviene. Los tipos de nodo soportados son: TABLE, VIEW, COLUMN, PROCEDURE, PYTHON_FUNCTION, FILE, ADF_PIPELINE, ADF_DATASET y ADF_DATAFLOW. Esta taxonomía cubre activos SQL clásicos, artefactos de código Python y componentes de pipelines de Azure Data Factory.

Cada arista conecta un nodo origen con un nodo destino y tiene un tipo (EdgeType), una descripción opcional de la transformación aplicada y un nivel de confianza (confidence) que indica si la relación fue extraída por parsing determinístico (1.0) o por interpretación LLM (típicamente 0.7–0.9). Los tipos de arista son: HAS_COLUMN, DERIVES_FROM, READS_FROM, WRITES_TO, DEFINED_IN, COPIES_TO y TRIGGERS.

El grafo completo (LineageGraph) expone métodos para agregar nodos y aristas, fusionar grafos parciales (merge) y serializar a diccionario. La fusión es idempotente: si un nodo o arista ya existe, no se duplica.

---

### V.C — Motor de análisis híbrido

*C. Motor de análisis híbrido*

El componente central del sistema es el motor de análisis (engine.py), que orquesta la elección del parser adecuado para cada archivo. El flujo de decisión es el siguiente:

1) Se recibe un archivo con su ruta y contenido.
2) Se detecta el tipo de archivo: .sql se dirige al parser SQL, .py al parser Python/LLM, y archivos JSON en carpetas pipeline/, dataset/ o dataflow/ al parser de Azure Data Factory.
3) Para archivos SQL, se intenta primero el parser determinístico (sqlglot). Si el contenido incluye patrones que el parser no resuelve (SQL dinámico, EXEC con variables, macros), se activa el fallback al LLM.
4) Para archivos Python, se invoca siempre el parser LLM porque las transformaciones en pandas, PySpark o SQLAlchemy requieren interpretación semántica.
5) Para archivos ADF, se usa un parser determinístico especializado que interpreta la estructura JSON de pipelines, datasets y dataflows.

Este enfoque híbrido maximiza la precisión en casos estándar (parsing determinístico) y extiende la cobertura a casos complejos (LLM), registrando el nivel de confianza de cada relación extraída.

---

### V.D — Parser determinístico SQL

*D. Parser determinístico SQL*

El parser SQL (parser_sql.py) utiliza la librería sqlglot [16] para construir un AST (Abstract Syntax Tree) del código SQL y extraer relaciones de lineage a nivel columna. Su cobertura incluye: sentencias SELECT con joins, subqueries y CTEs, que permiten identificar qué columnas de qué tablas alimentan cada columna de salida; sentencias CREATE TABLE ... AS SELECT e INSERT INTO ... SELECT, que detectan la tabla destino y las columnas fuente; definiciones de vistas (CREATE VIEW), que modelan la vista como nodo y vinculan sus columnas a las tablas subyacentes; procedimientos almacenados (CREATE PROCEDURE), de los cuales se extrae el cuerpo y se analizan las sentencias internas como SQL independiente; y transformaciones con funciones (COALESCE, CASE, agregaciones), cuya expresión se registra en la arista correspondiente.

El parser procesa archivos con múltiples sentencias separadas por punto y coma, lo que permite analizar scripts de migración y pipelines SQL completos en una sola pasada.

---

### V.E — Parser LLM para Python y SQL complejo

*E. Parser LLM para Python y SQL complejo*

El parser LLM (parser_llm.py) envía el código fuente a un modelo de lenguaje (GPT-4.1-mini vía API de OpenAI [17]) con un prompt estructurado que solicita la extracción de nodos y aristas en formato JSON. El prompt incluye instrucciones para identificar tablas, columnas y funciones referenciadas en el código; detectar operaciones de lectura (pd.read_sql, spark.read, SELECT) y escritura (to_sql, INSERT, saveAsTable); extraer relaciones DERIVES_FROM a nivel columna cuando la lógica lo permite; y devolver el resultado en un schema predefinido que el sistema parsea y convierte a LineageGraph.

Se utiliza cuando el parser determinístico no puede resolver el contenido: SQL dinámico construido con concatenación de strings, transformaciones en pandas/PySpark, notebooks con lógica mixta y procedimientos almacenados con EXEC dinámico.

---

### V.F — Parser de Azure Data Factory

*F. Parser de Azure Data Factory*

El parser ADF (parser_adf.py) interpreta los archivos JSON que Azure Data Factory [22] exporta al repositorio Git cuando se configura integración con control de versiones. Soporta tres tipos de artefacto: pipelines, de los cuales se extraen las actividades (las actividades Copy generan aristas COPIES_TO entre el dataset de entrada y el de salida, las actividades ExecutePipeline generan aristas TRIGGERS entre el pipeline padre y el hijo, y las actividades de DataFlow referencian el dataflow correspondiente); datasets, que se representan como nodos ADF_DATASET con metadata sobre la estructura; y dataflows, de los cuales se analizan las transformaciones source y sink para generar aristas que conectan los datasets de origen con los de destino a través del dataflow como nodo intermedio.

Esta cobertura permite incluir en el grafo de lineage las orquestaciones y movimientos de datos que ocurren fuera del código SQL o Python, completando la visión end-to-end del flujo de datos en organizaciones que utilizan ADF.

---

### V.G — Análisis multi-repositorio

*G. Análisis multi-repositorio*

En entornos reales, el código de datos de una organización no vive en un solo repositorio. Es común tener un repositorio para SQL (DDL, stored procedures), otro para transformaciones Python (ETL, notebooks) y otro para pipelines de orquestación (ADF, Airflow). El sistema soporta análisis multi-repo mediante la siguiente mecánica: se define una lista de fuentes (RepoSource) en la configuración, cada una con una URL de repositorio y una rama; el motor clona cada repositorio en un directorio temporal; se ejecuta el análisis estándar sobre cada repositorio, etiquetando cada nodo con su source_repo de origen; y los grafos parciales se fusionan en un grafo global mediante el método merge() de LineageGraph.

Este enfoque permite, por ejemplo, descubrir que una tabla creada en el repositorio SQL es leída por un pipeline en el repositorio ADF y transformada por un script en el repositorio Python, reconstruyendo la cadena completa de lineage cross-repo.

---

### V.H — Almacenamiento en Neo4j

*H. Almacenamiento en Neo4j*

El grafo de lineage se persiste en Neo4j [18] a través del módulo graph_store.py, que traduce los nodos y aristas del modelo interno a operaciones Cypher. Los nodos se crean con MERGE (upsert) usando el id como clave, y las aristas se crean como relaciones tipadas entre nodos. Esto garantiza idempotencia: ejecutar el mismo análisis dos veces no genera duplicados.

Neo4j permite consultas de recorrido eficientes: dado un nodo, se pueden obtener todos sus ancestros (upstream) o descendientes (downstream) con una sola query Cypher de longitud variable. Esta capacidad es la base del análisis de impacto: cuando se modifica una tabla o columna, el sistema recorre el grafo downstream para identificar todos los activos afectados.

---

### V.I — API REST

*I. API REST*

La API (api.py) expone los siguientes endpoints principales a través de FastAPI [19]: POST /analyze recibe una ruta local y ejecuta el análisis completo retornando el grafo de lineage; POST /analyze-multi recibe una lista de fuentes de repositorio y ejecuta análisis multi-repo; GET /graph retorna el grafo completo almacenado en Neo4j; GET /upstream/{node_id} y GET /downstream/{node_id} retornan los ancestros o descendientes de un nodo; GET /impact/{node_id} retorna el análisis de impacto (nodos afectados downstream); GET /search permite buscar nodos por nombre o tipo; y POST /pr-check recibe los parámetros de un pull request y ejecuta el análisis de impacto de lineage, retornando un reporte estructurado.

Todos los endpoints devuelven JSON y están documentados automáticamente vía OpenAPI (Swagger UI).

---

### V.J — Integración CI/CD con GitHub Actions

*J. Integración CI/CD con GitHub Actions*

Una de las contribuciones principales de este trabajo es la integración del análisis de lineage directamente en el flujo de CI/CD. El sistema incluye un GitHub Action [20] (lineage-check.yml) que se activa automáticamente cuando se abre o actualiza un pull request. El flujo comprende cinco etapas: primero, se clona el repositorio con historial completo (fetch-depth: 0) para poder comparar la rama del PR con la rama base; segundo, se ejecuta el motor de lineage sobre la rama base (main) para obtener el grafo de lineage previo al cambio; tercero, se ejecuta el motor sobre la rama del PR para obtener el grafo posterior al cambio; cuarto, el módulo diff.py compara ambos grafos, identifica nodos y aristas agregados, eliminados y modificados, y ejecuta un recorrido BFS downstream para determinar todos los activos impactados por los cambios; y quinto, el resultado se formatea como un reporte Markdown y se publica como comentario en el pull request, indicando cantidad de cambios en el grafo, nodos impactados, nivel de riesgo y detalle de cada cambio.

Este flujo permite que los revisores del PR vean el impacto de lineage antes de aprobar el merge, reduciendo el riesgo de cambios no evaluados que afecten reportes o pipelines downstream.

---

### V.K — Motor de diff y análisis de impacto

*K. Motor de diff y análisis de impacto*

El módulo diff.py implementa la comparación entre dos grafos de lineage (before y after). El algoritmo compara nodos por id: los presentes solo en after son ADDED, los presentes solo en before son REMOVED, y los presentes en ambos pero con atributos diferentes son MODIFIED. La misma lógica se aplica a las aristas, comparando por la combinación de source_id, target_id y edge_type. Para cada nodo o arista con cambios, se ejecuta un recorrido BFS en el grafo after para recolectar todos los nodos downstream impactados. El resultado es un LineageDiff con la lista de cambios y el conjunto de nodos impactados.

El resultado se expone tanto como estructura de datos (para la API) como reporte Markdown (para el comentario en GitHub).

---

### V.L — Validación

*L. Validación*

Para validar el sistema se diseñó un repositorio de ejemplo (sample_repo/) que contiene artefactos representativos de un entorno real: cuatro archivos SQL con DDL de staging tables, intermediate views, reporting tables y stored procedures que cubren CREATE TABLE, CREATE VIEW, INSERT INTO ... SELECT, joins, subqueries, CTEs y procedimientos almacenados; un archivo Python con un pipeline de transformación que lee de base de datos con pandas, aplica transformaciones y escribe resultados, representando el patrón típico de ETL en Python; y seis archivos JSON (dos pipelines, tres datasets y un dataflow) que modelan un flujo de ingesta y transformación en Azure Data Factory.

El análisis del repositorio de ejemplo produce un grafo con nodos de tipo TABLE, VIEW, COLUMN, PROCEDURE, PYTHON_FUNCTION, ADF_PIPELINE, ADF_DATASET y ADF_DATAFLOW, y aristas que conectan toda la cadena desde las tablas fuente hasta los reportes finales.

Adicionalmente, se implementó una suite de 23 tests automatizados con pytest que cubren: 9 tests para el motor de diff, que verifican la detección correcta de nodos y aristas agregados, eliminados y modificados, y el recorrido de impacto downstream; 5 tests para el analizador de pull requests, que verifican la generación del reporte Markdown, la detección de archivos relevantes y la estructura del resultado; y 9 tests para el parser ADF, que verifican la correcta extracción de nodos y aristas desde pipelines, datasets y dataflows JSON. Todos los tests pasan exitosamente y se ejecutan como parte del flujo de CI/CD.

---

## PASO 4 — Renumerar la sección de Referencias a VI → VIII

Tu sección actual "VI. REFERENCIAS" pasa a ser **"VIII. REFERENCIAS"** porque se agregan dos secciones nuevas antes.

---

## PASO 5 — Agregar VI. CONCLUSIONES (antes de las Referencias)

Insertá esta sección **después de V.L (Validación)** y **antes de las Referencias**.

**VI. CONCLUSIONES**

El sistema desarrollado demuestra que es viable construir una solución de data lineage estático que combine parsing determinístico e interpretación por IA para reconstruir automáticamente el linaje de datos a nivel columna desde código fuente, sin necesidad de ejecutar los pipelines en producción.

Las principales contribuciones de este trabajo son: (1) un enfoque híbrido determinístico + LLM, donde el uso de sqlglot para SQL estándar garantiza precisión y velocidad en los casos resolubles por parsing, mientras que el fallback a GPT-4.1-mini extiende la cobertura a SQL dinámico, transformaciones Python y procedimientos almacenados complejos, y el campo confidence en cada arista permite distinguir el origen de cada relación extraída; (2) cobertura multi-lenguaje y multi-plataforma, ya que el sistema analiza SQL, Python y artefactos de Azure Data Factory de forma unificada, produciendo un grafo de lineage integrado que cubre toda la cadena de datos end-to-end, y el soporte multi-repo permite combinar grafos de distintos repositorios; (3) integración continua con el flujo de desarrollo, incorporando el análisis de lineage como paso automático en los pull requests via GitHub Actions, lo que transforma el lineage de un artefacto estático a una capacidad operativa que informa decisiones de merge en tiempo real; (4) análisis de impacto preventivo, donde el motor de diff y el recorrido BFS downstream permiten identificar, antes del merge, todos los activos de datos que se verían afectados por un cambio propuesto, reduciendo el riesgo de incidentes por modificaciones no evaluadas; y (5) arquitectura extensible, cuyo diseño modular (parsers, engine, graph store, API) permite incorporar nuevos lenguajes, plataformas de orquestación o backends de almacenamiento sin modificar el núcleo del sistema.

El principal limitante del enfoque LLM es el costo por token y la latencia de las llamadas a la API, lo que hace que el análisis de repositorios muy grandes pueda ser lento o costoso. Este limitante se mitiga mediante el enfoque híbrido: el LLM solo se invoca cuando el parser determinístico no puede resolver el contenido.

---

## PASO 6 — Agregar VII. LÍNEAS FUTURAS

Insertá esta sección **después de Conclusiones** y **antes de las Referencias**.

**VII. LÍNEAS FUTURAS**

A partir del trabajo realizado, se identifican las siguientes líneas de evolución: (1) soporte para dbt y Airflow, incorporando parsers para modelos dbt (análisis de archivos .sql con macros Jinja y el manifest.json) y DAGs de Apache Airflow (análisis de archivos Python con decoradores @task y dependencias entre tasks); (2) lineage temporal, registrando versiones históricas del grafo de lineage con timestamps para consultar cómo era el lineage de un activo en una fecha específica y comparar su evolución a lo largo del tiempo; (3) integración con catálogos de datos, conectando el grafo de lineage con plataformas de catálogo (DataHub, OpenMetadata) para enriquecer los metadatos con información de lineage automatizada; (4) lineage de notebooks Jupyter, desarrollando un parser especializado que interprete celdas de notebooks (.ipynb), detecte dependencias entre celdas y extraiga lineage de código mixto (SQL + Python en el mismo notebook); (5) scoring de calidad del lineage, implementando métricas de completitud y confianza del grafo para que los equipos puedan evaluar la madurez de su trazabilidad; (6) soporte para Spark SQL y transformaciones PySpark nativas, extendiendo el parser determinístico para interpretar operaciones de DataFrame de PySpark sin necesidad de recurrir al LLM; y (7) plugin para IDE, desarrollando una extensión de Visual Studio Code que muestre el lineage de un archivo o función directamente en el editor, permitiendo a los desarrolladores consultar el impacto de sus cambios antes de hacer commit.

---

## PASO 7 — Agregar Glosario (opcional, como Apéndice o sección IX)

Si tu profesora lo pide o si querés incluirlo como apéndice al final:

**IX. GLOSARIO**

**AST** (Abstract Syntax Tree): representación jerárquica del código fuente como árbol de nodos sintácticos, utilizada por parsers para analizar la estructura del código sin ejecutarlo.

**BFS** (Breadth-First Search): algoritmo de recorrido de grafos que explora todos los vecinos de un nodo antes de avanzar al siguiente nivel de profundidad. Se usa en este trabajo para calcular impacto downstream.

**CI/CD** (Continuous Integration / Continuous Delivery): práctica de desarrollo que automatiza la integración, pruebas y despliegue de código mediante pipelines que se ejecutan ante cada cambio.

**Column-level lineage**: trazabilidad que vincula cada columna de salida con sus columnas de entrada y la transformación aplicada, proporcionando la granularidad más fina de lineage.

**CTE** (Common Table Expression): construcción SQL (WITH ... AS) que define una subconsulta nombrada reutilizable dentro de una sentencia.

**DAG** (Directed Acyclic Graph): grafo dirigido sin ciclos utilizado para representar dependencias entre tareas, modelos o transformaciones.

**Data Lineage**: capacidad de rastrear el origen, las transformaciones y el destino de un dato a lo largo de su ciclo de vida en una organización.

**DDL** (Data Definition Language): subconjunto de SQL que define la estructura de la base de datos (CREATE TABLE, ALTER TABLE, DROP TABLE).

**ETL** (Extract, Transform, Load): patrón de integración de datos que extrae información de fuentes, la transforma y la carga en un destino analítico.

**LLM** (Large Language Model): modelo de inteligencia artificial entrenado sobre grandes volúmenes de texto, capaz de comprender y generar lenguaje natural y código.

**Parsing determinístico**: análisis de código mediante reglas gramaticales fijas que producen un resultado único y predecible para cada entrada válida.

**Pull Request (PR)**: mecanismo de revisión de código en plataformas como GitHub donde un desarrollador propone cambios y otros revisan antes de integrarlos a la rama principal.

**Runtime lineage**: lineage obtenido observando la ejecución real de pipelines y procesos, en contraste con el lineage estático obtenido del análisis de código fuente.

---

## PASO 8 — Agregar referencias [16]–[22]

Agregá estas 7 referencias **al final** de tu lista actual de [1]–[15]:

[16] sqlglot, "sqlglot: Python SQL Parser and Transpiler." Available: https://sqlglot.com/

[17] OpenAI, "OpenAI API Documentation." Available: https://platform.openai.com/docs/

[18] Neo4j, "Neo4j Graph Database Documentation." Available: https://neo4j.com/docs/

[19] FastAPI, "FastAPI Documentation." Available: https://fastapi.tiangolo.com/

[20] GitHub, "GitHub Actions Documentation." Available: https://docs.github.com/en/actions

[21] GitPython, "GitPython Documentation." Available: https://gitpython.readthedocs.io/

[22] Microsoft, "Azure Data Factory Documentation." Available: https://learn.microsoft.com/en-us/azure/data-factory/

---

## Estructura final del paper

Así debería quedar el índice completo:

```
I.    INTRODUCCIÓN                          (ya existe — no tocar)
II.   JUSTIFICACIÓN                         (ya existe — no tocar)
      A. Obligación regulatoria...
      B. Vacío tecnológico...
      C. Valor de negocio...
III.  MARCO TEÓRICO                         (ya existe — no tocar)
      A–G.
IV.   ESTADO DEL ARTE                       (ya existe — no tocar)
V.    DESARROLLO                            (reescribir intro + A, agregar B–L)
      A. Arquitectura general
      B. Modelo de datos
      C. Motor de análisis híbrido
      D. Parser determinístico SQL
      E. Parser LLM para Python y SQL complejo
      F. Parser de Azure Data Factory
      G. Análisis multi-repositorio
      H. Almacenamiento en Neo4j
      I. API REST
      J. Integración CI/CD con GitHub Actions
      K. Motor de diff y análisis de impacto
      L. Validación
VI.   CONCLUSIONES                          ← NUEVO
VII.  LÍNEAS FUTURAS                        ← NUEVO
VIII. REFERENCIAS                           (renumerar, agregar [16]–[22])
IX.   GLOSARIO (opcional)                   ← NUEVO
```

---

## Notas de estilo

- **Listas**: en el formato IEEE se evitan bullet points. El texto nuevo ya está redactado como prosa continua con enumeración dentro del párrafo (ej: "primero, ...; segundo, ...; tercero, ...") para que encaje con el estilo del paper.
- **Referencias inline**: las nuevas subsecciones citan [16]–[22] donde corresponde. Asegurate de que la numeración coincida con tu lista final.
- **Nombres técnicos**: se mantienen en inglés los que ya aparecían así en el paper (lineage, upstream, downstream, pull request, confidence, merge).
- **Longitud**: el contenido nuevo agrega aproximadamente 3–4 páginas al paper en formato IEEE de dos columnas.

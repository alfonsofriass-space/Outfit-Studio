# Outfit MVP Backend

Backend FastAPI que convierte una descripción de outfit en datos estructurados y
genera un outfit board con OpenAI. El producto es solo español en esta fase y dispone
del frontend local complementario `../outfit-app-front` en el mismo monorepo.

> Estado: MVP personal funcional con cuentas locales, ownership, revisión previa y
> archivo agrupado. La API todavía no debe exponerse públicamente: el registro es libre,
> todas las cuentas comparten la misma capacidad de gasto y aún no existen cuotas, rate
> limiting, recuperación de contraseña ni endurecimiento para despliegue.

## Acceso y propiedad

- `POST /auth/register` crea siempre una cuenta normal; el cliente no puede elegir el
  rol. `POST /auth/login` inicia una sesión firmada en una cookie `HttpOnly` y
  `POST /auth/logout` la elimina.
- Las contraseñas se guardan con hash Argon2 mediante `pwdlib`; nunca se devuelven por la
  API. Tras `alembic upgrade head` existe el administrador local `admin` / `test`.
- Cada outfit nuevo recibe el usuario autenticado como propietario. Una cuenta normal
  solo puede listar, abrir, generar, buscar, borrar o descargar imágenes de sus outfits;
  el administrador puede revisar todos.
- Las migraciones asignan al administrador todos los outfits creados antes de incorporar
  usuarios. La separación se aplica antes de cualquier llamada pagada.

## Flujo actual

1. Una heurística local descarta sin coste de IA el texto que no menciona ni una prenda
   ni un detalle visual. El contrato mínimo del outfit se comprueba después de la
   extracción, porque el diccionario local no reconoce todo el vocabulario de ropa y no
   puede afirmar que falta una prenda que el usuario sí ha escrito.
2. `gpt-5.4-nano` estructura las prendas y conserva relaciones de estilo que el usuario
   haya expresado, como una superposición, un accesorio con otro uso o un par
   desparejado. Solo puede responder `ok` o `needs_clarification`; el código decide si
   una salida inválida o insuficiente necesita `gpt-5.4-mini`, nunca el propio modelo ni
   un error operativo.
3. El código compone un prompt visual determinista: layout vertical con 1-3 piezas y
   composición ancha con rail semántico de accesorios desde 4. El prompt enumera las
   piezas exactas del board y la geometría escala con ellas, de modo que el modelo no
   complete el outfit con prendas ni accesorios que el usuario no ha descrito. Las
   prendas permanecen separadas salvo cuando una relación explícita exige otra
   colocación.
4. El cliente puede revisar el análisis, el coste estimado y el prompt exacto sin generar
   imagen mediante `generate_image=false`; una confirmación posterior crea la primera
   composición.
5. `gpt-image-2` genera una imagen `1024x1024` en calidad `low`.
6. SQLite persiste el outfit y las imágenes; cada una conserva su prompt exacto. Los PNG
   se guardan en `app/generated/` y
   se sirven desde `/images/<uuid>.png`.
7. Para cada flat-lay, el cliente puede revisar el prompt y el coste aproximado de una
   vista puesta. Solo otro botón explícito ejecuta una edición `1024x1536 low` usando el
   flat-lay como referencia; no repite el modelo de texto y guarda el resultado emparejado.
8. `GET /outfits` descubre también análisis pendientes y `GET /outfits/{id}` reconstruye
   análisis, composiciones, vistas puestas y regeneraciones restantes sin llamar a OpenAI.
9. `DELETE /outfits/{id}` elimina el outfit confirmado, sus filas dependientes y solo los
   PNG locales que tenía referenciados; tampoco llama a OpenAI.
10. Desde el detalle guardado se puede buscar una prenda concreta. El backend muestra
    antes una estimación conservadora de `≈ $0.03`, exige información suficiente, limita la
    petición a una o dos acciones `web_search` y guarda hasta tres candidatos verificables.

El límite es una imagen original más tres regeneraciones. Solo puede existir una
operación pagada activa por outfit —flat-lay, variación, vista puesta o búsqueda—: una
petición simultánea recibe HTTP `409` antes de llamar a OpenAI. La vista puesta no cuenta
como regeneración y solo puede existir una por cada flat-lay. El borrado comparte esa
misma reserva: si hay una operación activa devuelve `409` y no elimina nada.

## Requisitos e instalación

- Python 3.12 o superior.
- Una API key de OpenAI para las generaciones reales.

```bash
python3.12 -m venv venv
source venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
```

`requirements-dev.txt` instala el backend y las herramientas de desarrollo/CI. Las
versiones y la separación entre dependencias viven en `pyproject.toml`; los ficheros
`requirements*.txt` son entradas finas para una instalación uniforme. Para instalar
solo el runtime:

```bash
python -m pip install -r requirements.txt
```

Completa `OPENAI_API_KEY` en `.env`. La configuración disponible es:

| Variable | Default del proyecto | Uso |
|---|---|---|
| `OPENAI_API_KEY` | sin valor | Credencial de OpenAI |
| `SESSION_SECRET` | valor local de desarrollo | Firma de la cookie; cambiar antes de compartir |
| `SESSION_COOKIE_SECURE` | `false` | Usar `true` cuando la aplicación funcione sobre HTTPS |
| `OPENAI_TEXT_MODEL_PRIMARY` | `gpt-5.4-nano` | Extracción principal |
| `OPENAI_TEXT_MODEL_FALLBACK` | `gpt-5.4-mini` | Fallback de calidad |
| `OPENAI_PRODUCT_SEARCH_MODEL` | `gpt-5.4-nano` | Búsqueda web por prenda |
| `OPENAI_IMAGE_MODEL` | `gpt-image-2` | Generación visual |
| `IMAGE_QUALITY` | `low` | Calidad de imagen |
| `IMAGE_SIZE` | `1024x1024` | Tamaño verificado en pricing |
| `OPENAI_TIMEOUT_TEXT` | `60` | Timeout de texto, en segundos |
| `OPENAI_TIMEOUT_IMAGE` | `120` | Timeout de imagen, en segundos |
| `DATABASE_URL` | `sqlite:///outfit.db` | Conexión SQLAlchemy |

`app/config.py` carga y valida estas variables de forma tipada. Una API key ausente,
un timeout no positivo, modelos de texto iguales o una combinación de imagen no
verificada impiden arrancar con un error explícito.

## Base de datos y migraciones

Para una base nueva:

```bash
python -m alembic upgrade head
```

Las bases creadas antes de incorporar Alembic ya tienen el esquema inicial. Después de
comprobar que proceden de esta versión del proyecto, se adoptan una sola vez sin cambiar
sus tablas ni datos:

```bash
python -m alembic stamp 20260716_0001
python -m alembic upgrade head
```

`python -m alembic current` muestra la revisión aplicada. La aplicación ya no crea
tablas silenciosamente al arrancar: cada cambio de esquema debe incluir su migración.
Alembic no necesita una API key de OpenAI. En SQLite, cada conexión activa
`PRAGMA foreign_keys=ON`; borrar un outfit en base de datos elimina en cascada sus
imágenes, vistas puestas, búsquedas de producto y reservas asociadas. El endpoint de
borrado confirma primero la transacción de base de datos y después retira, de forma
tolerante a archivos ausentes, solo los PNG que pertenecen a `app/generated/`.
Las revisiones `20260809_0006` y `20260809_0007` crean los usuarios, guardan el hash del
administrador local y asignan a este los outfits históricos.

## Ejecutar la API

```bash
fastapi dev app/main.py
```

Alternativamente:

```bash
uvicorn app.main:app --reload
```

Comprobación local:

```bash
curl http://127.0.0.1:8000/health
```

La documentación OpenAPI queda disponible en `http://127.0.0.1:8000/docs`.
La primera entrada se realiza con `admin` / `test`; también se puede crear una cuenta
normal desde el frontend. Estas credenciales son deliberadamente locales y deben
cambiarse antes de compartir la aplicación.

## Endpoints

| Método y ruta | Función | Respuestas relevantes |
|---|---|---|
| `GET /health` | Estado del servicio | `200` |
| `POST /auth/register` | Crea una cuenta normal e inicia su sesión | `201`, `409`, `422` |
| `POST /auth/login` | Inicia una sesión existente | `200`, `401`, `422` |
| `POST /auth/logout` | Cierra la sesión actual | `204` |
| `GET /auth/me` | Recupera el usuario de la sesión | `200`, `401` |
| `GET /outfits` | Lista outfits completos; pagina con `offset` y filtra con `favourites_only` | `200`, `422` |
| `PATCH /outfits/{id}` | Marca la portada elegida o el favorito, sin llamar al proveedor | `200`, `404`, `409`, `422` |
| `GET /outfits/{id}` | Recupera un outfit completo para continuarlo | `200`, `404` |
| `POST /outfits/{id}/items/{item_index}/product-search` | Busca y cachea hasta tres productos para una prenda | `product_search_ready`, `404`, `409`, `422`, `502`, `503` |
| `DELETE /outfits/{id}` | Elimina el outfit, sus dependencias y PNG locales referenciados | `204`, `404`, `409` |
| `POST /outfits/generate` | Analiza y persiste; genera solo si `generate_image=true` | `completed`, `needs_clarification`, `422`, `502`, `503` |
| `POST /outfits/{id}/regenerate` | Crea la primera imagen pendiente o una variación, sin repetir texto | `regenerated`, `regeneration_limit_reached`, `404`, `409`, `502` |
| `POST /outfits/{id}/images/{image_id}/worn-view` | Crea o recupera la vista puesta de ese flat-lay, sin repetir texto | `worn_view_ready`, `404`, `409`, `502` |
| `POST /outfits/proposals` | Propone tres outfits para una situación, sin crear ningún outfit | `proposals_ready`, `needs_clarification`, `409`, `422`, `502`, `503` |
| `GET /outfits/proposals/{id}` | Recupera un conjunto ya pagado para continuarlo | `200`, `404`, `409` |
| `POST /outfits/proposals/{id}/choose` | Promociona una propuesta a outfit, sin llamar al proveedor | `completed`, `404`, `409`, `422` |
| `GET /images/{filename}` | Sirve un PNG generado | `200`, `404` |

Ejemplo mínimo con una cookie de sesión local:

```bash
curl -c /tmp/outfit-cookie -X POST http://127.0.0.1:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"test"}'

curl -b /tmp/outfit-cookie -X POST http://127.0.0.1:8000/outfits/generate \
  -H 'Content-Type: application/json' \
  -d '{"user_description":"chaqueta negra y vaqueros azules","generate_image":false}'
```

La respuesta `completed` incluye `outfit_id`, el `user_description` original copiado por
la aplicación, las prendas detectadas, `image_prompt` y
`flat_lay_estimated_cost`. El coste se calcula en el backend con la calidad y el tamaño
activos, de modo que el frontend no replica tarifas. Cada prenda exige una
`visual_phrase_en` no vacía antes de poder construir un prompt visual. Las relaciones
explícitas se devuelven como `styling_notes_en`, aparecen en la revisión y pueden anular
solo la separación o zona necesarias; no habilitan solapes genéricos.
Tras revisarlo, `POST /outfits/{id}/regenerate` genera la primera imagen con ese prompt
exacto. Solo las llamadas posteriores añaden la directiva de composición alternativa.
Cada imagen devuelve también `image_id` y, cuando el análisis guardado es válido, un
`worn_view_preview` con el prompt exacto y una estimación previa de `$0.015`. El endpoint
de vista puesta es idempotente: tras un éxito devuelve la fila existente sin una nueva
llamada de pago.

El listado y el detalle usan la misma representación: descripción, extracción, prompt
visual, modelo de texto, coste actual del flat-lay, accesorios omitidos, imágenes en
orden de generación, vista puesta opcional, búsquedas por prenda, contador y
regeneraciones restantes. El frontend construye todo su archivo desde `GET /outfits`;
la antigua ruta plana de galería se retiró para evitar dos fuentes de verdad. Son
lecturas locales y nunca repiten una llamada de texto, imagen o búsqueda.
`generation_prompt` puede ser `null` en filas históricas; no se reconstruye ni se
inventa ese dato.
Todas las rutas de outfits y de imágenes requieren sesión. El listado de una cuenta
normal filtra por `owner_id`; para una referencia ajena, detalle, mutaciones e imagen
responden como recurso inexistente y nunca llegan al proveedor. El administrador no
aplica ese filtro para poder mantener la vista de desarrollo global.

La ruta de búsqueda recibe un único `additional_details` opcional, con un máximo de 200
caracteres. Ese texto puede contener una marca concreta, color, material, corte o
cualquier otro matiz. Antes de pagar el backend lo combina únicamente con la marca,
tipo, color, material, corte y detalles persistidos. Una marca ya interpretada no tiene
que repetirse; una marca reconocida escrita en el campo, como `Versace`, pasa a ser el
requisito de la búsqueda. Una prenda genérica o sin atributo diferenciador devuelve
`422`; no intenta completar información por su cuenta. Cada llamada usa `gpt-5.4-nano`, entre una y dos
acciones web dentro de una única petición Responses, mercado España, hasta tres
candidatos, un margen de 4.000 tokens de salida y `max_retries=0`. Sin marca usa los diez
dominios conocidos. Con marca excluye tiendas de otras marcas, conserva los distribuidores
multimarca y añade primero el dominio oficial cuando está reconocido; `Versace` usa
`versace.com`, Zalando y El Corte Inglés. La ruta exige un
`status=completed` antes de interpretar el resultado. Una respuesta `incomplete` se
distingue de una estructura inválida, ambas liberan la reserva y solo permiten otro
intento mediante una acción explícita del usuario. El log conserva únicamente un
diagnóstico técnico —identificador, estado, motivo, tipos de salida y uso—, nunca el
contenido bruto. Solo se aceptan URLs presentes en las fuentes. Un resultado vacío
también se persiste; repetir la misma prenda devuelve la fila existente con
`created=false` y coste nuevo cero. El precio se conserva como texto bruto cuando aparece;
la disponibilidad no se consulta ni se garantiza. El prompt exige conservar el tipo de
prenda y, cuando existe, la marca. El backend descarta además cualquier candidato
multimarca cuyo título o URL no conserve esa marca. Color, material, corte y detalles
ordenan las alternativas dentro de la misma marca; si no existe ningún producto válido se
persiste una lista vacía, nunca una sustitución silenciosa. El detalle adicional puede
afinar también una consulta que ya era válida; no añade otra petición Responses.

El borrado es local, explícito e irreversible. Devuelve `204` sin cuerpo al completarse,
`404` si el outfit ya no existe y `409` si comparte el outfit con una operación pagada
activa. La cascada elimina imágenes, vistas puestas, búsquedas y reservas en la misma
transacción. Después se intentan retirar sus PNG gestionados; un archivo ya ausente no
convierte el borrado en error y una ruta externa al directorio generado se ignora.

## Pruebas

```bash
python scripts/check.py
```

Este es el comando canónico local y de CI: ejecuta Ruff (análisis e imports), comprueba
el formato y después lanza toda la suite de pytest. Para iterar sobre un único módulo se
puede usar `python -m pytest -q tests/<test_file>.py`, pero el cierre exige el comando
completo.

Estado verificado el 2026-08-12: **232 tests en verde**, incluidas carreras con dos
sesiones SQLite, el flujo de revisión previa, autenticación, ownership y migraciones
sobre datos históricos.
La continuidad cubre listado, detalle, análisis pendientes, composiciones anidadas,
vistas emparejadas y el contrato JSON compartido con React. La vista puesta queda
cubierta con mocks en éxito, idempotencia,
concurrencia, ausencia de referencia y fallo del proveedor. La suite comprueba también
la activación de claves foráneas, la cascada desde outfits, el bloqueo del borrado
concurrente, la caché de búsquedas normales o vacías, la allowlist de fuentes y que solo
se retiren los PNG gestionados, sin realizar llamadas reales.

Los tests usan mocks y no llaman a OpenAI. Las pruebas visuales reales se ejecutan de
forma explícita mediante el runner experimental y sí pueden generar coste:

```bash
# Smoke real: una imagen por caso seleccionado
venv/bin/python -m experiments.run_experiment --limit 5

# A/B complejo: dos imágenes por caso
venv/bin/python -m experiments.run_experiment --ab-complex

# Regenerar casos guardados sin repetir el modelo de texto
venv/bin/python -m experiments.run_experiment \
  --reuse-extractions experiments/output/<run_origen> \
  --case-ids C08,C10

# Preparar gratis el A/B de vista vestida (0 llamadas OpenAI)
venv/bin/python -m experiments.run_experiment \
  --ab-worn-view experiments/output/20260715_192645_wide_refinement \
  --case-id C10 \
  --dry-run

# Preparar gratis el smoke de búsqueda de productos (0 llamadas OpenAI)
venv/bin/python -m experiments.product_search_experiment \
  --database outfit.db \
  --selection 5:0,1,2,3,4 \
  --batch \
  --dry-run
```

El A/B vestido reutiliza la extracción y el flat-lay de C10. La ejecución real usa el
mismo comando sin `--dry-run`: hace como máximo 2 llamadas de imagen (1 generación
desde texto y 1 edición con referencia), 0 de texto y 0 reintentos automáticos; si la
primera falla, se detiene. Solo se ejecuta después de presentar de nuevo su presupuesto
y recibir aprobación expresa.

El dry-run de búsqueda de productos abre SQLite en modo solo lectura y no crea el cliente
OpenAI. Con `--batch` agrupa las cinco prendas persistidas del outfit 5 en un request
revisable, mantiene los resultados separados por `item_id` y fija `max_tool_calls=1`.
Una futura ejecución real exige `--execute`, exactamente 1 llamada aprobada y el
presupuesto indicado; esos flags no sustituyen la aprobación expresa en la tarea activa.
El smoke usa 0 llamadas de extracción, 0 de imagen, como máximo 1 búsqueda web,
0 reintentos y un techo absoluto calculado y redondeado de `$0.04`. Este modo se conserva
para reproducir la comparación, pero no es la estrategia elegida para producto.

Sin `--batch`, el runner aplica el límite actual por prenda: una petición Responses puede
usar hasta dos acciones web, muestra un techo de `$0.03` por selección y exige que la
aprobación declare ese máximo completo. No ejecuta una segunda petición ni reintenta.

La línea base pagada por prenda ya se midió sobre un pantalón palazzo beige: 1 búsqueda,
5 candidatos, 4 coincidencias semánticas razonables, 1 falso positivo, 0 miniaturas,
14,146 s y coste calculado desde `usage` de `$0.012374`. Por ahora la disponibilidad no
participa en la valoración.

La comparación agrupada costó `$0.012301` y tardó 6,667 s, pero solo encontró candidato
para 1 de las 5 prendas. El ahorro de `$0.000073` no compensa perder cuatro prendas. La
ruta de producto busca únicamente la prenda elegida, una por llamada, y reutiliza su
resultado persistido; «buscar outfit completo» sigue aplazado como posible lote explícito
con coste previo, no como una acción web única.

Resultado cerrado el 2026-07-20: la edición con referencia conservó claramente mejor
la identidad del outfit y fue aceptada para el producto. Costó `$0.014252` frente a
`$0.005955` de la variante solo texto; no hubo errores ni reintentos. La funcionalidad
está implementada como una segunda acción opcional, nunca junto con el board automático.
El smoke de la ruta de producto se completó y aceptó el 2026-07-22: 0 llamadas de texto,
1 `images.edit`, 0 reintentos, coste `$0.014137`, persistencia 1:1, archivo e idempotencia
verificadas. La segunda petición devolvió la fila existente sin coste nuevo.

GitHub Actions ejecuta este mismo `python scripts/check.py` en Python 3.12 y
`npm run check` en el frontend para cada push y pull request. El workflow no recibe
secretos ni ejecuta pruebas reales contra OpenAI, por lo que su coste de API es cero.

## Datos locales y Git

- `.env`, `outfit.db`, `app/generated/` y `experiments/output/` están ignorados por Git.
- Los resultados cuantitativos de los experimentos se conservan en `pricing.md`.
- Las imágenes y CSV brutos permanecen locales; para publicarlos habrá que seleccionar
  y comprimir evidencia o usar almacenamiento externo.

## Documentación

- [`contexto_proyecto.md`](./contexto_proyecto.md): arquitectura, contrato actual y
  roadmap vigente.
- [`pricing.md`](./pricing.md): tarifas, costes y resultados reales.
- [`OPTIMIZACION_PROMPTS_PRICING.md`](./OPTIMIZACION_PROMPTS_PRICING.md): decisiones y
  experimentos de composición.
- [`AUDITORIA_TECNICA.md`](./AUDITORIA_TECNICA.md): fotografía histórica de riesgos y
  registro de su resolución.

## Prioridades actuales

- **P0 cerrado — smoke de vista puesta:** ruta real, fidelidad visual, coste, persistencia,
  archivo e idempotencia aceptados.
- **P1 cerrado — integridad y claridad:** claves foráneas/cascadas SQLite, coste previo de
  composición y flujo de tres etapas con esperas y reintentos explícitos.
- **P2 cerrado — continuidad persistente:** listado y detalle de outfits, recuperación
  al recargar y continuidad desde análisis o composiciones antiguas, con validación
  manual confirmada.
- **P3 validado — archivo como espacio de trabajo:** agrupación por outfit con una
  portada visual estable, detalle grande sin duplicar una composición única, selector
  compacto de variaciones, comparación directa, generación de vista histórica y
  descargas diferenciadas. El logo inicia una descripción nueva de forma segura. Usa
  solo `GET /outfits`; la ruta plana anterior ya no existe.
- **P4A cerrada — mantenimiento local seguro:** borrado explícito y coherente de BD/PNG,
  confirmación previa y limpieza del estado activo, validados manualmente. Selección,
  filtros y limpieza automática siguen sujetos al uso.
- **P5.1 cerrada — descubrimiento de productos:** el archivo permite lanzar y
  reabrir una búsqueda textual individual por prenda, con coste previo, fuentes
  permitidas, caché persistente e idempotencia. La interfaz está validada manualmente.
  El primer smoke real detectó `sources: null`; tras corregirlo, una única repetición
  devolvió y persistió tres candidatos con un coste calculado de `$0.012203`.
- **P8 en validación — cuentas locales y vistas por rol:** registro e inicio de sesión
  simples, contraseñas Argon2, outfits e imágenes separados por propietario y cuenta
  administradora con acceso global. El frontend normal oculta prompts, modelos,
  consultas internas y costes; el administrador conserva la vista técnica.

Cuotas por usuario, recuperación de contraseña, administración avanzada, publicación,
PostgreSQL, storage externo, colas, router y estado global permanecen aplazados mientras
la aplicación sea personal y local.

El detalle y los criterios de cierre de cada prioridad están en
[`contexto_proyecto.md`](./contexto_proyecto.md#roadmap-priorizado-actual).
